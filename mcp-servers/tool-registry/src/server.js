// MCP Server wiring. U1 provides stub handlers; U7 replaces them with real ones.

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

import { handleListTools } from "./handlers/list_tools.js";
import { handleToolHealth } from "./handlers/tool_health.js";
import { handleRecommendTool } from "./handlers/recommend_tool.js";
import { handleListProfiles } from "./handlers/list_profiles.js";
import { handleRefresh } from "./handlers/refresh.js";

const TOOL_DEFINITIONS = [
  {
    name: "list_tools",
    description:
      "Return registry-known tools, optionally filtered by profile, intent, " +
      "or health. With no arguments, returns the full healthy set.",
    inputSchema: {
      type: "object",
      properties: {
        profile: { type: "string", description: "Profile name to filter by" },
        intent: {
          type: "object",
          description: "Intent filter: { category?, tags? }",
          properties: {
            category: { type: "string" },
            tags: { type: "array", items: { type: "string" } },
          },
        },
        health: {
          type: "boolean",
          description:
            "When true (default), only return healthy tools. When false, " +
            "include unhealthy tools with failure detail for triage.",
        },
      },
    },
  },
  {
    name: "tool_health",
    description:
      "Re-probe health for a single tool by manifest name and return the " +
      "updated state.",
    inputSchema: {
      type: "object",
      properties: { name: { type: "string" } },
      required: ["name"],
    },
  },
  {
    name: "recommend_tool",
    description:
      "Score allowed-and-healthy tools against the given intent and return " +
      "the highest-ranked tool name, or null if no tool fits.",
    inputSchema: {
      type: "object",
      properties: {
        intent: {
          type: "object",
          properties: {
            category: { type: "string" },
            tags: { type: "array", items: { type: "string" } },
          },
          required: ["category"],
        },
        profile: { type: "string" },
      },
      required: ["intent"],
    },
  },
  {
    name: "list_profiles",
    description:
      "Return all profile names and their resolved allowed-tool sets.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "refresh",
    description:
      "Rebuild the manifest cache without a session restart. Returns a " +
      "summary { tools_count, healthy_count, errors_count }.",
    inputSchema: { type: "object", properties: {} },
  },
];

export async function startServer(projectRoot) {
  const server = new Server(
    { name: "mcp-tool-registry", version: "0.1.0" },
    { capabilities: { tools: {} } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: TOOL_DEFINITIONS,
  }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;
    try {
      switch (name) {
        case "list_tools":
          return await handleListTools(args || {}, projectRoot);
        case "tool_health":
          return await handleToolHealth(args || {}, projectRoot);
        case "recommend_tool":
          return await handleRecommendTool(args || {}, projectRoot);
        case "list_profiles":
          return await handleListProfiles(args || {}, projectRoot);
        case "refresh":
          return await handleRefresh(args || {}, projectRoot);
        default:
          throw new Error(`Unknown tool: ${name}`);
      }
    } catch (err) {
      return {
        content: [{ type: "text", text: `Error: ${err.message}` }],
        isError: true,
      };
    }
  });

  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error(`tool-registry: MCP server running on stdio (root=${projectRoot})`);
}

export { TOOL_DEFINITIONS };
