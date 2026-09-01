---
name: codex-run
description: Hand work to OpenAI Codex from Claude and watch it run in the Wheelhouse.app GUI. Use whenever the user says "run this on codex", "give this to codex", wants a second agent to attempt something, or wants dangerous/large work done outside this session. Also covers driving, steering, and cleaning up Codex threads.
---

# Running work on Codex

> **Audience: the external orchestrator driving Codex — not Codex itself.**
> Codex's rules arrive with every dispatched turn as `./CODEX-TURN.md` — the
> canonical shared-checkout protocol; this file never restates it. A Codex
> agent handed THIS file anyway: your conduct rules are in CODEX-TURN.md (start
> with "never invoke `codex-run`"); the methodology here — structural
> blindness, prespecification, output contracts, cwd and write scoping — does
> also apply to you. See "Skill roots" for why this skill is deliberately not
> registered for Codex.

## Every rule, in one place

If you read nothing else, read this. Each line is a rule; the section named
after it holds the detail.

1. **Read `LOCAL.md` completely if it exists** — mandatory, more specific than
   this file, never copied into a dispatch. *(Load the local overlay first)*
2. **Obey the Hard rules** — 0 through 6, no exceptions. *(Hard rules)*
3. **Drive through `bin/codex-run`.** Never start `bridge.py` by hand.
   *(Use the CLI)*
4. **Take the tree lock before writing a shared checkout**, release after. A
   held lock is a hard stop, not a hint. *(Take the tree lock)*
5. **Hook every dispatch before moving on.** Use `watch "$TID"`, never
   `busy "$TID"` — `busy` is account-global. *(Never leave work unhooked)*
6. **Ensure the shared model EXISTS before the first dispatch into a project:
   `AGENTS.md` must be a symlink to `CLAUDE.md` — create it if missing.** Then
   do not restate what it already gives Codex. *(The shared model)*
7. **Every dispatch carries entrypoint, inputs, preconditions, output
   contract.** *(Orchestrating well)*
8. **Ask for status, counts and verbatim errors — never content — from a run
   you must not read.** *(Getting facts out of a run you must not read)*
9. **Codex cannot wait for anything.** Continuation is yours. *(Codex cannot
   wait)*
10. **Pass `--cwd` whenever the work writes outside the thread's directory.**
    Reads are not scoped; writes are. *(Writes are scoped)*
11. **Thread names are cosmetic; resolution is by chat id.** Renaming is safe.
    *(Thread names are cosmetic)*
12. **Turns inherit the thread's settings.** Override only deliberately, and
    match the tier to the work. *(Effort is a budget decision)*
13. **Never restart while a turn is running.** `reload` for UI changes.
    *(Restarting the app kills running turns)*
14. **This skill is deliberately not a registered Codex skill root.**
    *(Skill roots)*
15. **Raw protocol only when the CLI has no subcommand.** *(Raw protocol)*

Two more are mandatory ONLY in their modes, and are the last two sections:

16. **Blindness must be STRUCTURAL, never instructed** — before any independent
    derivation compared against a reference.
17. **The spec-and-results loop** — before any exchange where Codex designs and
    you execute.

## What to read, and when

**This file carries every rule. Nothing you are required to obey lives anywhere
else, so no link below has to be followed for you to act correctly.**

Sections are ordered by when you need them. Everything down to "Skill roots"
applies to **every** dispatch. The last two sections — **"Blindness must be
STRUCTURAL"** and **"The spec-and-results loop"** — are mandatory only in their
modes: read them before setting up an independent derivation, an experiment, or
a design exchange where Codex specifies and you execute.

Three companion files sit beside this one. They hold the *why* — the incident
behind each rule, the reasoning, and the reference tables — and are **optional
background**:

