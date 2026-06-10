// U6: Profile loader (Node side). Parallel to hooks/lib/tool_registry_client.py
// for the Python hooks.

import fs from "node:fs/promises";
import path from "node:path";

export async function loadProfiles(projectRoot) {
  const file = path.join(projectRoot, "hooks", "profiles.json");
  try {
    const buf = await fs.readFile(file, "utf-8");
    return JSON.parse(buf);
  } catch {
    return { version: 1, profiles: {} };
  }
}

// resolveProfile returns the Set of allowed tool names for `name` against
// the provided manifest. Allowed = explicit `tools` ∪ tools whose `category`
// intersects the profile's `categories`. Unknown profile name → empty set.
export function resolveProfile(name, profilesDoc, manifest) {
  const profiles = profilesDoc?.profiles || {};
  const profile = profiles[name];
  if (!profile) return new Set();

  const explicit = new Set(profile.tools || []);
  const categories = new Set(profile.categories || []);

  const allowed = new Set(explicit);
  for (const [toolName, tool] of Object.entries(manifest?.tools || {})) {
    const cats = tool?.category || [];
    if (cats.some((c) => categories.has(c))) allowed.add(toolName);
  }
  return allowed;
}

// Parse `<!-- tools: ... -->` override comments. Returns the list of items
// (which may be tool names, category names, or a single profile name) or null.
const TOOLS_RE = /<!--\s*tools:\s*([\s\S]*?)\s*-->/i;

export function parseOverride(prompt) {
  if (!prompt) return null;
  const m = TOOLS_RE.exec(prompt);
  if (!m) return null;
  return m[1]
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

// Given an override token list, return the resolved allowed set:
//   - if it's a single token AND that token is a known profile name, use that profile
//   - otherwise treat each token as either a tool name or a category name; the
//     allowed set = explicit tool names ∪ tools in any of the listed categories
export function resolveOverride(tokens, profilesDoc, manifest) {
  const profiles = profilesDoc?.profiles || {};
  if (tokens.length === 1 && profiles[tokens[0]]) {
    return resolveProfile(tokens[0], profilesDoc, manifest);
  }
  const allowed = new Set();
  const categories = new Set();
  const toolNames = manifest?.tools || {};
  for (const tok of tokens) {
    if (toolNames[tok]) allowed.add(tok);
    else categories.add(tok);
  }
  for (const [name, tool] of Object.entries(toolNames)) {
    const cats = tool?.category || [];
    if (cats.some((c) => categories.has(c))) allowed.add(name);
  }
  return allowed;
}
