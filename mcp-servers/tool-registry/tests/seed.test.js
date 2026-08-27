// U4: annotation overlay lint — every prefer_over reference is a real tool
// name (either elsewhere in the overlay or in hooks/tools/cli-tools.yaml).

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { parseSimpleYaml } from "../src/yaml_lite.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");

test("U4: annotations.yaml parses as YAML", async () => {
  const text = await fs.readFile(
    path.join(REPO_ROOT, "hooks", "tools", "annotations.yaml"),
    "utf-8",
  );
  const parsed = parseSimpleYaml(text);
  assert.ok(parsed && typeof parsed === "object");
  assert.ok(parsed["mcp__fff__grep"], "seed entry present");
});

test("U4: every prefer_over reference resolves to a known tool name", async () => {
  const annText = await fs.readFile(
    path.join(REPO_ROOT, "hooks", "tools", "annotations.yaml"),
    "utf-8",
  );
  const cliText = await fs.readFile(
    path.join(REPO_ROOT, "hooks", "tools", "cli-tools.yaml"),
    "utf-8",
  );
  const ann = parseSimpleYaml(annText);
  const cli = parseSimpleYaml(cliText);
  const known = new Set([
    ...Object.keys(ann),
    ...cli, // top-level list of names
  ]);

  for (const [toolName, entry] of Object.entries(ann)) {
    const prefer = entry?.prefer_over || {};
    for (const [cat, chain] of Object.entries(prefer)) {
      for (const ref of chain) {
        assert.ok(
          known.has(ref),
          `${toolName}.prefer_over.${cat} references unknown tool "${ref}"`,
        );
      }
    }
  }
});

test("U4: prototype-slice tools all have a category assigned", async () => {
  const annText = await fs.readFile(
    path.join(REPO_ROOT, "hooks", "tools", "annotations.yaml"),
    "utf-8",
  );
  const ann = parseSimpleYaml(annText);
  const prototypeSlice = [
    "mcp__fff__grep",
    "mcp__fff__find_files",
    "ast-grep",
    "rg",
    "fd",
    "grep",
    "find",
    "mcp__turbo-rag__semantic_search",
  ];
  for (const name of prototypeSlice) {
    const entry = ann[name];
    assert.ok(entry, `${name} missing from overlay`);
    assert.ok(
      Array.isArray(entry.category) && entry.category.length > 0,
      `${name} has no category`,
    );
  }
});

test("U4: search-content chain matches AGENTS.md canonical order", async () => {
  const annText = await fs.readFile(
    path.join(REPO_ROOT, "hooks", "tools", "annotations.yaml"),
    "utf-8",
  );
  const ann = parseSimpleYaml(annText);
  // The head of the chain prefers over the rest in order. ast-grep is
  // deliberately absent: it is a different KIND of search (syntax structure,
  // not text), so it belongs to no tier of this chain. It ranks first in its
  // own ast-search category instead.
  assert.deepEqual(ann["mcp__fff__grep"].prefer_over["search-content"], ["rg", "grep"]);
  assert.deepEqual(ann["mcp__fff__multi_grep"].prefer_over["search-content"], ["rg", "grep"]);
  for (const head of ["mcp__fff__grep", "mcp__fff__multi_grep"]) {
    assert.ok(
      !ann[head].prefer_over["search-content"].includes("ast-grep"),
      `${head} must not demote ast-grep — a structural query is not a worse text search`,
    );
  }
  assert.deepEqual(ann["ast-grep"].prefer_over["ast-search"], []);
});
