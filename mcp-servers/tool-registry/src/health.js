// U5: Health resolution.
//
// CLI tool health = whether command -v returned a path.
// MCP tool health = propagated from the parent server's tools/list handshake
//                   (set by discovery.js at build time).
// tool_health(name) re-probes a single tool/server on demand.

import { spawn } from "node:child_process";
import { readManifestOrEmpty, manifestPath } from "./manifest.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { readMcpJson, readSettingsServers, mergeServers } from "./discovery.js";
import fs from "node:fs/promises";

export async function resolveCliHealth(tool) {
  const checkedAt = new Date().toISOString();
  if (tool.source?.binary) {
    return { state: "healthy", checked_at: checkedAt, detail: `binary at ${tool.source.binary}` };
  }
  return { state: "unhealthy", checked_at: checkedAt, detail: "command -v returned no path" };
}

function commandV(name) {
  return new Promise((resolve) => {
    const child = spawn("/usr/bin/env", ["bash", "-lc", `command -v ${JSON.stringify(name)}`], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.on("close", (code) => {
      if (code === 0 && out.trim()) resolve(out.trim().split("\n")[0]);
      else resolve(null);
    });
    child.on("error", () => resolve(null));
  });
}

async function probeMcpServer(serverDef) {
  if (serverDef?.type === "http" || serverDef?.url) {
    return { state: "unknown", detail: "HTTP transport not probable via stdio" };
  }
  if (!serverDef?.command) {
    return { state: "unhealthy", detail: "no command defined" };
  }
  const transport = new StdioClientTransport({
    command: serverDef.command,
    args: serverDef.args || [],
    env: { ...process.env, ...(serverDef.env || {}) },
  });
  const client = new Client(
    { name: "tool-registry-reprobe", version: "0.1.0" },
    { capabilities: {} },
  );
  try {
    await client.connect(transport);
    const list = await client.listTools();
    const count = list?.tools?.length || 0;
    if (count === 0) {
      return { state: "unhealthy", detail: "tools/list returned empty" };
    }
    return { state: "healthy", detail: `tools/list returned ${count} tools` };
  } catch (err) {
    return { state: "unhealthy", detail: err.message };
  } finally {
    try {
      await client.close();
    } catch {
      // ignore
    }
  }
}

async function writeManifestEntry(updated) {
  // Atomic-update one entry in the existing cache.
  const file = manifestPath();
  let data;
  try {
    data = JSON.parse(await fs.readFile(file, "utf-8"));
  } catch {
    return; // no cache yet; nothing to update
  }
  data.tools = data.tools || {};
  data.tools[updated.name] = updated;
  const tmp = `${file}.tmp`;
  const handle = await fs.open(tmp, "w");
  try {
    await handle.writeFile(JSON.stringify(data, null, 2));
    await handle.sync();
  } finally {
    await handle.close();
  }
  await fs.rename(tmp, file);
}

export async function reprobeTool(name, projectRoot) {
  const manifest = await readManifestOrEmpty(projectRoot);
  const tool = manifest.tools?.[name];
  const checkedAt = new Date().toISOString();
  if (!tool) {
    return { name, health: { state: "unknown", checked_at: checkedAt, detail: "not in manifest" } };
  }
  if (tool.source?.kind === "cli") {
    const binary = await commandV(tool.source.binary ? tool.source.binary.split("/").pop() : name);
    const updated = {
      ...tool,
      source: { ...tool.source, binary: binary || null },
      health: binary
        ? { state: "healthy", checked_at: checkedAt, detail: `binary at ${binary}` }
        : { state: "unhealthy", checked_at: checkedAt, detail: "command -v returned no path" },
    };
    await writeManifestEntry(updated);
    return updated;
  }
  if (tool.source?.kind === "mcp") {
    const repo = await readMcpJson(projectRoot);
    const settings = await readSettingsServers(projectRoot);
    const merged = mergeServers(repo, settings);
    const serverDef = merged[tool.source.server];
    if (!serverDef) {
      const updated = {
        ...tool,
        health: { state: "unhealthy", checked_at: checkedAt, detail: "server definition missing" },
      };
      await writeManifestEntry(updated);
      return updated;
    }
    const probe = await probeMcpServer(serverDef);
    const updated = {
      ...tool,
      health: { ...probe, checked_at: checkedAt },
    };
    await writeManifestEntry(updated);
    return updated;
  }
  return tool;
}
