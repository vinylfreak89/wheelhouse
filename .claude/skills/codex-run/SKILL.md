---
name: codex-run
description: Hand work to OpenAI Codex from Claude and watch it run in the Wheelhouse.app GUI. Use whenever the user says "run this on codex", "give this to codex", wants a second agent to attempt something, or wants dangerous/large work done outside this session. Also covers driving, steering, and cleaning up Codex threads.
---

# Running work on Codex

Codex is driven through the **app-server JSON-RPC protocol**, not the CLI and not
the official desktop app. A local bridge exposes it over HTTP so both Claude and
the GUI can drive the *same* server — the protocol has no thread ownership
model, so several clients may attach to one thread. That is the whole point:
the user watches in the window while Claude drives, and either can type.

    Wheelhouse.app (WKWebView) ─┐
                           ├─→ bridge.py 127.0.0.1:8770 ─→ codex app-server
    Claude (curl /rpc)  ───┘

every trade-off and the upstream bugs that forced them.

## Use the CLI — do not hand-roll the startup

    <repo>/bin/codex-run

**Always drive Codex through this.** Every subcommand preflights: it launches
`Wheelhouse.app` if it is not running, waits for the bridge, then acts. Starting
`bridge.py` directly also "works" and is WRONG — it leaves the user with no
window to watch, which is the entire point of this setup.

    codex-run say "do the thing"        # most common — auto-named from this chat
    codex-run up                        # just ensure GUI + bridge are live
    codex-run name                      # show the name that would be used
    codex-run new ["<name>"] [cwd]      # create a thread, print its id
    codex-run send <id> "<text>"        # send a turn, wait, print the reply
    codex-run steer <id> "<text>"       # type into a RUNNING turn
    codex-run list / info <id> / read <id>
    codex-run archive <id>              # tidy up, KEEPS the transcript
    codex-run rm <id>                   # delete permanently

`cwd` defaults to the directory you are working in, so the thread lands in the
right project group and the sandbox is scoped to that project. Pass an explicit
cwd only to override.

**Names are automatic and should stay that way.** `codex-run` names a thread
after **this chat** — e.g. `Llama-Guard local model setup` — read from the title
of the Claude session whose `cwd` matches, in
`~/Library/Application Support/Claude/claude-code-sessions/*/*/*.json`. The user
should be able to look at a Codex thread and know which conversation spawned it.

No `[project]` prefix: the sidebar already groups by `cwd`, so the prefix is
redundant. Pass an explicit name only when one thread per chat is not enough.

Requires `codex` on PATH (brew cask), `python3`, and `~/.codex/auth.json`.
Never start a second `codex app-server` by hand.

## Raw protocol (escape hatch)

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
API** — the sidebar groups by `cwd`, so set `cwd` to the project directory.

## Do NOT restate what Codex already loads

`AGENTS.md` in this project is a **symlink to CLAUDE.md** — byte identical.
Codex reads it automatically on every thread. Everything in it is already in
context: project conventions, hard rules, directory layout, the lot.

So a dispatch must NOT re-state:
  * project conventions or coding standards
  * "never touch /Volumes/T7", sandbox etiquette, or anything else in CLAUDE.md
  * background about the project

Repeating it wastes tokens on every turn AND dilutes the actual instruction —
the one novel thing you are asking for competes with 800 lines it already knows.

A dispatch should carry ONLY what is genuinely new to this task:
  1. the goal
  2. the specific inputs (job id, path, commit)
  3. anything that CONTRADICTS or narrows the standing instructions
  4. the output contract

Check before writing a dispatch:  `ls -l AGENTS.md` — if it points at CLAUDE.md,
assume Codex knows everything in it.

### If Codex must change those instructions, it edits CLAUDE.md — never AGENTS.md

`AGENTS.md` is a **symlink** to `CLAUDE.md`. Most write paths (editors, `>`
redirection, atomic write-then-rename, many tooling `apply_patch`
implementations) REPLACE a symlink with a regular file instead of writing
through it. That silently forks the two: Codex keeps reading a now-stale
`AGENTS.md` while `CLAUDE.md` moves on, and nothing errors.

So any dispatch that might update project instructions must say:

    Edit CLAUDE.md directly. Do NOT write to AGENTS.md — it is a symlink to
    CLAUDE.md and writing to it will replace the link with a regular file.

**Respect a standing embargo.** If the user has said not to touch CLAUDE.md,
that holds until they lift it — say so explicitly in the dispatch rather than
relying on Codex to infer it. An embargo is currently in force unless the user
has since lifted it.

