// Opt-in, visible-dispatch companion to pi-subagents' in-process `Agent` tool.
// Ordinary delegation should keep using `Agent` (fast, in-process, no herdr
// dependency). Reach for this tool only when a task genuinely benefits from a
// separate, watchable terminal -- long-running work, parallel-and-visible
// work, or an explicit user request for a separate pane.
//
// Kept as its own extension file, separate from claude-hooks-bridge.ts --
// one concern per file.
//
// herdr already ships a built-in Pi integration (`herdr integration install
// pi`), so it detects and drives a `pi` process in a pane the same way it
// does Claude/Codex/OpenCode -- no custom naming/state hook scripts needed
// here (unlike Claude's herdr-agent-name.sh / herdr-agent-state.sh).
import { execFileSync } from "node:child_process";
import { Type } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

function herdr(args: string[]): any {
  const raw = execFileSync("herdr", args, { encoding: "utf-8" });
  return JSON.parse(raw);
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "spawn_pane_subagent",
    label: "Spawn Pane Subagent",
    description:
      "Dispatch a task to a NEW pi instance running in a separate, visible herdr pane. " +
      "Use only when the task benefits from a watchable terminal (long-running, parallel-and-visible, " +
      "or explicitly requested by the user). For ordinary delegation, use the Agent tool instead -- " +
      "it is faster and does not require herdr.",
    parameters: Type.Object({
      name: Type.String({ description: "Short, unique lowercase name for the pane's agent, e.g. 'reviewer'" }),
      prompt: Type.String({ description: "The task to run in the new pane" }),
      direction: Type.Optional(Type.Union([Type.Literal("right"), Type.Literal("down")], {
        description: "Split direction, default 'right'",
      })),
      timeoutMs: Type.Optional(Type.Number({ description: "How long to wait for completion, default 120000" })),
    }),

    async execute(_toolCallId, params: { name: string; prompt: string; direction?: "right" | "down"; timeoutMs?: number }) {
      if (process.env.HERDR_ENV !== "1") {
        return {
          content: [{
            type: "text",
            text: "Not running inside a herdr-managed pane (HERDR_ENV is unset) -- cannot spawn a "
              + "visible pane. Use the in-process Agent tool instead.",
          }],
        };
      }

      const direction = params.direction ?? "right";
      const split = herdr(["pane", "split", "--current", "--direction", direction, "--cwd", process.cwd(), "--no-focus"]);
      const paneId = split?.result?.pane?.pane_id;
      if (!paneId) {
        return { content: [{ type: "text", text: `herdr pane split failed: ${JSON.stringify(split)}` }] };
      }

      herdr(["agent", "start", params.name, "--kind", "pi", "--pane", paneId]);
      herdr(["agent", "prompt", params.name, params.prompt, "--wait", "--timeout", String(params.timeoutMs ?? 120000)]);
      const read = herdr(["agent", "read", params.name, "--source", "recent-unwrapped", "--lines", "200"]);

      return {
        content: [{ type: "text", text: read?.result?.text ?? JSON.stringify(read) }],
        details: { paneId, name: params.name },
      };
    },
  });
}
