#!/usr/bin/env node
// Entry point for the tool-registry MCP server.
//
// Usage:
//   node index.js <project-root>            # run as MCP server on stdio
//   node index.js <project-root> --refresh-and-exit  # rebuild cache and exit (SessionStart hook)
//
// The project root is resolved in this order:
//   1. First non-flag argv after the script path
//   2. MCP_PROJECT_ROOT environment variable
//   3. Hard error — never falls back to process.cwd() (Claude Code spawns MCP
//      servers from arbitrary working directories per KTD3).

import path from "node:path";
import fs from "node:fs/promises";
import { startServer } from "./src/server.js";
import { buildAndWriteManifest } from "./src/manifest.js";

function parseArgs(argv) {
  const args = argv.slice(2);
  const flags = new Set();
  const positionals = [];
  for (const arg of args) {
    if (arg.startsWith("--")) flags.add(arg);
    else positionals.push(arg);
  }
  return { positionals, flags };
}

function resolveProjectRoot(positionals) {
  if (positionals.length > 0) return path.resolve(positionals[0]);
  if (process.env.MCP_PROJECT_ROOT) return path.resolve(process.env.MCP_PROJECT_ROOT);
  console.error(
    "Error: project root not provided. Pass it as the first positional " +
      "argument or set MCP_PROJECT_ROOT. The tool-registry server never " +
      "falls back to process.cwd().",
  );
  process.exit(1);
}

async function assertProjectRoot(root) {
  try {
    const stats = await fs.stat(root);
    if (!stats.isDirectory()) {
      console.error(`Error: project root is not a directory: ${root}`);
      process.exit(1);
    }
  } catch (err) {
    console.error(`Error: cannot access project root ${root}: ${err.message}`);
    process.exit(1);
  }
}

async function main() {
  const { positionals, flags } = parseArgs(process.argv);
  const projectRoot = resolveProjectRoot(positionals);
  await assertProjectRoot(projectRoot);

  if (flags.has("--refresh-and-exit")) {
    try {
      const summary = await buildAndWriteManifest(projectRoot);
      console.error(
        `tool-registry: refreshed manifest (tools=${summary.tools_count}, ` +
          `healthy=${summary.healthy_count}, errors=${summary.errors_count})`,
      );
      process.exit(0);
    } catch (err) {
      console.error(`tool-registry: refresh failed: ${err.message}`);
      // Exit 0 so SessionStart never blocks; downstream falls back gracefully.
      process.exit(0);
    }
  }

  await startServer(projectRoot);
}

main().catch((err) => {
  console.error(`Fatal error: ${err.message}`);
  process.exit(1);
});