Verify the link survived any run that touched instructions:

    ls -l AGENTS.md        # must still show  AGENTS.md -> CLAUDE.md

## Restarting the app kills running turns

The bridge owns `codex app-server` as a CHILD process. Killing the bridge — or
the app — aborts any turn in flight. Observed: a user's message was accepted,
started a turn, and was destroyed six seconds later by a restart; the text was
never persisted anywhere recoverable (`task_started` and `turn_aborted` in the
rollout, no `user_message` record, nothing in the queue DB).

    codex-run reload      # UI changes — hot-reloads the page, no turn loss
    codex-run busy        # exits non-zero if any thread is mid-turn
    codex-run restart     # REFUSES while a turn is active

UI changes never need a restart: the bridge serves `ui/index.html` fresh on
every request. Only Swift shell changes require relaunching the app.

## Codex cannot wait for anything — continuation is YOUR job

There is no wait-for-trigger primitive. A turn runs to completion and stops.
Of the 95 client methods the only related ones are `fs/watch` / `fs/unwatch`
(which notify the CLIENT, not the agent) and `hooks/list` (read-only). Do not
write a dispatch that says "wait until X appears then continue" — it will either
stop immediately or burn tokens polling in a loop.

Two mechanisms, both driven from outside:

    codex-run on-file <path> <id> <taskfile>    # wait for a file, then dispatch
    codex-run queue <id> "<text>"               # queue a turn to run after this one

`on-file` waits for the path to exist AND stop changing size (three stable
polls) before dispatching, so a half-written file is never consumed, and it
waits for any in-flight turn to finish rather than colliding with it.

For anything more complex — a condition, an external event, a schedule — the
orchestrator polls and dispatches. Split the work into phases that each end in
a written artifact, and gate the next phase on that artifact appearing.

## Blindness must be STRUCTURAL, never instructed

If a task requires an agent to derive something independently and only THEN be
compared against a reference answer, you cannot achieve that by telling it not
to look. Access is capability: if the reference is reachable, it will leak into
the derivation — usually at the moment the agent gets stuck, which is exactly
when contamination matters most.

Observed, and admitted by Codex unprompted when asked directly:

    "I also used Claude's recorded outputs to reconstruct expected diffs too
     early. Most clearly, when regeneration failed, I mechanically applied line
     changes known from Claude's result ... That contaminates the replay."

The dispatch had said to reconstruct the reference "for comparison" while
handing over the transcript that contained it. The resulting convergence figures
measured copying, not re-derivation, and were reported as a result before anyone
checked. **A contaminated run is worse than a failed one: it produces confident
numbers that mean nothing.**

Enforce it with a barrier the agent cannot cross:

1. **Stage inputs per step.** Step N's working directory contains ONLY the
   instruction and the artifacts legitimately available at that point.
2. **Keep the reference out of reach** — a different directory, or withheld by
   the orchestrator entirely — until the agent has written its own result.
3. **Freeze before reveal.** The agent commits its output (a file, a hash) and
   says so. Only then is the reference exposed.
4. **Compare as a separate step**, ideally a separate agent, so the comparer
   cannot retro-fit the derivation.

If you cannot stage it that way, at minimum ASK the agent afterwards whether it
stayed blind, and treat the answer as evidence — it will often tell you plainly
that it did not. Better: do not rely on that.

This is the same design the scrub gate itself uses (blind prober, separate
private evaluator). Copy that shape whenever "independent derivation" matters.

## Orchestrating well — Codex cannot see your context

You are the orchestrator; Codex is a separate agent with **none** of your
conversation, your files-in-mind, or your assumptions. An instruction that feels
complete to you is usually underspecified to it.

Every dispatch must carry:

1. **The exact entrypoint** — script path and subcommand, not "run the gate".
2. **Which inputs** — the specific job id, file, or commit. A pipeline stage
   named without its job will make Codex guess, and it will guess wrong.
3. **Preconditions** — what must already exist, and what to do if it does not
   (fail loudly, rather than invent a path).
4. **The output contract** — exactly what the final message must contain. If the
   work touches private material, say explicitly that the reply may carry status,
   counts, and tooling errors ONLY, never content.

Observed failure: dispatching "run the Filter B evaluator stage" produced correct
stage names and zeros, because `b2_evaluator` is stage 7 of a pipeline and no job
id was given. Codex behaved reasonably; the instruction was incomplete.

