// Pi -> Claude hooks bridge. Generic engine: reads hooks-bridge.json (one
// directory up) and invokes the declared Claude hook scripts on the matching
// Pi lifecycle event, using the same stdin-JSON / stdout-JSON protocol
// Claude's own hook runner uses. Config-not-fork: add or change wiring in
// hooks-bridge.json, not here. Mirrors opencode/plugins/claude-hooks-bridge.js
// at the same conservative scope -- only PreToolUse(bash) and Stop are wired;
// anything keyed on Claude-specific mechanisms (SubagentStart injection,
// tool-registry enforcement, SessionStart stdout-injection) has no faithful
// analog in Pi's event model and is intentionally left out.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { spawnSync } from "node:child_process";
import { homedir } from "node:os";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

interface HookEntry {
  matcher: string;
  command: string;
}

interface BridgeConfig {
  toolNameMap: Record<string, string>;
  tool_call?: HookEntry[];
  session_shutdown?: HookEntry[];
}

function expandHome(path: string): string {
  return path.startsWith("~") ? join(homedir(), path.slice(1)) : path;
}

function loadConfig(): BridgeConfig {
  const dir = dirname(fileURLToPath(import.meta.url));
  const raw = readFileSync(join(dir, "..", "hooks-bridge.json"), "utf-8");
  return JSON.parse(raw);
}

function runHook(command: string, input: unknown): string {
  const [cmd, ...args] = expandHome(command).split(" ");
  const result = spawnSync(cmd!, args, {
    input: JSON.stringify(input),
    encoding: "utf-8",
    timeout: 5000,
  });
  return result.stdout ?? "";
}

export default function (pi: ExtensionAPI) {
  const config = loadConfig();

  pi.on("tool_call", async (event: { toolName: string; input: unknown }) => {
    const claudeName = config.toolNameMap[event.toolName];
    if (!claudeName) return;

    for (const entry of config.tool_call ?? []) {
      if (entry.matcher !== "*" && entry.matcher !== event.toolName) continue;

      const stdout = runHook(entry.command, {
        tool_name: claudeName,
        tool_input: event.input,
        cwd: process.cwd(),
      });

      if (!stdout.trim()) continue;
      try {
        const parsed = JSON.parse(stdout);
        const decision = parsed?.hookSpecificOutput?.permissionDecision;
        if (decision === "deny") {
          return { block: true, reason: parsed.hookSpecificOutput.permissionDecisionReason };
        }
      } catch {
        // Hook emitted non-JSON stdout without a decision -- treat as allow.
      }
    }
    return;
  });

  pi.on("session_shutdown", async () => {
    for (const entry of config.session_shutdown ?? []) {
      runHook(entry.command, {});
    }
  });
}
