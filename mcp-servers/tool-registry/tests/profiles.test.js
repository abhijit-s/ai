// U6: Node-side profile tests.

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

import { loadProfiles, resolveProfile, parseOverride, resolveOverride } from "../src/profiles.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");

const SAMPLE_MANIFEST = {
  schema_version: 1,
  tools: {
    "mcp__fff__grep": { category: ["search-content"], health: { state: "healthy" } },
    "mcp__fff__find_files": { category: ["find-files"], health: { state: "healthy" } },
    "ast-grep": { category: ["search-content", "ast-search"], health: { state: "healthy" } },
    rg: { category: ["search-content"], health: { state: "healthy" } },
    "mcp__turbo-rag__semantic_search": {
      category: ["semantic-search"],
      health: { state: "unhealthy" },
    },
  },
};

test("U6: loadProfiles reads the repo's hooks/profiles.json", async () => {
  const doc = await loadProfiles(REPO_ROOT);
  assert.equal(doc.version, 1);
  assert.ok(doc.profiles["code-explorer"]);
  assert.ok(doc.profiles["session-default"]);
  assert.ok(doc.profiles["default"]);
});

test("U6: resolveProfile returns category union", async () => {
  const doc = await loadProfiles(REPO_ROOT);
  const allowed = resolveProfile("code-explorer", doc, SAMPLE_MANIFEST);
  assert.ok(allowed.has("mcp__fff__grep"));
  assert.ok(allowed.has("ast-grep"));
  assert.ok(allowed.has("rg"));
  assert.ok(allowed.has("mcp__fff__find_files"));
});

test("U6: resolveProfile unknown name → empty set", async () => {
  const doc = await loadProfiles(REPO_ROOT);
  const allowed = resolveProfile("phantom", doc, SAMPLE_MANIFEST);
  assert.equal(allowed.size, 0);
});

test("U6: parseOverride matches multiline comment with newlines", () => {
  const tokens = parseOverride(
    "<!-- tools:\n  mcp__fff__grep,\n  find-files\n-->",
  );
  assert.deepEqual(tokens, ["mcp__fff__grep", "find-files"]);
});

test("U6: parseOverride first match wins", () => {
  const tokens = parseOverride(
    "<!-- tools: code-explorer --> ... <!-- tools: documentation-refiner -->",
  );
  assert.deepEqual(tokens, ["code-explorer"]);
});

test("U6: parseOverride coexists with inject: prefix in the same prompt", () => {
  const tokens = parseOverride(
    "<!-- inject: tool-hierarchy -->\n<!-- tools: code-explorer -->",
  );
  assert.deepEqual(tokens, ["code-explorer"]);
});

test("U6: resolveOverride single profile token routes to profile", async () => {
  const doc = await loadProfiles(REPO_ROOT);
  const allowed = resolveOverride(["documentation-refiner"], doc, SAMPLE_MANIFEST);
  assert.ok(allowed.has("mcp__fff__grep"));
  assert.ok(allowed.has("rg"));
  // documentation-refiner is search-content only
  assert.ok(!allowed.has("mcp__fff__find_files"));
});

test("U6: resolveOverride mixed tools + categories", async () => {
  const doc = await loadProfiles(REPO_ROOT);
  const allowed = resolveOverride(
    ["mcp__fff__grep", "find-files"],
    doc,
    SAMPLE_MANIFEST,
  );
  assert.deepEqual(
    [...allowed].sort(),
    ["mcp__fff__find_files", "mcp__fff__grep"].sort(),
  );
});

test("U6: loadProfiles missing file returns empty profile doc", async () => {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "tool-registry-pf-"));
  try {
    const doc = await loadProfiles(tmp);
    assert.deepEqual(doc, { version: 1, profiles: {} });
  } finally {
    await fs.rm(tmp, { recursive: true, force: true });
  }
});
