// Manifest cache read/write + assembly. U3 adds CLI discovery + overlay merge;
// U2 provides MCP discovery; U1 ships with the cache I/O primitives so handler
// modules can import them.

import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";

import { discoverMcpTools } from "./discovery.js";
import { discoverCliTools, loadAnnotations, applyOverlay } from "./cli_discovery.js";
import { resolveCliHealth } from "./health.js";

export const SCHEMA_VERSION = 1;

export function manifestPath() {
  return path.join(os.homedir(), ".claude", "cache", "tool-registry-manifest.json");
}

export async function readManifestOrEmpty(_projectRoot) {
  try {
    const buf = await fs.readFile(manifestPath(), "utf-8");
    const parsed = JSON.parse(buf);
    if (parsed?.schema_version !== SCHEMA_VERSION) {
      // Schema mismatch — caller can treat as empty; full rebuild expected
      // on next SessionStart per KTD4.
      return emptyManifest();
    }
    return parsed;
  } catch {
    return emptyManifest();
  }
}

export function emptyManifest() {
  return {
    schema_version: SCHEMA_VERSION,
    generated_at: null,
    last_success: null,
    tools: {},
    discovery_errors: [],
  };
}

async function atomicWrite(filePath, contents) {
  const dir = path.dirname(filePath);
  await fs.mkdir(dir, { recursive: true });
  const tmp = `${filePath}.tmp`;
  // Open with explicit truncation, write all bytes, fsync, then rename.
  const handle = await fs.open(tmp, "w");
  try {
    await handle.writeFile(contents);
    await handle.sync();
  } finally {
    await handle.close();
  }
  await fs.rename(tmp, filePath);
}

export async function buildAndWriteManifest(projectRoot) {
  const { tools: mcpTools, errors: mcpErrors } = await discoverMcpTools(projectRoot);
  const cliTools = await discoverCliTools(projectRoot);
  const annotations = await loadAnnotations(projectRoot);

  // Apply CLI health (command -v results — already filled by discoverCliTools).
  const merged = {};
  for (const tool of mcpTools) merged[tool.name] = tool;
  for (const tool of cliTools) merged[tool.name] = tool;

  // Resolve CLI health states for any tools that arrived without one set.
  for (const name of Object.keys(merged)) {
    const t = merged[name];
    if (t.source?.kind === "cli" && !t.health) {
      merged[name] = { ...t, health: await resolveCliHealth(t) };
    }
  }

  // Apply overlay (KTD2 override rules).
  const withOverlay = {};
  for (const [name, tool] of Object.entries(merged)) {
    withOverlay[name] = applyOverlay(tool, annotations[name] || null);
  }

  const generatedAt = new Date().toISOString();
  const manifest = {
    schema_version: SCHEMA_VERSION,
    generated_at: generatedAt,
    last_success: generatedAt,
    tools: withOverlay,
    discovery_errors: mcpErrors,
  };

  await atomicWrite(manifestPath(), JSON.stringify(manifest, null, 2));

  const toolsList = Object.values(manifest.tools);
  return {
    tools_count: toolsList.length,
    healthy_count: toolsList.filter((t) => t?.health?.state === "healthy").length,
    errors_count: (mcpErrors || []).length,
  };
}
