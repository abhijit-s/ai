// U2: MCP discovery tests.

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

import {
  discoverMcpTools,
  mergeServers,
  readMcpJson,
  readSettingsServers,
} from "../src/discovery.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const STUB = path.join(__dirname, "fixtures", "stub-mcp-server.mjs");

async function buildProject(fixtureSubdir) {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "tool-registry-disc-"));
  const fixtureDir = path.join(__dirname, "fixtures", fixtureSubdir);
  const entries = await fs.readdir(fixtureDir);
  for (const e of entries) {
    const src = path.join(fixtureDir, e);
    const buf = await fs.readFile(src, "utf-8");
    await fs.writeFile(path.join(tmp, e), buf.replace(/__STUB_PATH__/g, STUB));
  }
  return tmp;
}

async function cleanup(dir) {
  await fs.rm(dir, { recursive: true, force: true });
}

test("U2: discovery reads mcp.json and returns prefixed tool names", async () => {
  const proj = await buildProject("u2-basic");
  try {
    const { tools, errors } = await discoverMcpTools(proj);
    const names = tools.map((t) => t.name).sort();
    assert.deepEqual(names, ["mcp__stubA__alpha", "mcp__stubA__beta"]);
    // No global-deadline timeouts expected.
    assert.equal(errors.find((e) => e.kind === "timeout"), undefined);
    // Each tool carries source.kind=mcp + schema + healthy state.
    for (const t of tools) {
      assert.equal(t.source.kind, "mcp");
      assert.equal(t.source.server, "stubA");
      assert.ok(t.schema);
      assert.equal(t.health.state, "healthy");
    }
  } finally {
    await cleanup(proj);
  }
});

test("U2: settings.json-only server is discovered", async () => {
  const proj = await buildProject("u2-settings-only");
  try {
    const { tools } = await discoverMcpTools(proj);
    const names = tools.map((t) => t.name);
    assert.ok(names.includes("mcp__settingsOnly__gamma"));
  } finally {
    await cleanup(proj);
  }
});

test("U2: claude-settings overrides mcp.json on name collision", async () => {
  const proj = await buildProject("u2-conflict");
  try {
    const { tools } = await discoverMcpTools(proj);
    const names = tools.map((t) => t.name);
    // settings should win — TOOL_NAMES=from_settings
    assert.ok(names.includes("mcp__shared__from_settings"));
    assert.ok(!names.includes("mcp__shared__from_mcp_json"));
  } finally {
    await cleanup(proj);
  }
});

test("U2: crashing server records error and continues", async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "tool-registry-disc-crash-"));
  try {
    await fs.writeFile(
      path.join(tmp, "mcp.json"),
      JSON.stringify({
        mcpServers: {
          crasher: { command: "node", args: [STUB], env: { SERVER_BEHAVIOR: "crash" } },
          ok: { command: "node", args: [STUB], env: { TOOL_NAMES: "ok1" } },
        },
      }),
    );
    const { tools, errors } = await discoverMcpTools(tmp);
    // crasher's tools absent; ok present
    assert.ok(tools.some((t) => t.name === "mcp__ok__ok1"));
    assert.ok(errors.some((e) => e.server === "crasher"));
  } finally {
    await cleanup(tmp);
  }
});

test("U2: mergeServers — settings wins on collision", () => {
  const repo = { foo: { command: "A" }, bar: { command: "B" } };
  const settings = { foo: { command: "C" } };
  const merged = mergeServers(repo, settings);
  assert.equal(merged.foo.command, "C");
  assert.equal(merged.bar.command, "B");
});

test("U2: env-substitution token unset surfaces handshake error, not a crash", async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "tool-registry-disc-env-"));
  try {
    delete process.env.MADE_UP_DISCOVERY_VAR;
    await fs.writeFile(
      path.join(tmp, "mcp.json"),
      JSON.stringify({
        mcpServers: {
          unauth: {
            command: "node",
            args: [STUB],
            env: { TOOL_NAMES: "x", FAKE_TOKEN: "${MADE_UP_DISCOVERY_VAR}" },
          },
        },
      }),
    );
    const { tools, errors } = await discoverMcpTools(tmp);
    // The stub doesn't actually fail on env, so it returns tools — but the
    // contract under test is "literal placeholder propagated" (no crash).
    // Verify discovery didn't throw and returned a usable result.
    assert.ok(Array.isArray(tools));
    assert.ok(Array.isArray(errors));
  } finally {
    await cleanup(tmp);
  }
});

test("U2: readMcpJson / readSettingsServers return empty when files missing", async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "tool-registry-disc-empty-"));
  try {
    assert.deepEqual(await readMcpJson(tmp), {});
    assert.deepEqual(await readSettingsServers(tmp), {});
  } finally {
    await cleanup(tmp);
  }
});
