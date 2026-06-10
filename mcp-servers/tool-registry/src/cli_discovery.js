// U3: CLI discovery + overlay merge.
//
// Reads hooks/tools/cli-tools.yaml (a flat YAML list of tool names), probes
// each via `command -v`, and produces manifest entries. Also loads
// hooks/tools/annotations.yaml and exposes the overlay merge helper.

import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";

import { parseSimpleYaml } from "./yaml_lite.js";

// Fields the overlay may override (per KTD2).
const OVERRIDABLE_FIELDS = [
  "description",
  "category",
  "capability_tags",
  "prefer_over",
  "fallback_to",
  "compose_with",
];

export async function loadCliToolList(projectRoot) {
  const file = path.join(projectRoot, "hooks", "tools", "cli-tools.yaml");
  try {
    const buf = await fs.readFile(file, "utf-8");
    const parsed = parseSimpleYaml(buf);
    if (Array.isArray(parsed)) return parsed.map(String);
    if (parsed && Array.isArray(parsed.tools)) return parsed.tools.map(String);
    return [];
  } catch {
    return [];
  }
}

export async function loadAnnotations(projectRoot) {
  const file = path.join(projectRoot, "hooks", "tools", "annotations.yaml");
  try {
    const buf = await fs.readFile(file, "utf-8");
    const parsed = parseSimpleYaml(buf);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
    return {};
  } catch (err) {
    if (err.code === "ENOENT") return {};
    // Malformed YAML — surface the error per U3 test scenario.
    throw new Error(`annotations.yaml parse error: ${err.message}`);
  }
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

export async function discoverCliTools(projectRoot) {
  const names = await loadCliToolList(projectRoot);
  const tools = [];
  for (const name of names) {
    // `git-status` is a verb composite, not a binary — synthesize its source.
    const probeName = name === "git-status" ? "git" : name;
    const binary = await commandV(probeName);
    tools.push({
      name,
      source: { kind: "cli", binary: binary || null },
      schema: null,
      description: null,
      category: [],
      capability_tags: [],
      prefer_over: {},
      compose_with: [],
      // health filled in by manifest assembly via resolveCliHealth
    });
  }
  return tools;
}

export function applyOverlay(tool, overlayEntry) {
  if (!overlayEntry) return tool;
  const result = { ...tool };
  for (const field of OVERRIDABLE_FIELDS) {
    if (overlayEntry[field] !== undefined) {
      result[field] = overlayEntry[field];
    }
  }
  // Non-overrideable fields: name, source, schema, health are preserved.
  return result;
}

export { OVERRIDABLE_FIELDS };
