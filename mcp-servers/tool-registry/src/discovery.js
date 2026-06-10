// U2: MCP server enumeration.
//
// Reads mcp.json and the mcpServers block of claude-settings.json, spawns each
// declared server in parallel with a per-server timeout and a global discovery
// deadline, calls tools/list over stdio, and returns one manifest entry per
// tool. Per-server failures are recorded in `errors`, never thrown.

import fs from "node:fs/promises";
import path from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const PER_SERVER_TIMEOUT_MS = 10_000;
const GLOBAL_DEADLINE_MS = 15_000;

export async function readMcpJson(projectRoot) {
  const file = path.join(projectRoot, "mcp.json");
  try {
    const buf = await fs.readFile(file, "utf-8");
    const parsed = JSON.parse(buf);
    return parsed?.mcpServers || {};
  } catch {
    return {};
  }
}

export async function readSettingsServers(projectRoot) {
  const file = path.join(projectRoot, "claude-settings.json");
  try {
    const buf = await fs.readFile(file, "utf-8");
    const parsed = JSON.parse(buf);
    return parsed?.mcpServers || {};
  } catch {
    return {};
  }
}

export function mergeServers(repoServers, settingsServers) {
  // claude-settings.json wins on collision (mirrors Claude Code's own merge).
  return { ...repoServers, ...settingsServers };
}

function withTimeout(promise, ms, label) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(`${label}: timeout after ${ms}ms`)), ms);
    promise
      .then((v) => {
        clearTimeout(t);
        resolve(v);
      })
      .catch((e) => {
        clearTimeout(t);
        reject(e);
      });
  });
}

function expandEnv(value) {
  if (typeof value !== "string") return value;
  // ${VAR} substitution; if VAR is unset, leave the literal placeholder
  // so the downstream handshake surfaces the auth error explicitly.
  return value.replace(/\$\{([A-Z0-9_]+)\}/g, (_, name) => process.env[name] ?? `\${${name}}`);
}

function expandEnvAll(obj) {
  if (!obj) return obj;
  const result = {};
  for (const [k, v] of Object.entries(obj)) {
    result[k] = expandEnv(v);
  }
  return result;
}

async function probeServer(name, def) {
  // HTTP-transport servers are not stdio-spawned; skip them with a marker.
  if (def?.type === "http" || def?.url) {
    return {
      name,
      tools: [],
      error: { kind: "skipped", detail: "HTTP transport not enumerable via stdio" },
    };
  }

  if (!def?.command) {
    return {
      name,
      tools: [],
      error: { kind: "invalid", detail: "no command defined" },
    };
  }

  const transport = new StdioClientTransport({
    command: expandEnv(def.command),
    args: (def.args || []).map(expandEnv),
    env: { ...process.env, ...expandEnvAll(def.env || {}) },
  });
  const client = new Client(
    { name: "tool-registry-discovery", version: "0.1.0" },
    { capabilities: {} },
  );
  try {
    await withTimeout(client.connect(transport), PER_SERVER_TIMEOUT_MS, `${name} connect`);
    const list = await withTimeout(
      client.listTools(),
      PER_SERVER_TIMEOUT_MS,
      `${name} tools/list`,
    );
    return { name, tools: list?.tools || [], error: null };
  } catch (err) {
    return { name, tools: [], error: { kind: "handshake", detail: err.message } };
  } finally {
    try {
      await client.close();
    } catch {
      // ignore
    }
  }
}

export async function discoverMcpTools(projectRoot) {
  const repo = await readMcpJson(projectRoot);
  const settings = await readSettingsServers(projectRoot);
  const merged = mergeServers(repo, settings);

  const serverNames = Object.keys(merged);
  // Spawn all in parallel; honor a global deadline so a single slow server
  // can't blow the SessionStart budget (U10).
  const probes = serverNames.map((n) => probeServer(n, merged[n]));
  const settled = await Promise.race([
    Promise.allSettled(probes),
    new Promise((resolve) =>
      setTimeout(() => resolve("__deadline__"), GLOBAL_DEADLINE_MS),
    ),
  ]);

  let results;
  if (settled === "__deadline__") {
    // Build placeholder results for any not-yet-resolved probe.
    results = await Promise.all(
      probes.map((p) =>
        Promise.race([p, Promise.resolve({ __timeout__: true })]),
      ),
    );
  } else {
    results = settled.map((s) =>
      s.status === "fulfilled"
        ? s.value
        : { name: "?", tools: [], error: { kind: "throw", detail: String(s.reason) } },
    );
  }

  const tools = [];
  const errors = [];
  for (let i = 0; i < serverNames.length; i++) {
    const serverName = serverNames[i];
    const r = results[i];
    if (r?.__timeout__) {
      errors.push({ server: serverName, kind: "timeout", detail: "global deadline" });
      // No tools to emit — server's tools simply absent until next refresh.
      continue;
    }
    if (r?.error) {
      errors.push({ server: serverName, ...r.error });
      // Emit a synthetic placeholder so the consumer can see the server tried.
      continue;
    }
    for (const tool of r.tools) {
      tools.push({
        name: `mcp__${serverName}__${tool.name}`,
        source: { kind: "mcp", server: serverName, tool: tool.name },
        schema: tool.inputSchema || null,
        description: tool.description || null,
        category: [],
        capability_tags: [],
        prefer_over: {},
        compose_with: [],
        health: {
          state: "healthy",
          checked_at: new Date().toISOString(),
          detail: `tools/list returned ${r.tools.length} tools`,
        },
      });
    }
    // Empty tools/list response → mark via a sentinel error so health resolution
    // (U5) can flip the state. discoverMcpTools is intentionally additive here.
    if (r.tools.length === 0) {
      errors.push({
        server: serverName,
        kind: "empty",
        detail: "tools/list returned empty",
      });
    }
  }

  return { tools, errors };
}
