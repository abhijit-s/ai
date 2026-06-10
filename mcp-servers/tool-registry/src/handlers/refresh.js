// refresh handler: rebuild the manifest cache and return a summary.

import { buildAndWriteManifest } from "../manifest.js";

function jsonResponse(payload) {
  return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }] };
}

export async function handleRefresh(_args, projectRoot) {
  const summary = await buildAndWriteManifest(projectRoot);
  return jsonResponse(summary);
}
