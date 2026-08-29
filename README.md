# Wheelhouse

A native macOS client for the `codex` app-server, plus a CLI and a Claude Code
skill for driving it. Built because the official desktop app could not display
threads created on a remote host and offered no way to start one
(openai/codex #27284, #22438, #24280).

    NSWindow + WKWebView  ->  bridge.py (127.0.0.1:8770)  ->  codex app-server

`codex` ships a documented app-server speaking newline-delimited JSON-RPC over
stdio. The bridge owns exactly one as a child process and multiplexes it, so the
GUI and any number of CLI callers share a single server and a single view of
state.

## Requirements

- macOS 13+
- `codex` on PATH, authenticated (`~/.codex/auth.json`)
- `python3`
- Xcode or the Swift toolchain, to build the app

## Build and run

    ./build.sh
    open Wheelhouse.app

The app starts the bridge, which starts `codex app-server`. Quitting takes both
down. If a bridge is already listening on 8770, the app reuses it.

## The CLI

    bin/codex-run say "read the README and summarise it"

Put it on your PATH if you want it everywhere. It resolves its own location
through symlinks, so the repo can live anywhere:

    ln -s "$PWD/bin/codex-run" ~/.local/bin/codex-run

### Task files

A task file is a plain text file whose entire contents become the prompt for one
turn. There is no format -- no frontmatter, no schema, no directives. It is read
and sent verbatim.

    codex-run task <id> brief.txt --effort xhigh --cwd ~/some/project

It exists because anything worth handing to another agent is usually several
paragraphs -- background, constraints, an explicit output contract -- and
passing that as a shell argument is unreadable and invites quoting bugs. A file
is also reviewable before you send it and reusable afterwards.

`say` is the inline equivalent for one-liners. `task` adds what a longer
dispatch needs: `--skill NAME` attaches a skill to the turn, `--effort` sets
reasoning effort, and `--cwd` scopes where the turn may write (under
`workspace-write` an agent reads almost anywhere but writes only beneath its
cwd, so work in a sandbox or worktree needs this or every write fails). It
blocks until the turn ends, then prints the reply.

`on-file <path> <id> <file>` sends the same kind of file, but waits for `<path>`
to appear and stop changing first -- for handing over work that depends on a
long-running job finishing.

### It does not need the GUI

The CLI prefers the window when a bundle is present, but does not depend on it.
With no `Wheelhouse.app` built -- over SSH, on a headless box, or when you
simply do not want a window -- it runs `bridge.py` itself:

    CODEX_HEADLESS=1 codex-run say "what changed in the last commit?"

The bridge is started detached, so it outlives the invocation that started it
and later calls reuse it. `codex-run` is a single stdlib-only Python script
with no third-party dependencies; the only requirements are `python3` and an
authenticated `codex`.

Every verb is idempotent and self-healing: each one ensures the app and bridge
are up before it does anything, so there is no separate start step and repeated
invocations converge rather than duplicate.

    codex-run new                  reuse-or-create this chat's thread
    codex-run say "<text>"         send a turn, wait, print the reply
    codex-run task <id> <file>     send a task file (see below)
    codex-run watch <id>           follow status, sub-agents, tokens
    codex-run steer <id> "<text>"  type into a RUNNING turn
    codex-run rename [<id>] "<n>"  retitle a thread
    codex-run project              show/set the project a thread displays under
    codex-run list | read | info | agents | errors | archive | rm

Run it with no arguments for the full set.

### Threads belong to projects, not directories

Codex persists a turn's `cwd` into `threads.cwd`. Grouping the sidebar on that
column means passing `--cwd` to write into a scratch directory silently
relocates the whole conversation. Wheelhouse instead resolves project identity
from Claude's registered project roots and the driving chat's persisted origin;
the bridge serves that at `/projects`, and nothing a turn does can move it.
Thread names are cosmetic for the same reason: resolution goes through the
driving Claude chat's stable session id, so renaming a thread cannot orphan it
and another chat in the same directory cannot claim it by becoming active more
recently.

## The GUI

- **Projects / Running / Done / Agents** sidebar, sub-agents nested under their
  parent thread
- **Model, reasoning effort, service tier, approvals** — model-driven from
  `model/list`, showing the *resolved* value rather than the word "default"
- **Live streaming** of agent text, reasoning, and command execution with output
- **Steering**: typing while a turn runs sends `turn/steer` into that turn
  rather than queueing a new one
- **Approvals** inline, with the correct per-method decision vocabulary
- **Protocol pane**: every JSON-RPC frame in and out
- **Usage**: 5-hour and weekly rate-limit windows with reset times

The conversation renders entirely from the rollout JSONL on disk rather than a
client-side cache — the cache was the single root cause of missing, duplicated,
out-of-order and mis-timestamped messages.

Enter sends, Shift+Enter newlines. ⌘N new thread, ⌘R reload, ⌘Q quit.

## The skill

`.claude/skills/codex-run/SKILL.md` teaches Claude Code to hand work to Codex
and supervise it. It mostly encodes failure modes that cost real time: restarting
kills running turns, Codex cannot wait for anything, writes are scoped to the
turn's cwd while reads are not.

`SKILL.md` holds every rule, ordered by when you need it, so an agent that reads
only it still behaves correctly. Three companions beside it carry the material
that used to be inline and is consulted on demand: `INCIDENTS.md` (the
post-mortem behind each rule), `ORCHESTRATION.md` (methodology for two-agent
work), and `PROTOCOL.md` (architecture, the raw JSON-RPC method table, thread and
project resolution internals).

`.claude/skills` is deliberately **not** registered as a Codex skill root: this
skill drives Codex, so loading it into Codex's own context would invite
self-invocation against the bridge that owns its app-server.

## Configuration

| | |
|---|---|
| `CODEX_SKILL_ROOTS` | extra skill directories, colon-separated |
| `CODEX_DEFAULT_CWD` | default working directory for new threads |
| `CODEX_EFFORT` | default reasoning effort (default `xhigh`) |
| `CODEX_APP_DIR` | where to find `bridge.py` and the app bundle |
| `CODEX_HEADLESS` | `1` runs the bridge without the GUI |
| `state/skill-roots.json` | extra skill roots, as a local file |
| `.claude/skills/codex-run/LOCAL.md` | optional gitignored operator/project overlay for the Claude skill |

`state/` is machine-local and gitignored.

## Layout

    bin/codex-run      the CLI
    bridge.py          HTTP/SSE <-> app-server stdio bridge
    ui/index.html      the whole UI: no build step, no dependencies
    native/main.swift  NSWindow + WKWebView shell and menu bar
    build.sh           builds Wheelhouse.app

## License

MIT. Not affiliated with OpenAI.
