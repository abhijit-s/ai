#!/usr/bin/env node
// Minimal stub MCP server for discovery tests. Honors TOOL_NAMES env var
// (comma-separated) and SERVER_BEHAVIOR (=ok|empty|crash|slow).

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

const behavior = process.env.SERVER_BEHAVIOR || "ok";
const toolNames = (process.env.TOOL_NAMES || "alpha,beta").split(",").map((s) => s.trim()).filter(Boolean);

if (behavior === "crash") {
  console.error("stub: crashing immediately");
  process.exit(2);
}

const server = new Server(
  { name: "stub", version: "0.0.0" },
  { capabilities: { tools: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, async () => {
  if (behavior === "slow") {
    await new Promise((r) => setTimeout(r, 20_000));
  }
  if (behavior === "empty") return { tools: [] };
  return {
    tools: toolNames.map((n) => ({
      name: n,
      description: `stub tool ${n}`,
      inputSchema: { type: "object", properties: { x: { type: "string" } } },
    })),
  };
});

server.setRequestHandler(CallToolRequestSchema, async () => ({
  content: [{ type: "text", text: "ok" }],
}));

const transport = new StdioServerTransport();
await server.connect(transport);
