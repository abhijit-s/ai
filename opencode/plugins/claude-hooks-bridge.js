// Bridge OpenCode plugin events to the existing Claude Code hook scripts.
//
// OpenCode has no shell-hook config; hooks are plugin functions. This generic
// engine spawns the Claude hook scripts named in hooks-bridge.json, feeding each
// a Claude-shaped JSON payload on stdin and honoring Claude's block signals:
//   - PreToolUse -> tool.execute.before  (deny => throw, which aborts the tool)
//   - PostToolUse -> tool.execute.after  (fire-and-forget)
//   - Stop/SessionStart/PostCompact -> the generic `event` hook, by event.type
//
// Fidelity is best-effort: OpenCode's payloads differ from Claude's, and events
// like SubagentStart have no analog. The bridge fails OPEN on any error so a
// misbehaving hook can never wedge OpenCode.

import { appendFileSync, readFileSync } from "node:fs";
import { homedir } from "node:os";

const CONFIG_PATH =
  process.env.CLAUDE_HOOKS_BRIDGE_CONFIG ||
  `${homedir()}/.dotfiles/ai/opencode/hooks-bridge.json`;

// Opt-in tracing: set CLAUDE_HOOKS_BRIDGE_DEBUG=1 to prove the plugin loaded and
// which events fired. Writes to ~/.cache/claude-hooks-bridge.log; no-op otherwise.
const TRACE_PATH = `${homedir()}/.cache/claude-hooks-bridge.log`;
function trace(msg) {
  if (!process.env.CLAUDE_HOOKS_BRIDGE_DEBUG) return;
  try {
    appendFileSync(TRACE_PATH, `${new Date().toISOString()} ${msg}\n`);
  } catch {
    // tracing must never affect the bridge
  }
}

// Read once at load; restart OpenCode to pick up config changes.
let CONFIG = { toolNameMap: {} };
try {
  CONFIG = JSON.parse(readFileSync(CONFIG_PATH, "utf8"));
} catch (e) {
  console.error(`[claude-hooks-bridge] no config at ${CONFIG_PATH}: ${e}`);
}

const toClaudeTool = (tool) => CONFIG.toolNameMap?.[tool] ?? tool;

const matches = (matcher, name) => {
  if (!matcher || matcher === "*") return true;
  try {
    return new RegExp(matcher).test(name);
  } catch {
    return false;
  }
};

async function runScript(command, payload) {
  const proc = Bun.spawn(["/bin/sh", "-c", command], {
    stdin: new TextEncoder().encode(JSON.stringify(payload)),
    stdout: "pipe",
    stderr: "pipe",
  });
  const [stdout, stderr] = await Promise.all([
    new Response(proc.stdout).text(),
    new Response(proc.stderr).text(),
  ]);
  return { code: await proc.exited, stdout, stderr };
}

// Translate a Claude hook's block signal into a reason string, or null to allow.
function blockReason(res) {
  try {
    const j = JSON.parse(res.stdout);
    const hso = j.hookSpecificOutput;
    if (hso?.permissionDecision === "deny")
      return hso.permissionDecisionReason || "blocked by hook";
    if (j.decision === "block") return j.reason || "blocked by hook";
  } catch {
    // non-JSON stdout is fine; fall through to the exit-code convention
  }
  if (res.code === 2) return res.stderr.trim() || "blocked by hook (exit 2)";
  return null;
}

async function runAll(entries, toolName, payloadFor, { blocking } = {}) {
  for (const entry of entries ?? []) {
    if (toolName !== undefined && !matches(entry.matcher, toolName)) continue;
    let res;
    try {
      res = await runScript(entry.command, payloadFor());
    } catch (e) {
      console.error(`[claude-hooks-bridge] ${entry.command} failed: ${e}`);
      continue; // fail open
    }
    if (blocking) {
      const reason = blockReason(res);
      if (reason)
        throw new Error(`[claude-hooks-bridge] ${entry.command}: ${reason}`);
    }
  }
}

export const ClaudeHooksBridge = async ({ directory }) => {
  trace(`plugin loaded (cwd=${directory}, config=${CONFIG_PATH})`);
  return {
  "tool.execute.before": async (input, output) => {
    const tool = toClaudeTool(input.tool);
    trace(`tool.execute.before ${input.tool}->${tool}`);
    await runAll(CONFIG.PreToolUse, tool, () => ({
      hook_event_name: "PreToolUse",
      tool_name: tool,
      tool_input: output.args,
      cwd: directory,
      session_id: input.sessionID,
    }), { blocking: true });
  },

  "tool.execute.after": async (input, output) => {
    const tool = toClaudeTool(input.tool);
    await runAll(CONFIG.PostToolUse, tool, () => ({
      hook_event_name: "PostToolUse",
      tool_name: tool,
      tool_input: input.args,
      tool_response: { output: output.output },
      cwd: directory,
      session_id: input.sessionID,
    }));
  },

  event: async ({ event }) => {
    const claudeEvent = {
      "session.idle": "Stop",
      "session.created": "SessionStart",
      "session.compacted": "PostCompact",
    }[event?.type];
    if (!claudeEvent) return;
    trace(`event ${event.type}->${claudeEvent}`);
    await runAll(CONFIG[claudeEvent], undefined, () => ({
      hook_event_name: claudeEvent,
      cwd: directory,
      session_id: event?.properties?.sessionID,
    }));
  },
  };
};
