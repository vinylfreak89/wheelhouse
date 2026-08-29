# codex-run — incidents behind the rules

**Contains:** the post-mortems for rules stated in `./SKILL.md` — what each one
cost the first time it was not followed. **Does NOT contain:** any rule you are
required to obey. Every rule lives in `./SKILL.md`, and reading this file is
never a substitute for reading that one. Open this when a rule looks excessive,
or when you are about to make an exception.

Each entry names the rule it belongs to and then gives the evidence, verbatim as
it was recorded.

---

## A restart destroyed a live turn, unrecoverably

*Rule: "Restarting the app kills running turns".*

Observed: a user's message was accepted, started a turn, and was destroyed six
seconds later by a restart; the text was never persisted anywhere recoverable
(`task_started` and `turn_aborted` in the rollout, no `user_message` record,
nothing in the queue DB).

UI changes never need a restart: the bridge serves `ui/index.html` fresh on
every request.

---

## A replay run was contaminated by a reachable reference

*Rule: "Blindness must be STRUCTURAL, never instructed". The reasoning is in
`./ORCHESTRATION.md`.*

Observed, and admitted by Codex unprompted when asked directly:

    "I also used Claude's recorded outputs to reconstruct expected diffs too
     early. Most clearly, when regeneration failed, I mechanically applied line
     changes known from Claude's result ... That contaminates the replay."

The dispatch had said to reconstruct the reference "for comparison" while
handing over the transcript that contained it. The resulting convergence figures
measured copying, not re-derivation, and were reported as a result before anyone
checked.

---

## A dispatch without its job id returned correct-looking zeros

*Rule: "Orchestrating well — Codex cannot see your context", requirement 2.*

Observed failure: dispatching "run the Filter B evaluator stage" produced correct
stage names and zeros, because `b2_evaluator` is stage 7 of a pipeline and no job
id was given. Codex behaved reasonably; the instruction was incomplete.

---

## Three ways an unlocked shared checkout went wrong

*Rule: "Take the tree lock before writing a shared checkout".*

Both sides write the same working tree: you edit files directly, and a Codex
turn edits them from inside. Nothing stops the two interleaving, and the damage
is quiet rather than loud:

* `git add` sweeps up whatever the other side left modified. An approvals fix
  once landed inside a commit titled "ui: test and refresh turn parameters",
  authored by neither party as a unit.
* Reloading the UI mid-write serves half-written JS, because the bridge reads
  `index.html` from disk with `Cache-Control: no-store`.
* Two `git commit` calls racing produce a history nobody can attribute.

**It is advisory.** Nothing in the filesystem enforces it; it works only because
both sides check.

If both parties are going to work concurrently and often, the real fix is
separate worktrees, not a tighter lock.

---

## Every read worked; the first write failed

*Rule: "Writes are scoped to the turn's cwd — reads are not".*

This asymmetry is vicious: a task that reads its inputs happily will run for a
while and then die on the first write with

    fatal: Unable to create '<path>/.git/index.lock': Operation not permitted

Observed: a replay job was pointed at a sandbox under the tool's own directory
while its thread's cwd was a different project. Every read worked;
the first `git` operation failed.

---

## `--cwd` used to relocate the whole conversation

*Rule: "`--cwd` moves the writes, NOT the conversation".*

Codex persists a turn's `cwd` into `threads.cwd`. The sidebar used to group on
that column, so one `--cwd` into a scratch directory silently relocated the
whole conversation out of its project — and `find_thread` matched on cwd too,
so the next `codex-run new` could fail to find the thread and split the chat
across two.

That is what the project registry (`<repo>/state/projects.json`) exists to
prevent.

---

## A rename used to orphan the thread

*Rule: "Thread names are cosmetic — resolution is not".*

This was not always true: resolution used to match the display name against the
chat title, so a single rename would orphan the thread and the next `new` would
quietly open a second one.

---

## Nothing was watching, so nothing was noticed

*Rule: "Never leave work unhooked", and the protocol's step 5.*

**Hook every dispatch before you move on.** A turn that ends with unhooked
background work means you find out it finished by remembering to poll -- and
you will forget. Codex went idle for a full exchange once while the driver
kept saying "still running", because nothing was watching it.

---

## A blanket `xhigh` burned 92% of a 5-hour window

*Rule: "Effort is a budget decision, not a default".*

When you do override, match the tier to the work — a blanket `xhigh` burned
92% of a 5-hour window on one transcript-reading pass.

Model, effort and approval policy are omitted from `turn/start` when you do not
pass them, so the server uses whatever the thread is set to — which is what the
owner sees in the UI and may have deliberately changed. Sending `effort` on
every turn silently reverted their choice on the next dispatch, so only send it
when you have a reason. `new` still applies `CODEX_EFFORT` (default `xhigh`)
when CREATING a thread, because a new thread has no prior state to inherit.

---

## A tool's "headless" runner tried to call `claude`

*Rule: "Anything that shells out to `claude` will fail — and must keep
failing", in "Speaking Codex's language".*

Tools with a pluggable runner will happily choose a "headless" mode that invokes
it and die with:

    RuntimeError: headless call error: Not logged in · Please run /login

---

## Restarting a staged job discards work

*Rule: "Driving a staged pipeline", step 1.*

Starting a fresh job silently discards work and, for gates with cumulative
filters, corrupts the history. Isolation is usually the point of the per-stage
sub-agent, so do not answer bootstrap prompts inline when the tool expects one.

## A `never` thread committed nothing and said nothing

*Rule: Hard rule 5.*

A dispatch whose job was to `git commit` went to an existing thread created
under the CLI's old hardcoded `approvalPolicy:"never"`. Under
`sandbox:"workspace-write"`, `.git` writes are denied; under `never` there is no
escalation path, so `git add` failed with `Unable to create '.git/index.lock':
Operation not permitted` and the turn ended having committed nothing — the
failure surfaced only as a sandbox error handed back to the agent, easy to miss.
The CLI default has since moved to `auto-review` (`on-request`), but existing
threads keep whatever they were born with, and `send`/`task` cannot change an
existing thread's mode. Before dispatching commit/git work to an existing
thread, check `codex-run info <id>`; a legacy `never` thread must be raised in
the UI or replaced with a fresh one. And do NOT "fix" the symptom by touching
the repo's filesystem — stripping `.git`'s sandbox ACL / xattrs (`com.apple.macl`)
is out of scope, doesn't close the approval gap, and just corrupts the sandbox
metadata (restore it from a backup if you already did).
