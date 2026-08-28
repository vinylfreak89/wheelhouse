# codex-run — architecture and raw protocol

**Contains:** why the bridge exists and how it is wired, the raw JSON-RPC method
table for the escape hatch in `./SKILL.md`, and the thread/project resolution
internals. **Does NOT contain:** the rules for driving Codex, or permission to
bypass the CLI. Go through `bin/codex-run` unless `./SKILL.md` says otherwise;
this file is reference for the cases where it has no subcommand, and for anyone
changing the bridge itself.

---

## Architecture

Codex is driven through the **app-server JSON-RPC protocol**, not the CLI and not
the official desktop app. A local bridge exposes it over HTTP so both Claude and
the GUI can drive the *same* server — the protocol has no thread ownership
model, so several clients may attach to one thread. That is the whole point:
the user watches in the window while Claude drives, and either can type.

    Wheelhouse.app (WKWebView) ─┐
                           ├─→ bridge.py 127.0.0.1:8770 ─→ codex app-server
    Claude (curl /rpc)  ───┘

Project: `~/Documents/codex-app` — `README.md` for usage, `INCIDENTS.md` for
every trade-off and the upstream bugs that forced them.

---

## What the CLI does for you

*Rule: "Use the CLI — do not hand-roll the startup", in `./SKILL.md`.*

**Always drive Codex through this.** Every subcommand preflights: it launches
`Wheelhouse.app` if it is not running, waits for the bridge, then acts. With no
bundle present, or under `CODEX_HEADLESS=1`, it runs `bridge.py` itself instead,
so the CLI works with no window at all.

There is no wait-for-trigger primitive to build continuation on. Of the 95
client methods the only related ones are `fs/watch` / `fs/unwatch` (which notify
the CLIENT, not the agent) and `hooks/list` (read-only).

Under `sandbox: "workspace-write"`, `thread/start` takes a `cwd`, and
`turn/start` takes one that overrides it for that turn.

---

## Method table

Only when the CLI has no subcommand for what you need. Still run
`codex-run up` first so the GUI exists.

    curl -s -XPOST 127.0.0.1:8770/rpc -H 'Content-Type: application/json' \
      -d '{"method":"thread/list","params":{"limit":10}}'

Core flow: `thread/start` → `thread/name/set` → `turn/start` → watch `/events`.

| need | method |
|---|---|
| new thread | `thread/start` {cwd, sandbox, approvalPolicy, model, threadSource:"user"} |
| name it | `thread/name/set` {threadId, name} |
| send a message | `turn/start` {threadId, input:[{type:"text",text}], model?, effort?} |
| type into a RUNNING turn | `turn/steer` {threadId, expectedTurnId, input} |
| stop a turn | `turn/interrupt` {threadId, turnId} |
| history with tool output | `thread/turns/list` {threadId, itemsView:"full"} |
| effective settings | `GET /threadmeta?id=<threadId>` (model, effort, sandbox, tokens) |
| live stream | `GET /events` (SSE) |

Threads are named after the driving chat (see above). There is **no project
API**. The sidebar groups on the pin in `<repo>/state/projects.json`, NOT on
`cwd` — see "Where the project pin lives" below. Set `cwd` for the sandbox
scope; it no longer determines grouping.

The gotchas that bite hardest here — `result.data`, the per-request-type
approval vocabulary, `turn/interrupt` needing `turnId`, the status enum — are in
`./SKILL.md` under "Gotchas that will cost you an hour each". Read them before
sending anything.

---

## Thread and project resolution internals

*The rules that depend on these are in `./SKILL.md` under "Use the CLI",
"`--cwd` moves the writes, NOT the conversation", and "Thread names are
cosmetic — resolution is not". You do not need this section to follow them.*

**How a thread is bound to a chat.** `codex-run` resolves Claude's exported
session id against the desktop session JSON, then binds the resulting stable id
and title to the thread. That binding is recorded when the thread is first
created or resolved. Two chats in the same directory therefore cannot steal one
another's thread merely by becoming active most recently. Direct terminal use,
where no Claude session id exists, falls back to the most recently active
same-directory session. The user should be able to look at a Codex thread and
know which conversation spawned it.

`find_thread` resolves through the registry's **chat** binding rather than the
thread's display name, which is why a rename is safe at any time.

No `[project]` prefix: the sidebar already groups by the project pin, so the
prefix is redundant.

**Where the project pin lives.** A thread's project is pinned in
`<repo>/state/projects.json`. The bridge serves that at `/projects`, and the
sidebar groups on it — not on `threads.cwd`, which a turn can change.

**Why the basename is the display name.** The displayed name is the project
directory's basename, the same name Claude Code shows. Claude Code has no
separate project-name record: a project IS a cwd, so the basename is
authoritative rather than a guess. The sidebar shows the name and keeps the full
path in the tooltip.

---

## Skill roots

`bridge.py` registers extra skill roots with the app-server at startup via
`skills/extraRoots/set`, because the app-server forgets them on restart. The
roots are `<repo>/skills`, plus anything listed in `state/skill-roots.json` or
`$CODEX_SKILL_ROOTS` (colon-separated). Each root is a directory *containing*
skill folders; Codex namespaces them by the parent directory name.

`.claude/skills` — this directory — is deliberately not a root, and must not be
added. See "Skill roots — this skill is deliberately not one of them" in
`./SKILL.md` for the reason.

---

## Do not use the official desktop app

It cannot display threads created on a remote host and offers no way to start
one (openai/codex #27284, #22438, #24280). That is why this exists.