When a run comes back empty or wrong, re-read the dispatch before blaming the
model.

## Speaking Codex's language

Codex is not Claude Code. Two differences bite immediately:

**Sub-agents are separate threads.** Codex spawns them as child threads linked by
spawn edges, discoverable with `thread/list {ancestorThreadId}` and shown in the
app's Agents tab. They are NOT inline tool calls, and they do not appear in a
default `thread/list` (interactive sources only). When you want fan-out, say
"spawn a sub-agent per X" — do not assume the Claude Task-tool shape.

**Anything that shells out to `claude` will fail — and must keep failing.**
The `claude` CLI is deliberately NOT on PATH.

> **HARD RULE: never install the `claude` CLI, and never add it to PATH.**
> Not to "unblock" a run, not temporarily, not in a subshell. Its absence is a
> deliberate boundary: work handed to Codex must be done BY Codex, not silently
> bounced back into Claude. If a tool needs it, change the tool's runner.

Tools with a pluggable runner will happily choose a "headless" mode that invokes
it and die with:

    RuntimeError: headless call error: Not logged in · Please run /login

Observed with the scrub gate: `runner: auto` chose headless, Prepare completed,
the first LLM stage (canary) failed on auth. **Always pin such tools to SESSION
mode**, i.e. Codex itself performs the model stages — spawning its own
sub-agents — through the tool's bootstrap/submit interface.

### Driving a staged pipeline (the shape that works)

Mirror how Claude runs it: resume, then step in order, one sub-agent per stage.

1. **Resume, do not restart.** Ask the tool for existing state first
   (`status`, or list its job dir). If a job exists, continue from the first
   stage with no recorded output. Starting a fresh job silently discards work
   and, for gates with cumulative filters, corrupts the history.
2. **Run stages in the tool's declared order** — take the order from the tool
   itself (e.g. `--stage` choices in `--help`), never from memory.
3. **Per stage:** `bootstrap` the prompt → spawn a sub-agent to answer it →
   `submit` the reply. Do not answer bootstrap prompts inline if the tool
   expects an isolated agent; isolation is usually the point.
4. **Stop on the first stage error** and report which stage and the verbatim
   tool error. Do not skip ahead.

## Effort is a budget decision, not a default

`codex-run` defaults to `xhigh`, but **match the tier to the work** — a blanket
`xhigh` burned 92% of a 5-hour window on one transcript-reading pass.

| work | tier |
|---|---|
| bulk reading, grepping, summarising large files | `medium` |
| ordinary implementation, refactors, running a tool | `high` |
| judgement: diffing for structural equivalence, adversarial review, "is this the same decision?" | `xhigh` |

Pass it per task: `codex-run task <id> <file> medium`.

Check the budget BEFORE dispatching anything large:

    curl -s -XPOST 127.0.0.1:8770/rpc -H 'Content-Type: application/json' \
      -d '{"method":"account/rateLimits/read","params":{}}'

The primary window is 5 hours, the secondary 7 days. A multi-phase job that
exhausts the 5h window mid-run loses everything it had not yet reported, so
split phases and check between them. If a phase is cheap, run it cheap.

## Getting facts out of a run you must not read

Sometimes Codex must read material Claude is forbidden to see. Do not read the
thread. Instead:

1. Put a strict OUTPUT CONTRACT in the dispatch naming exactly what may appear.
2. Ask for the answer on a single machine-readable line, e.g.
   `RECOVERY_POINT=<iso8601> COMMIT=<sha>` — a fact, not prose.
3. Extract with a regex for that line only; never print surrounding text.

## Writes are scoped to the turn's cwd — reads are not

Under `sandbox: "workspace-write"` Codex may READ almost anywhere but may only
WRITE beneath the turn's `cwd` (plus /tmp). This asymmetry is vicious: a task
that reads its inputs happily will run for a while and then die on the first
write with

    fatal: Unable to create '<path>/.git/index.lock': Operation not permitted

Observed: a replay job was pointed at a sandbox under the tool's own directory
while its thread's cwd was a different project. Every read worked;
the first `git` operation failed.

**If the work writes anywhere outside the thread's own directory, pass that
directory as the turn's cwd:**

    codex-run task <id> <file> --effort high --cwd /path/to/sandbox

`thread/start` takes a `cwd`, and `turn/start` takes one that overrides it for
that turn — use the turn-level override so one chat keeps one thread while
still writing into a sandbox.

### `--cwd` moves the writes, NOT the conversation

