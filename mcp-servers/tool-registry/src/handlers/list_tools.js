// list_tools handler: filters the manifest by profile, intent, and health.

import { readManifestOrEmpty } from "../manifest.js";
import { loadProfiles, resolveProfile } from "../profiles.js";

function jsonResponse(payload) {
  return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }] };
}

export async function handleListTools(args, projectRoot) {
  const { profile, intent, health = true } = args;
  const manifest = await readManifestOrEmpty(projectRoot);
  const profiles = await loadProfiles(projectRoot);

  let names = Object.keys(manifest.tools || {});

  if (profile) {
    const allowed = resolveProfile(profile, profiles, manifest);
    names = names.filter((n) => allowed.has(n));
  }

  if (health) {
    names = names.filter((n) => manifest.tools[n]?.health?.state === "healthy");
  }

  if (intent?.category) {
    names = names.filter((n) => {
      const cats = manifest.tools[n]?.category || [];
      return cats.includes(intent.category);
    });
    // Order by prefer_over position when filtering by category.
    names.sort((a, b) => {
      const chainA = manifest.tools[a]?.prefer_over?.[intent.category] || [];
      const chainB = manifest.tools[b]?.prefer_over?.[intent.category] || [];
      // Tools at the head of a chain rank above tools they prefer over.
      if (chainA.includes(b)) return -1;
      if (chainB.includes(a)) return 1;
      return 0;
    });
  }

  const tools = names.map((n) => manifest.tools[n]);
  return jsonResponse({
    tools,
    count: tools.length,
    discovery_errors: manifest.discovery_errors || [],
  });
}
