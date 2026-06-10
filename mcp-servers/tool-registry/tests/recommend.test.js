// U7: recommend_tool scoring tests.

import { test } from "node:test";
import assert from "node:assert/strict";

import { recommendTool } from "../src/recommend.js";

function manifestWithSearch() {
  return {
    schema_version: 1,
    tools: {
      "mcp__fff__grep": {
        category: ["search-content"],
        capability_tags: ["content-search", "frecency-ranked", "indexed"],
        prefer_over: { "search-content": ["ast-grep", "rg", "grep"] },
        health: { state: "healthy" },
      },
      "ast-grep": {
        category: ["search-content", "ast-search"],
        capability_tags: ["structural-search", "ast-aware"],
        prefer_over: { "search-content": ["rg", "grep"] },
        health: { state: "healthy" },
      },
      rg: {
        category: ["search-content"],
        capability_tags: ["content-search", "gitignore-aware"],
        prefer_over: { "search-content": ["grep"] },
        health: { state: "healthy" },
      },
      grep: {
        category: ["search-content"],
        capability_tags: ["content-search", "last-resort"],
        prefer_over: {},
        health: { state: "healthy" },
      },
    },
  };
}

test("U7: search-content intent → mcp__fff__grep when healthy", () => {
  const result = recommendTool(
    { category: "search-content" },
    manifestWithSearch(),
    null,
  );
  assert.equal(result, "mcp__fff__grep");
});

test("U7: search-content intent → ast-grep when fff is unhealthy", () => {
  const m = manifestWithSearch();
  m.tools["mcp__fff__grep"].health.state = "unhealthy";
  const result = recommendTool({ category: "search-content" }, m, null);
  assert.equal(result, "ast-grep");
});

test("U7: search-content intent restricted to documentation-refiner profile (rg only) → rg", () => {
  const allowed = new Set(["rg", "grep"]);
  const result = recommendTool({ category: "search-content" }, manifestWithSearch(), allowed);
  assert.equal(result, "rg"); // rg has a chain (rank=1), grep has none (rank=0)
});

test("U7: no allowed-and-healthy tool matches the intent → returns null", () => {
  const m = manifestWithSearch();
  for (const t of Object.values(m.tools)) t.health.state = "unhealthy";
  const result = recommendTool({ category: "search-content" }, m, null);
  assert.equal(result, null);
});

test("U7: capability tags affect scoring via jaccard", () => {
  const m = manifestWithSearch();
  // Intent with tags matching rg's gitignore-aware but not fff's frecency.
  // fff still wins because of higher rank_bonus (chain length 3 vs rg's 1).
  const result = recommendTool(
    { category: "search-content", tags: ["gitignore-aware"] },
    m,
    null,
  );
  assert.equal(result, "mcp__fff__grep");
});

test("U7: empty manifest → null", () => {
  const result = recommendTool(
    { category: "search-content" },
    { schema_version: 1, tools: {} },
    null,
  );
  assert.equal(result, null);
});
