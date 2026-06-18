#!/usr/bin/env bash
# PreToolUse(Bash) guard — block broad git staging/commit in the shared umbrella tree.
#
# Why: multiple Claude sessions (+ the Desktop app) share one working tree under
# ~/vaults/workspace. A broad `git add -A` / `git add .` / `git commit -a` sweeps
# OTHER sessions' uncommitted work into your commit. Stage only your own files.
#
# Behavior: deny the broad pattern ONLY when cwd is inside ~/vaults/workspace.
# Override per-command by prefixing `ALLOW_BROAD_COMMIT=1` (detected in the command
# string, since an inline env prefix never reaches this hook's own environment).
# Fails open (allow) on any parsing error so it can never wedge the Bash tool.

input=$(cat 2>/dev/null) || exit 0
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null) || exit 0
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$cwd" ] && cwd="$PWD"

# Only act inside the umbrella workspace (or its nested repos).
case "$cwd" in
  "$HOME/vaults/workspace"|"$HOME/vaults/workspace/"*) : ;;
  *) exit 0 ;;
esac

# Explicit per-command override.
printf '%s' "$cmd" | grep -q 'ALLOW_BROAD_COMMIT=1' && exit 0

MSG='Multiple Claude sessions share this working tree (~/vaults/workspace). Stage only the files you changed: git add <specific paths> — never git add -A / git add . / git commit -a (a broad add sweeps other sessions'\'' uncommitted work into your commit). Run git status and confirm every staged path is yours. See umbrella CLAUDE.md -> "Parallel sessions / shared working tree". Deliberate broad commit? Prefix the command with ALLOW_BROAD_COMMIT=1 (e.g. ALLOW_BROAD_COMMIT=1 git add -A).'

deny() {
  jq -n --arg r "$MSG" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}' 2>/dev/null
  exit 0
}

# Broad `git add`: -A, --all, or a standalone "." path token.
if printf '%s' "$cmd" | grep -Eq '(^|[;&|[:space:]])git[[:space:]]+add[[:space:]]+(-A|--all|\.)([[:space:]]|;|&|\||$)'; then
  deny
fi

# `git commit` with -a / -am / --all (single-dash cluster containing 'a', or --all);
# double-dash flags like --amend / --author are NOT matched.
if printf '%s' "$cmd" | grep -Eq '(^|[;&|[:space:]])git[[:space:]]+commit([[:space:]]|$)' \
  && printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])(-[A-Za-z]*a[A-Za-z]*|--all)([[:space:]=]|$)'; then
  deny
fi

exit 0
