---
name: slack-notify
description: Send a Slack notification to a channel. Use as a helper agent at the end of tasks, steps, or workflows to report completion, failure, or status updates. Other agents and skills should invoke this whenever a user-visible event completes.
argument-hint: "<channel> <message> [status: success|error|warning|info]"
---

# Slack Notify

Send a structured notification to a Slack channel. Designed to be called by other agents and skills as a final step after completing work.

## Input Parsing

Parse the args string for:
- **channel**: channel name (e.g. `#general`, `engineering`, `a.salvi`) or user ID. Required.
- **message**: the notification body. Required.
- **status**: one of `success`, `error`, `warning`, `info`. Default: `info`.

If args are unstructured prose, extract intent. Example: `"notify #deploys that the migration finished successfully"` → channel=deploys, message="Migration finished", status=success.

## Step 1 — Resolve the Channel ID

If the channel value is already a Slack ID (starts with `C`, `U`, or `G` followed by uppercase alphanumerics), skip this step.

Otherwise, search for it:

```
mcp__claude_ai_Slack__slack_search_channels(query: "<channel name>", limit: 5)
```

Pick the closest match by name. If no match found, try `slack_search_users` for DMs. If still not found, report the error clearly and stop — do not guess.

## Step 2 — Format the Message

Build a concise, scannable notification. Use this structure:

```
<status_emoji> **<Title>**

<message body>

_<context line>_
```

**Status emoji mapping:**
| Status  | Emoji |
|---------|-------|
| success | ✅    |
| error   | ❌    |
| warning | ⚠️    |
| info    | ℹ️    |

**Title** is a short (≤8 word) summary derived from the message.

**Context line** (optional): include if there's relevant metadata like duration, environment, or triggering task. Keep it one line.

**Example output for a deploy notification:**
```
✅ **Deploy complete**

Monolith v3.40.5 deployed to production.

_Triggered by: release workflow · Duration: 4m 32s_
```

**Keep it tight.** Notifications should be skimmable in 3 seconds. No walls of text.

## Step 3 — Send the Message

```
mcp__claude_ai_Slack__slack_send_message(
  channel_id: "<resolved channel ID>",
  message: "<formatted message>"
)
```

## Step 4 — Confirm

After sending, return the message link. Report success or failure back to the calling agent/user in one line.

## Invocation Patterns

**From another agent or skill** — at the end of a task, include:
> After completing the work, invoke the `slack-notify` skill to notify `#<channel>` that `<what completed>` with status `<success|error>`.

**As a slash command:**
```
/slack-notify #deploys Migration DAP-713 completed successfully
/slack-notify #engineering Build failed on main — 3 tests broken (status: error)
/slack-notify @a.salvi Your scheduled report is ready (status: info)
```

**From orchestrator agents:**
```
Agent({
  subagent_type: "general-purpose",
  prompt: "Use the slack-notify skill to send a success notification to #releases: 'Service lstm-clip-service 1.57.0 deployed to production.'"
})
```

## Error Handling

- Channel not found → report clearly, do not send to a fallback channel
- Slack API error → surface the error message verbatim
- Message too long (>4000 chars) → truncate body with `…[truncated]` suffix
- Never silently swallow errors — the calling agent needs to know if notification failed
