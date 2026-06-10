// recommend_tool handler: score tools and return the best name.

import { readManifestOrEmpty } from "../manifest.js";
import { loadProfiles, resolveProfile } from "../profiles.js";
import { recommendTool } from "../recommend.js";

function jsonResponse(payload) {
  return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }] };
}

export async function handleRecommendTool(args, projectRoot) {
  const { intent, profile } = args;
  if (!intent || !intent.category) {
    throw new Error("recommend_tool: 'intent.category' is required");
  }
  const manifest = await readManifestOrEmpty(projectRoot);
  const profiles = await loadProfiles(projectRoot);
  const allowed = profile
    ? resolveProfile(profile, profiles, manifest)
    : null; // null = all tools allowed
  const result = recommendTool(intent, manifest, allowed);
  return jsonResponse({ recommended: result });
}
