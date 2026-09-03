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

// herdr's documented contract: a server-side error (e.g. agent_pane_busy)
// exits non-zero with JSON on stderr. Extracted so both herdr() and
// herdrText() report the same actionable message instead of a raw exec
// failure.
function herdrErrorMessage(err: any): string {
  const stderr: string = typeof err?.stderr === "string" ? err.stderr : "";
  let message = stderr.trim() || err?.message || String(err);
  try {
    message = JSON.parse(stderr)?.error?.message ?? message;
  } catch {
    // stderr wasn't JSON -- keep the raw message
  }
  return message;
}

// For control-plane commands (pane split, agent start/prompt) -- these return
// a JSON envelope on success.
function herdr(args: string[]): any {
  try {
    const raw = execFileSync("herdr", args, { encoding: "utf-8" });
    return JSON.parse(raw);
  } catch (err: any) {
    throw new Error(herdrErrorMessage(err));
  }
}

// For read commands (agent/pane read) -- these have NO json output mode
// (`--format text|ansi` only per herdr's own CLI signature): they dump the
// pane's actual rendered content as plain text, never a JSON envelope.
// JSON.parse-ing that content was the source of the "Unexpected token 'p',
// \"pi_style=...\" is not valid JSON" failure -- whatever the pane happened
// to render was never JSON to begin with.
function herdrText(args: string[]): string {
  try {
    return execFileSync("herdr", args, { encoding: "utf-8" });
  } catch (err: any) {
    throw new Error(herdrErrorMessage(err));
  }
}

// Best-effort: close a pane this tool created after a later step failed, so a
// partial spawn (e.g. split succeeded, agent start hit agent_pane_busy) never
// leaves an orphaned pane behind. Never throws -- a cleanup failure must not
// mask the original error.
function closePaneQuietly(paneId: string): boolean {
  try {
    herdr(["pane", "close", paneId]);
    return true;
  } catch {
    return false;
  }
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
      let split;
      try {
        split = herdr(["pane", "split", "--current", "--direction", direction, "--cwd", process.cwd(), "--no-focus"]);
      } catch (err) {
        return { content: [{ type: "text", text: `herdr pane split failed: ${(err as Error).message}` }] };
      }
      const paneId = split?.result?.pane?.pane_id;
      if (!paneId) {
        return { content: [{ type: "text", text: `herdr pane split failed: ${JSON.stringify(split)}` }] };
      }

      // From here on, the pane exists -- any failure must clean it up rather
      // than leave an orphan (this is what agent_pane_busy left behind before).
      try {
        herdr(["agent", "start", params.name, "--kind", "pi", "--pane", paneId]);
        herdr(["agent", "prompt", params.name, params.prompt, "--wait", "--timeout", String(params.timeoutMs ?? 120000)]);
        const output = herdrText(["agent", "read", params.name, "--source", "recent-unwrapped", "--lines", "200"]);
        return {
          content: [{ type: "text", text: output }],
          details: { paneId, name: params.name },
        };
      } catch (err) {
        const cleaned = closePaneQuietly(paneId);
        const note = cleaned
          ? `Closed pane ${paneId}.`
          : `WARNING: could not close pane ${paneId} -- check \`herdr agent list\` for an orphan.`;
        return {
          content: [{ type: "text", text: `spawn_pane_subagent failed: ${(err as Error).message}\n${note}` }],
        };
      }
    },
  });
}