Codex persists a turn's `cwd` into `threads.cwd`. The sidebar used to group on
that column, so one `--cwd` into a scratch directory silently relocated the
whole conversation out of its project — and `find_thread` matched on cwd too,
so the next `codex-run new` could fail to find the thread and split the chat
across two.

A thread's project is now **pinned once**, when `codex-run` first creates or
resolves it, in `<repo>/state/projects.json`. The bridge serves
that at `/projects`, the sidebar groups on it, and nothing a turn does can move
it. This is automatic — if you go through the CLI there is nothing to remember.

    codex-run project              # name, root, and this chat's pinned thread
    codex-run project --name NAME  # override (two checkouts, same basename)
    codex-run project --repair     # pin threads created before the registry

### Thread names are cosmetic — resolution is not

`codex-run rename [<id>] "<name>"` retitles a thread. It is safe to do at any
time, including on a thread mid-turn, because `find_thread` resolves through
the registry's **chat** binding rather than the thread's display name. That
binding is recorded when the thread is first created or resolved.

This was not always true: resolution used to match the display name against the
chat title, so a single rename would orphan the thread and the next `new` would
quietly open a second one. If you touch that code path, keep the property and
test it by exercising it — rename, re-resolve, assert the id is unchanged.

The displayed name is the project directory's **basename** — the same name
Claude Code shows. Claude Code has no separate project-name record: a project
IS a cwd, so the basename is authoritative rather than a guess. Do not derive a
prettier name from `CLAUDE.md` or anywhere else; the sidebar shows the name and
keeps the full path in the tooltip.

## Gotchas that will cost you an hour each

1. **`thread/list` returns results under `result.data`** — not `.threads` or
   `.items`. Reading the wrong key yields a silent, convincing `0`.
2. **Approval decision vocabulary differs per request type.** Sending the wrong
   value is treated as a *rejection*, not an error:
   - `item/commandExecution/requestApproval`, `item/fileChange/requestApproval`
     → `accept` | `acceptForSession` | `decline`
   - `execCommandApproval`, `applyPatchApproval`
     → `approved` | `approved_for_session` | `abort`
3. **Thread status enum** is `notLoaded | idle | systemError | active`. There is
   no `"running"`.
4. **`turn/interrupt` requires `turnId`**, not just `threadId`.
5. **Threads do not appear in `thread/list` until their first turn.**
6. **`reasoning effort` is per model.** `model/list` gives
   `supportedReasoningEfforts` and `defaultReasoningEffort`; `gpt-5.6-sol`
   defaults to `low` and supports up to `ultra`, `gpt-5.4` stops at `xhigh`.
   There is no `minimal`.
7. **The state DB lags a turn by milliseconds** — reading `/threadmeta` straight
   after `turn/start` can show stale or NULL values. Re-read.
8. **Command output is not replayed by the server** for legacy-history threads;
   only the live stream and the GUI's own cache have it. `thread/turns/list`
   returns ONLY `userMessage` / `agentMessage`, so "zero command items" in
   history does **not** mean nothing ran. Watch `/events` to see commands, and
   read `agentMessage.text` for results.
9. **`agentMessage` carries a `phase`**: `commentary` (thinking aloud) vs
   `final_answer`. Read the `final_answer` for the result; a reply that looks
   empty is usually you reading the wrong item or field.

## Hard rules

0. **Go through `bin/codex-run`.** It guarantees the GUI is up. If you find
   yourself typing `python3 bridge.py`, stop — the user cannot see that.
1. **Clean up threads you create.** Name throwaway probes `[test] …` so they are
   obvious, then remove them. Never delete a thread the user has typed into.
2. **Archive to tidy up, delete only to reclaim space.** `thread/archive` keeps
   the transcript and rollout; `thread/delete` **destroys the rollout JSONL**.
   The trail lives at `~/.codex/sessions/**/rollout-*.jsonl`.
3. **Never spawn a second app-server.** Doing so once rotated the user's shared
   OAuth refresh token. Attach to the running one, or use
   `codex app-server proxy`.
4. **Never install `claude`, never put it on PATH.** See above — this is a
   boundary, not an obstacle.
5. Default new threads to `sandbox:"workspace-write"`, `approvalPolicy:"never"`
   unless the user asks otherwise; raise to `on-request`/`untrusted` for
   anything destructive.

## Do not use the official desktop app

It cannot display threads created on a remote host and offers no way to start
one (openai/codex #27284, #22438, #24280). That is why this exists.
