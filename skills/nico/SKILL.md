---
name: nico
description: General-purpose notification dispatcher. Sends notifications to pluggable channels (Slack, email). Use as a helper at the end of any task, step, or workflow. Invoke via /nico or from any agent/skill prompt.
argument-hint: "<channel-prefix>:<target> <message> [status: success|error|warning|info]"
---

# Nico — Notification Dispatcher

Send structured notifications to any supported channel. Designed to be dropped into any workflow as a final step.

## Supported Channels

| Prefix | Target format | Status |
|--------|--------------|--------|
| `slack:` | `#channel-name` or `@username` or Slack user ID | ✅ Full |
| `email:` | `user@example.com` | ⚠️ Creates draft (requires manual send) |

## Input Format

```
<prefix>:<target> <message> [status: success|error|warning|info]
```

**Examples:**
```
/nico slack:#deploys Migration DAP-713 complete status: success
/nico slack:@a.salvi Your report is ready status: info
/nico email:a.salvi@easygo.io Build failed on main — 3 tests broken status: error
/nico slack:#engineering Service lstm-clip-service 1.57.0 deployed
```

If args are unstructured prose, extract intent:
- `"notify #releases that the deploy finished"` → `slack:#releases`, message=deploy finished, status=info
- `"email me that the job failed"` → `email:a.salvi@easygo.io`, status=error

**Default status:** `info` when not specified.

---

## Step 1 — Parse Input

Extract:
- **channel_type**: everything before the first `:`
- **target**: everything after `:` up to the first space
- **message**: remaining text (strip `status:` suffix if present)
- **status**: value after `status:` if present, else `info`

---

## Step 2 — Format the Notification

Build the payload before dispatching. Use this structure for all channels:

**Status emoji:**
| Status  | Emoji |
|---------|-------|
| success | ✅    |
| error   | ❌    |
| warning | ⚠️    |
| info    | ℹ️    |

**Title:** ≤8 word summary derived from the message (imperative, e.g. "Deploy complete", "Build failed on main").

**Body:** the message, unchanged.

**Context line** (optional): include if metadata is available — duration, environment, triggering task/ticket. One line only.

**Template:**
```
<emoji> **<Title>**

<message body>

_<context line>_
```

---

## Step 3 — Dispatch

Route to the correct channel handler based on `channel_type`.

---

### Channel: `slack`

**Resolve target → channel ID:**

- If target starts with `#`: search by channel name
  ```
  mcp__claude_ai_Slack__slack_search_channels(query: "<name without #>", limit: 5)
  ```
  Pick closest name match. If ambiguous, pick the non-archived one with the most members.

- If target starts with `@` or is a display name: search by user
  ```
  mcp__claude_ai_Slack__slack_search_users(query: "<name or email>", limit: 5)
  ```
  Use the `id` field from the best match as `channel_id` (DMs use user ID).

- If target is already a Slack ID (starts with `C`, `U`, or `G`): use directly.

**Send:**
```
mcp__claude_ai_Slack__slack_send_message(
  channel_id: "<resolved ID>",
  message: "<formatted message>"
)
```

Return the message link on success.

---

### Channel: `email`

**Note:** Email creates a Gmail draft — it does NOT auto-send. Inform the caller that a draft was created and requires manual sending.

**Compose subject** from the title: `[Nico] <Title>` (e.g. `[Nico] Build failed on main`).

**Compose HTML body:**
```html
<p><strong><emoji> <Title></strong></p>
<p><message body></p>
<p><em><context line></em></p>
<hr>
<p style="color:#888;font-size:12px">Sent via Nico · <timestamp></p>
```

**Create draft:**
```
mcp__claude_ai_Gmail__create_draft(
  to: ["<email address>"],
  subject: "<subject>",
  htmlBody: "<html body>"
)
```

Return the draft ID and note that the user must open Gmail to send it.

---

## Step 4 — Confirm

After dispatching, return one line to the caller:

- **Slack success:** `Notified <target> via Slack — <message link>`
- **Slack failure:** `Failed to notify <target> via Slack: <error>`
- **Email draft:** `Draft created for <email> — open Gmail to send (draft ID: <id>)`
- **Email failure:** `Failed to create email draft: <error>`

Never silently swallow errors.

---

## Adding New Channels

To wire a new channel, add a new `### Channel: <prefix>` section under Step 3 with:
1. Target resolution logic
2. MCP tool call(s)
3. Confirmation format

Current candidates when MCP tools become available: `pagerduty:`, `notion:`, `gcal:`.

---

## Invocation from Other Agents

Drop this into any agent/skill prompt as a final step:

```
After completing work, use the nico skill to notify slack:#<channel>
that <what completed> with status <success|error>.
```

Or from an orchestrator:
```
Agent({
  prompt: "Use the nico skill to send a success notification to slack:#releases: 'lstm-clip-service 1.57.0 deployed to production.'"
})
```
