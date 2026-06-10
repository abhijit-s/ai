// tool_health handler: re-probe one tool and update the cache.

import { reprobeTool } from "../health.js";

function jsonResponse(payload) {
  return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }] };
}

export async function handleToolHealth(args, projectRoot) {
  const { name } = args;
  if (!name) throw new Error("tool_health: 'name' argument is required");
  const updated = await reprobeTool(name, projectRoot);
  return jsonResponse(updated);
}