| file | holds | open it when |
|---|---|---|
| `./INCIDENTS.md` | the post-mortem behind each rule here — what it cost the first time it was not followed | a rule seems excessive, or you are tempted to make an exception |
| `./ORCHESTRATION.md` | the reasoning for two-agent work: why blindness fails when merely instructed, why prespecification is the point | you are designing an experiment or a spec-and-results exchange |
| `./PROTOCOL.md` | architecture, the raw JSON-RPC method table, thread/project resolution internals | the CLI has no subcommand for what you need, or you are changing the bridge |

The table above is on-demand. `LOCAL.md` is NOT: if it exists beside this
file it is mandatory and must be read completely before acting — see the
next section. An index that lists only the optional companions hides the
one companion that is not optional.

## Load the local overlay first

If `LOCAL.md` exists beside this file, read it completely before acting. It
contains operator- and project-specific additions that do not belong in the
public skill. Treat its instructions as more specific than this file when they
overlap.

`LOCAL.md` is deliberately gitignored. Never stage it, copy its contents into
the public skill, or include its private details in a dispatch unless the user
explicitly asks for that disclosure. `LOCAL.example.md` documents the supported
shape without carrying anyone's local policy.

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
4. **Never install `claude`, never put it on PATH.** See "Speaking Codex's
   language" — this is a boundary, not an obstacle.
5. New threads default to `sandbox:"workspace-write"` + the **`auto-review`**
   mode (`approvalPolicy:"on-request"`, `approvalsReviewer:"auto_review"`) — the
   CLI's default; `CODEX_APPROVALS` overrides it. Use `approvalPolicy:"never"`
   only when the user explicitly wants a fully autonomous, no-escalation thread.
   **Never use `never` for work that must write to `.git` (commits, tags,
   rebases):** under `sandbox:"workspace-write"` the sandbox denies `.git`
   writes, and `never` gives Codex no escalation path, so the commit fails
   *silently* (`Unable to create '.git/index.lock': Operation not permitted`).
   Any commit-capable dispatch needs `on-request`/`auto-review`. `send`/`task`
   cannot change an existing thread's mode, so check it first with
   `codex-run info <id>`; a legacy `never` thread (created before the CLI's
   default moved off `never`) must be raised in the UI or replaced with a fresh
   thread.
6. **Do not use the official desktop app.** It cannot drive these threads;
   `./PROTOCOL.md` has the upstream issues.

## Use the CLI — do not hand-roll the startup

    <repo>/bin/codex-run

**Always drive Codex through this.** The one exception is environmental, not
discretionary: with no app bundle present, or under `CODEX_HEADLESS=1`, the
CLI runs `bridge.py` itself and works with no window at all. That is the CLI
choosing headless for you — it is still not a licence to start `bridge.py`
by hand. Every subcommand preflights the GUI and
bridge for you. Starting `bridge.py` directly also "works" and is WRONG — it
leaves the user with no window to watch, which is the entire point of this
setup.

    codex-run say "do the thing"        # most common — auto-named from this chat
    codex-run up                        # just ensure GUI + bridge are live
    codex-run name                      # show the name that would be used
    codex-run new [cwd]                 # create a thread, print its id
    codex-run send <id> "<text>"        # send a turn, wait, print the reply
    codex-run steer <id> "<text>"       # type into a RUNNING turn
    codex-run list / info <id> / read <id>
    codex-run archive <id>              # tidy up, KEEPS the transcript
    codex-run rm <id>                   # delete permanently

The remaining subcommands are introduced below alongside the rule that requires
them: `lock` (tree lock), `busy` / `reload` / `restart` (restarting), `on-file`
and `queue` (continuation), `task` (effort, and writes outside the thread's
cwd), `project` and `rename` (thread resolution).

`cwd` defaults to the directory you are working in. Pass an explicit cwd only to
override.

**Names are automatic and should stay that way.** `codex-run` names a thread
after **this chat** — e.g. `API migration review`. No `[project]` prefix.
`new` takes no name at all: it is always the chat title, deliberately not
overridable. Only `say` accepts an explicit name
(`codex-run say "<name>" "<text>"`), for when one thread per chat is not enough.

