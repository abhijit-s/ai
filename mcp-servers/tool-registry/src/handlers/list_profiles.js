// list_profiles handler: return all profile names + their resolved allowed-tool sets.

import { readManifestOrEmpty } from "../manifest.js";
import { loadProfiles, resolveProfile } from "../profiles.js";

function jsonResponse(payload) {
  return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }] };
}

export async function handleListProfiles(_args, projectRoot) {
  const manifest = await readManifestOrEmpty(projectRoot);
  const profiles = await loadProfiles(projectRoot);
  const result = {};
  for (const name of Object.keys(profiles.profiles || {})) {
    result[name] = Array.from(resolveProfile(name, profiles, manifest)).sort();
  }
  return jsonResponse({ profiles: result });
}
