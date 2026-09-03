// Port of the Claude Code memory-kit `::track` philosophy to Pi: track-scoped
// native auto-memory, without a hard dependency on memory-kit's own Claude
// plugin (that lives under a versioned, Claude-managed cache path -- not
// something a Pi extension should couple to). The single shared source of
// truth is the corpus registry at ~/.config/memory-kit/config.toml, read via
// pi/memory_track_resolve.py (stdlib-only Python, no plugin coupling) -- so
// any new corpus (repo or vault) the registry grows is picked up with no
// code change here.
//
// Scope (v1, deliberately narrower than the Claude version -- see the
// pi-setup conversation): write-path reroute + `::track` switching + a
// session-start recall of the identity layer and the current track's own
// native index. The full tiered semantic-layer scoping, retired-track
// handling, and episodic digest are left for a follow-up once this baseline
// is proven -- the Claude side's own read-path is still a parked TODO too.
//
// Ledger is keyed by CORPUS NAME, not by Pi's session file path (undocumented
// in the extension API) or raw cwd (would fragment across subdirectories of
// the same corpus) -- so a `::track` set anywhere inside one corpus applies
// everywhere inside it, and never bleeds into a different corpus.
import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync, realpathSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { join, dirname, isAbsolute } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// realpathSync: this file is symlinked in from the ai/ repo, so
// import.meta.url resolves to the SYMLINK path -- follow it to find the
// resolver script that actually lives beside the real file.
const RESOLVER = join(dirname(realpathSync(fileURLToPath(import.meta.url))), "..", "memory_track_resolve.py");
const LEDGER_PATH = join(process.env.HOME ?? "", ".pi", "agent", "memory-track-ledger.json");
const TRACK_RE = /^[A-Za-z0-9][A-Za-z0-9._/-]*$/;
const DIRECTIVE_RE = /^(?:::|\/)track\s+(\S+)/;

interface CorpusInfo {
  corpus?: string;
  native_auto_dir?: string;
  identity_dir?: string;
}

// Memoized per-cwd for the process lifetime -- the registry does not change
// mid-session, and this runs on every tool_call, so re-parsing TOML each time
// would be wasteful.
const corpusCache = new Map<string, CorpusInfo>();

function resolveCorpus(cwd: string): CorpusInfo {
  if (corpusCache.has(cwd)) return corpusCache.get(cwd)!;
  let info: CorpusInfo = {};
  try {
    const raw = execFileSync("python3", [RESOLVER, cwd], { encoding: "utf-8", timeout: 5000 });
    info = JSON.parse(raw);
  } catch {
    info = {}; // fail-open: a resolver fault must never break a turn or a write
  }
  corpusCache.set(cwd, info);
  return info;
}

function sanitizeTrack(raw: string): string | null {
  const track = raw.trim();
  if (!TRACK_RE.test(track)) return null;
  if (track.split("/").some((part) => part === "" || part === "." || part === "..")) return null;
  return track;
}

function readLedger(): Record<string, string> {
  try {
    return JSON.parse(readFileSync(LEDGER_PATH, "utf-8"));
  } catch {
    return {};
  }
}

function writeLedgerEntry(corpus: string, track: string): void {
  const ledger = readLedger();
  ledger[corpus] = track;
  mkdirSync(dirname(LEDGER_PATH), { recursive: true });
  writeFileSync(LEDGER_PATH, JSON.stringify(ledger, null, 2) + "\n");
}

function currentTrack(corpus: string): string | null {
  const envTrack = process.env.MEMORY_TRACK ? sanitizeTrack(process.env.MEMORY_TRACK) : null;
  if (envTrack) return envTrack;
  return readLedger()[corpus] ?? null;
}

function readMarkdownDir(dir: string | undefined): string {
  if (!dir || !existsSync(dir)) return "";
  return readdirSync(dir)
    .filter((f) => f.endsWith(".md") && !f.startsWith("_"))
    .sort()
    .map((f) => readFileSync(join(dir, f), "utf-8").trim())
    .join("\n\n---\n\n");
}

function trackNativeIndex(nativeAutoDir: string, track: string): string {
  const trackedDir = nativeAutoDir.replace(/\/auto$/, `/auto-${track}`);
  const indexPath = join(trackedDir, "MEMORY.md");
  return existsSync(indexPath) ? readFileSync(indexPath, "utf-8").trim() : "";
}

export default function (pi: ExtensionAPI) {
  // Injected once, on the first turn of a fresh session -- not every turn.
  let recallInjected = false;

  pi.on("before_agent_start", async (event: { prompt: string }, ctx: { cwd: string }) => {
    const info = resolveCorpus(ctx.cwd);
    if (!info.corpus) return; // no memory corpus claims this cwd -- pass through untouched

    const directive = DIRECTIVE_RE.exec(event.prompt);
    if (directive) {
      const track = sanitizeTrack(directive[1]!);
      if (!track) {
        return {
          message: {
            customType: "memory-track",
            content: `[memory-track] ignored -- invalid track name. Acknowledge briefly, do nothing else.`,
            display: true,
          },
        };
      }
      writeLedgerEntry(info.corpus, track);
      let recall = "";
      if (info.native_auto_dir) {
        recall = trackNativeIndex(info.native_auto_dir, track);
      }
      const confirm = `[memory-track] re-tracked this session (corpus \`${info.corpus}\`) to \`${track}\`; new native memory writes follow it. This was a track-switch directive -- acknowledge in one short line, do nothing else this turn.`;
      return {
        message: {
          customType: "memory-track",
          content: recall ? `${confirm}\n\n${recall}` : confirm,
          display: true,
        },
      };
    }

    if (!recallInjected) {
      recallInjected = true;
      const track = currentTrack(info.corpus);
      const identity = readMarkdownDir(info.identity_dir);
      const recall = track && info.native_auto_dir ? trackNativeIndex(info.native_auto_dir, track) : "";
      const body = [identity, recall].filter(Boolean).join("\n\n---\n\n");
      if (body) {
        return {
          message: {
            customType: "memory-track",
            content: `[memory-track] recall (corpus \`${info.corpus}\`${track ? `, track \`${track}\`` : ""}):\n\n${body}`,
            display: false,
          },
        };
      }
    }
    return;
  });

  pi.on("tool_call", async (event: { toolName: string; input: { path?: string } }, ctx: { cwd: string }) => {
    if (event.toolName !== "write" && event.toolName !== "edit") return;
    const rawPath = event.input.path;
    if (!rawPath) return;

    const info = resolveCorpus(ctx.cwd);
    if (!info.corpus || !info.native_auto_dir) return;

    const track = currentTrack(info.corpus);
    if (!track) return; // no track set -- leave the write at the shared native dir, same as baseline

    // The write/edit tool accepts a relative OR absolute path; the model
    // almost always passes it relative to cwd, so resolve before comparing
    // against the resolver's absolute native_auto_dir.
    const path = isAbsolute(rawPath) ? rawPath : join(ctx.cwd, rawPath);
    const trackedDir = info.native_auto_dir.replace(/\/auto$/, `/auto-${track}`);
    if (path === trackedDir || path.startsWith(`${trackedDir}/`)) return; // already namespaced
    if (path === info.native_auto_dir) {
      event.input.path = trackedDir;
    } else if (path.startsWith(`${info.native_auto_dir}/`)) {
      event.input.path = trackedDir + path.slice(info.native_auto_dir.length);
    }
  });
}