Requires `codex` on PATH (brew cask), `python3`, and `~/.codex/auth.json`.
Never start a second `codex app-server` by hand.

## Take the tree lock before writing a shared checkout

    codex-run lock status                    # this root: who holds it, since when
    codex-run lock status --all              # every held root
    codex-run lock acquire claude "why"      # take it before you edit
    codex-run lock release
    codex-run lock ... --root DIR            # act on another checkout's lock

**Locks are scoped per working root** (the git toplevel containing the path, or
the path itself outside a repo). Until 2026-09-01 there was one global lock, so
a turn in one checkout blocked an unrelated one; the registry is still a single
directory (`state/tree-locks/`, hence `--all`) but the exclusion is per root.
That scoping is a precondition for ever making contention refuse loudly rather
than warn-and-proceed: globally, refusing would serialize every project on the
account.

`codex-run task` AND `codex-run send` take the lock for the DURATION of the
turn automatically and release it in a `finally` (`send` historically did not —
send-dispatched turns ran with nobody holding the lock). The root locked is the
turn's own: its `--cwd` when given, else the thread's cwd. If the lock is
already held they warn and proceed rather than blocking — set `LOCK_WAIT=300`
to wait instead. Queued turns run with no live dispatcher process, so no lock is held
for them; `CODEX-TURN.md` tells Codex to acquire it itself in that case.

The shared mechanics — lockfile path and format, hard-stop-on-live-holds,
narrow staging, commit conventions — are stated ONCE, in `./CODEX-TURN.md`
(injected into every turn), and bind BOTH agents. This section covers only the
orchestrator's CLI operations on top of them.

**Staleness has two forms, and conflating them breaks it.** A turn holds the
lock inside a long-lived process, so a dead pid means abandoned. A manual
`acquire` returns to the shell immediately, so its pid is gone by the next
command — that is normal, not stale. Manual holds record no pid and expire on
age (`LOCK_MAX_AGE`, default 3600s).

*Why: `./INCIDENTS.md`, "Three ways an unlocked shared checkout went wrong".*

## Never leave work unhooked

Both halves of the loop run in the background, and both need a hook:

    # Codex: wait for the turn, then surface the artifact it wrote
    i=0; while codex-run watch "$TID" 30 | grep -q "still running" \
                && [ "$i" -lt 60 ]; do i=$((i+1)); done

    # your own long runs: wait on the PROCESS, not a fixed sleep
    i=0; while pgrep -f myrun.py >/dev/null && [ "$i" -lt 240 ]; do
           sleep 60; i=$((i+1)); done

**`codex-run busy` is account-GLOBAL and ignores any thread id you pass it.**
It lists every active thread and prints `idle` only when the whole account
is idle, so `busy "$TID"` waits on other people's turns as well as yours.
Use `watch "$TID"` when you mean one thread.

Launch those with Bash `run_in_background: true` so their exit fires a task
notification. Both forms cap their iterations: an unbounded wait on a job that
died silently hangs until the turn ends.

Prefer ONE hook covering several jobs when they are serialised behind each
other -- one notification when the queue drains beats four interleaved ones.
`codex-run on-file <path> <id> <taskfile>` is the stronger form when the
next step is itself a dispatch: it waits for a file to appear AND stop changing,
waits out any active turn, then sends the follow-up without you in the loop.

*Why: `./INCIDENTS.md`, "Nothing was watching, so nothing was noticed".*

## The shared model — AGENTS.md must exist, as a symlink to CLAUDE.md

Codex reads `AGENTS.md` automatically; Claude reads `CLAUDE.md`. The two agents
can only work off a shared model if those are the SAME FILE. Therefore, **before
the first dispatch into any project, check that `AGENTS.md` exists**:

    ls -la <project>/AGENTS.md || (cd <project> && ln -s CLAUDE.md AGENTS.md)

