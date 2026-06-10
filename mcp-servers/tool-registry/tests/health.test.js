// U5: health resolution tests.

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

import { resolveCliHealth, reprobeTool } from "../src/health.js";
import { buildAndWriteManifest, readManifestOrEmpty } from "../src/manifest.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const STUB = path.join(__dirname, "fixtures", "stub-mcp-server.mjs");

async function mkProject(opts) {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "tool-registry-h-"));
  if (opts.mcp) await fs.writeFile(path.join(tmp, "mcp.json"), opts.mcp);
  if (opts.cli) {
    await fs.mkdir(path.join(tmp, "hooks", "tools"), { recursive: true });
    await fs.writeFile(path.join(tmp, "hooks", "tools", "cli-tools.yaml"), opts.cli);
  }
  return tmp;
}

async function withFakeHome(fn) {
  const savedHome = process.env.HOME;
  const fakeHome = await fs.mkdtemp(path.join(os.tmpdir(), "tool-registry-fhome-"));
  process.env.HOME = fakeHome;
  try {
    await fn(fakeHome);
  } finally {
    process.env.HOME = savedHome;
    await fs.rm(fakeHome, { recursive: true, force: true });
  }
}

test("U5: healthy CLI yields state=healthy with binary detail", async () => {
  const out = await resolveCliHealth({ source: { kind: "cli", binary: "/usr/bin/ls" } });
  assert.equal(out.state, "healthy");
  assert.ok(out.detail.includes("/usr/bin/ls"));
});

test("U5: unhealthy CLI yields state=unhealthy with explanatory detail", async () => {
  const out = await resolveCliHealth({ source: { kind: "cli", binary: null } });
  assert.equal(out.state, "unhealthy");
  assert.match(out.detail, /command -v/);
});

test("U5: healthy MCP server propagates healthy to all of its tools", async () => {
  const proj = await mkProject({
    mcp: JSON.stringify({
      mcpServers: {
        stub: { command: "node", args: [STUB], env: { TOOL_NAMES: "a,b" } },
      },
    }),
    cli: "",
  });
  await withFakeHome(async () => {
    await buildAndWriteManifest(proj);
    const m = await readManifestOrEmpty(proj);
    assert.equal(m.tools["mcp__stub__a"].health.state, "healthy");
    assert.equal(m.tools["mcp__stub__b"].health.state, "healthy");
  });
  await fs.rm(proj, { recursive: true, force: true });
});

test("U5: tool_health re-probe of CLI that doesn't exist returns unhealthy", async () => {
  const proj = await mkProject({
    cli: "- definitely_not_a_real_xyz123\n",
  });
  await withFakeHome(async () => {
    await buildAndWriteManifest(proj);
    const updated = await reprobeTool("definitely_not_a_real_xyz123", proj);
    assert.equal(updated.health.state, "unhealthy");
  });
  await fs.rm(proj, { recursive: true, force: true });
});

test("U5: tool_health on unknown name returns state=unknown", async () => {
  const proj = await mkProject({ cli: "- ls\n" });
  await withFakeHome(async () => {
    await buildAndWriteManifest(proj);
    const updated = await reprobeTool("phantom_tool", proj);
    assert.equal(updated.health.state, "unknown");
  });
  await fs.rm(proj, { recursive: true, force: true });
});

test("U5: empty tools/list MCP server → discovery_errors records empty, tools absent", async () => {
  const proj = await mkProject({
    mcp: JSON.stringify({
      mcpServers: {
        emptyStub: { command: "node", args: [STUB], env: { SERVER_BEHAVIOR: "empty" } },
      },
    }),
    cli: "",
  });
  await withFakeHome(async () => {
    await buildAndWriteManifest(proj);
    const m = await readManifestOrEmpty(proj);
    // No tools surface (since tools/list was empty).
    assert.equal(Object.keys(m.tools).length, 0);
    assert.ok(
      m.discovery_errors.some((e) => e.server === "emptyStub" && e.kind === "empty"),
      "discovery_errors records the empty tools/list",
    );
  });
  await fs.rm(proj, { recursive: true, force: true });
});