- **If it is missing, create it as a symlink to `CLAUDE.md` — always a symlink,
  never a separate authored file.** A bespoke `AGENTS.md` forks the instructions
  into two documents that drift; the symlink keeps one source of truth.
- **If a regular (non-symlink) `AGENTS.md` already exists, STOP and surface it
  to the owner** — replacing an owner's file is not your call.
- The standing invariant — never replace the symlink, verify it survives
  instruction edits — is stated in `./CODEX-TURN.md` and binds both agents.

**The empty case is the trap that motivated this rule:** a repo WITHOUT
`AGENTS.md` gives Codex zero standing instructions, while the orchestrator —
obeying "don't restate what AGENTS.md provides" — sends none either. Codex then
runs an entire project with no lock discipline, no commit conventions, and no
trailers, and every symptom gets patched ad hoc in dispatch text.

Every dispatched turn additionally carries `./CODEX-TURN.md` (auto-injected by
the CLI in `send`/`task`/`queue`/`say`; `CODEX_NO_PROTOCOL=1` disables). That
file is the **canonical shared-checkout protocol** — do not restate its
contents here or in dispatches; change the protocol by editing it. The CLI
substitutes the lockfile path at dispatch time. The commit trailer in it is the
**fixed upstream Codex standard, verbatim** —
`Co-authored-by: Codex <noreply@openai.com>` — deliberately WITHOUT a model id:
that matches openai/codex's own git-attribution ext (source-verified), whose
dedupe rule ("do not duplicate this trailer") makes our injected line compose
safely with native attribution when Codex's server-side policy enables it. Do
not add a model id: Codex cannot state its own model, and deriving it
dispatcher-side is unreliable (thread settings can change mid-turn; queued
turns bake stale values). Keep the file terse (it costs tokens on every turn)
and Codex-facing (never orchestrator driving instructions — see "Skill
roots").

## Do NOT restate what Codex already loads

Everything in `AGENTS.md` is already in context: project conventions, hard
rules, directory layout, and scoped guidance.

So a dispatch must NOT re-state:
  * project conventions or coding standards
  * sandbox etiquette or other instructions already in `AGENTS.md`
  * background about the project

A dispatch should carry ONLY what is genuinely new to this task:
  1. the goal
  2. the specific inputs (job id, path, commit)
  3. anything that CONTRADICTS or narrows the standing instructions
  4. the output contract

Any current embargoes or special relationships between local instruction files
belong in `LOCAL.md`, not here. (Instruction-file symlink handling is in
`./CODEX-TURN.md` and "The shared model" above.)

## Orchestrating well — Codex cannot see your context

Every dispatch must carry:

1. **The exact entrypoint** — script path and subcommand, not "run the gate".
2. **Which inputs** — the specific job id, file, or commit. A pipeline stage
   named without its job will make Codex guess, and it will guess wrong.
3. **Preconditions** — what must already exist, and what to do if it does not
   (fail loudly, rather than invent a path).
4. **The output contract** — exactly what the final message must contain. If the
   work touches private material, say explicitly that the reply may carry status,
   counts, and tooling errors ONLY, never content.

When a run comes back empty or wrong, re-read the dispatch before blaming the
model.

## Getting facts out of a run you must not read

Sometimes Codex must read material Claude is forbidden to see. Do not read the
thread. Instead:

1. Put a strict OUTPUT CONTRACT in the dispatch naming exactly what may appear.
2. Ask for the answer on a single machine-readable line, e.g.
   `RECOVERY_POINT=<iso8601> COMMIT=<sha>` — a fact, not prose.
3. Extract with a regex for that line only; never print surrounding text.

## Codex cannot wait for anything — continuation is YOUR job

There is no wait-for-trigger primitive. A turn runs to completion and stops. Do
not write a dispatch that says "wait until X appears then continue".

Two mechanisms, both driven from outside:

    codex-run on-file <path> <id> <taskfile>    # wait for a file, then dispatch
    codex-run queue <id> "<text>"               # queue a turn to run after this one

`on-file` waits for the path to exist AND stop changing size (three stable
polls) before dispatching, so a half-written file is never consumed, and it
waits for any in-flight turn to finish rather than colliding with it.

For anything more complex — a condition, an external event, a schedule — the
orchestrator polls and dispatches. Split the work into phases that each end in
a written artifact, and gate the next phase on that artifact appearing.

## Writes are scoped to the turn's cwd — reads are not

Under `sandbox: "workspace-write"` Codex may READ almost anywhere but may only
WRITE beneath the turn's `cwd` (plus /tmp).

**If the work writes anywhere outside the thread's own directory, pass that
directory as the turn's cwd:**

    codex-run task <id> <file> --effort high --cwd /path/to/sandbox

Use the turn-level override, so one chat keeps one thread while still writing
into a sandbox.

*Why, and the error it produces: `./INCIDENTS.md`, "Every read worked; the
first write failed".*

**`.git` sits inside the workspace but is still write-denied under
`workspace-write`,** so a `git commit` needs `approvalPolicy:"on-request"`
(auto-review) to escalate — never `approvalPolicy:"never"`, under which the
commit fails silently. See Hard rule 5 and `./INCIDENTS.md`, "A `never` thread
committed nothing and said nothing".

### `--cwd` moves the writes, NOT the conversation

A thread's project comes from **Claude's registered project root and the
driving chat's persisted origin**, never from Codex's runtime `cwd`. A turn can
work in another checkout without moving the conversation. UI-created threads
without a Claude chat binding are resolved from their immutable rollout
creation path to the longest registered Claude project ancestor.

    codex-run project              # name, root, and this chat's pinned thread
    codex-run project --name NAME  # override (two checkouts, same basename)
    codex-run project --repair     # pin legacy threads from immutable rollout metadata

### Thread names are cosmetic — resolution is not

`codex-run rename [<id>] "<name>"` retitles a thread. It is safe to do at any
time, including on a thread mid-turn.

If you touch that code path, keep the property and test it by exercising it —
rename, re-resolve, assert the id is unchanged.

The displayed project name is an independent registry field. The authoritative
root comes from Claude's project registry; its basename is the initial label,
and `project --name` can override that label. Never derive either project
identity or its default name from a turn's mutable `cwd`.

## Effort is a budget decision, not a default

**A turn inherits the thread's CURRENT settings unless you override them.** Only
send `effort` when you have a reason. `codex-run` prints the effective settings
at dispatch, marking whether the effort came from the thread or from you.

`new` still applies `CODEX_EFFORT` (default `xhigh`) when CREATING a thread.

When you do override, **match the tier to the work**:

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

## Restarting the app kills running turns

The bridge owns `codex app-server` as a CHILD process. Killing the bridge — or
the app — aborts any turn in flight, unrecoverably.

    codex-run reload      # UI changes — hot-reloads the page, no turn loss
    codex-run busy        # exits non-zero if any thread is mid-turn
    codex-run restart     # REFUSES while a turn is active

UI changes never need a restart. Only Swift shell changes require relaunching
the app.

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

If a tool offers a runner that invokes `claude`, select a mode in which Codex
performs the model stages itself, using the tool's supported handoff interface.
Put exact runner names and private pipeline instructions in `LOCAL.md`.

### Driving a staged pipeline (the shape that works)

Mirror how Claude runs it: resume, then step in order, one sub-agent per stage.

1. **Resume, do not restart.** Ask the tool for existing state first
   (`status`, or list its job dir). If a job exists, continue from the first
   stage with no recorded output.
2. **Run stages in the tool's declared order** — take the order from the tool
   itself (e.g. `--stage` choices in `--help`), never from memory.
3. **Per stage:** `bootstrap` the prompt → spawn a sub-agent to answer it →
   `submit` the reply. Do not answer bootstrap prompts inline if the tool
   expects an isolated agent; isolation is usually the point.
4. **Stop on the first stage error** and report which stage and the verbatim
   tool error. Do not skip ahead.

## Skill roots — this skill is deliberately not one of them

`bridge.py` registers skill roots with the app-server at startup
(`skills/extraRoots/set`): `<repo>/skills`, plus anything in
`state/skill-roots.json` or `$CODEX_SKILL_ROOTS`.

**`.claude/skills` is deliberately NOT among them, and must not be added.** This
skill teaches an agent how to drive Codex — `up`, `restart`, `task` — so loading
it into Codex's own context invites self-invocation against the bridge that owns
its app-server. The omission is the design, not an oversight.

## Raw protocol (escape hatch)

Only when the CLI has no subcommand for what you need. Still run
`codex-run up` first so the GUI exists.

    curl -s -XPOST 127.0.0.1:8770/rpc -H 'Content-Type: application/json' \
      -d '{"method":"thread/list","params":{"limit":10}}'

**The method table and payload shapes are in `./PROTOCOL.md` — read it before
hand-rolling RPC.**

---

*The two sections below are mandatory only in the modes they name.*

## Blindness must be STRUCTURAL, never instructed

When a task requires an agent to derive something independently and only THEN be
compared against a reference answer, telling it not to look does not work.
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

Use this structural separation whenever "independent derivation" matters.

*Why instructing blindness does not work: `./ORCHESTRATION.md`.*

## The spec-and-results loop (when Codex designs, you execute)

1. **You brief** with roles explicit: who reads what, who executes, who
   interprets. Include the tooling entry points, the runtime per unit of work,
   and the output contract. State the division of labour in the FIRST message,
   not in a `steer`. If the reader must not learn the corpus, say so as a
   standing output contract in every brief, not as an afterthought.
2. **Codex returns two artifacts**: a machine-readable spec (`spec.json` — ids,
   file paths, line ranges, arms, parameters) and a prose report containing its
   reasoning and, critically, its **prespecified analysis**: the contrast it
   will compute and the evidence thresholds it will judge by, fixed BEFORE any
   data exists. Ask for that discipline explicitly, and for a hash-stamped spec.
3. **You execute** the spec verbatim and return a results table **keyed by the
   spec's ids**. Do not reinterpret the design while running it; if the spec is
   unrunnable, say so and ask, because silently substituting your own version
   destroys the prespecification.
4. **Codex interprets** against its own fixed thresholds.
5. **Hook every dispatch before you move on.**

**Report failures faithfully.** When the run falsifies the hypothesis you handed
over, hand back the numbers that falsify it, plainly labelled.

*Why this loop exists, the failures that shaped it, and why prespecification is
the point: `./ORCHESTRATION.md`.*

### Close the loop yourself — settle it, then narrate

When the other agent's critique conflicts with your evidence, do not hand the
user a choice between two positions. Run the experiment that decides it and
send the result back. Keep the user informed of the exchange and its outcome;
what they should not have to do is adjudicate.

The exchange that works has four parts:

1. **Concede what actually lands, by name.** Quote the specific claim you are
   accepting rather than gesturing at "good points".
2. **Falsify with a decomposition, not an argument.** If the critique says two
   factors were confounded, vary them one at a time.
3. **Separate the process criticism from the mechanism.** These come apart, and
   the distinction matters.
4. **Ask questions that can be ANSWERED, not agreed with.** "Does this change
   your conclusion?" invites assent. "Under your own standard, what concrete
   test would make this creditable, in a form I can run?" produces a design.

Expect to be corrected in return, including on things you asserted while
conceding.

**Do not relay a disagreement upward as a decision.** The user asked for the
two agents to fight it out and reach agreement.
