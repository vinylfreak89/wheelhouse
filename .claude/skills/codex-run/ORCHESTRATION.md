# codex-run — the reasoning behind the orchestration rules

**Contains:** why the dispatch, blindness and spec-and-results rules in
`./SKILL.md` are shaped the way they are. **Does NOT contain:** the rules
themselves, or anything you must have before acting — those are all in
`./SKILL.md`. Open this when a rule looks arbitrary, or when you are designing an
experiment or a spec-and-results exchange and want the reasoning behind the
protocol.

---

## Why a dispatch must not restate what Codex already loads

*Rule: "Do NOT restate what Codex already loads".*

Repeating it wastes tokens on every turn AND dilutes the actual instruction —
the one novel thing you are asking for competes with 800 lines it already knows.

---

## Why the dispatch contract is so explicit

*Rule: "Orchestrating well — Codex cannot see your context".*

You are the orchestrator; Codex is a separate agent with **none** of your
conversation, your files-in-mind, or your assumptions. An instruction that feels
complete to you is usually underspecified to it.

---

## Why blindness fails when it is merely instructed

*Rule: "Blindness must be STRUCTURAL, never instructed".*

If a task requires an agent to derive something independently and only THEN be
compared against a reference answer, you cannot achieve that by telling it not
to look. Access is capability: if the reference is reachable, it will leak into
the derivation — usually at the moment the agent gets stuck, which is exactly
when contamination matters most.

**A contaminated run is worse than a failed one: it produces confident numbers
that mean nothing.**

`./INCIDENTS.md` records the run where this happened, in Codex's own words.

---

## Why the spec-and-results loop exists

*Rule: "The spec-and-results loop (when Codex designs, you execute)".*

The strongest use of a second agent is not "do this task" but **it designs, you
run, it interprets** — because the two of you have different access. Codex can
read material you must not; you have a warm harness and can run long jobs in the
background. Handing work across that boundary needs a protocol, or you spend the
turn steering.

Failures observed building this loop, each of which the protocol below prevents:

- **Roles stated too late.** A brief that says "design AND run the experiment"
  gets Codex grinding through jobs that take minutes each, when it should have
  handed back a spec. State the division of labour in the FIRST message, not in
  a `steer`.
- **No machine-readable handoff.** Prose describing which spans to test has to
  be parsed by hand and transcribed, which is where errors enter. Demand a
  JSON artifact.
- **Runtimes unknown to Codex.** It cannot see how slow your tooling is. Tell it
  ("one classification at 5k tokens is ~3 min") or it will plan work it cannot
  finish inside a turn.
- **Blindness restated ad hoc.** If the reader must not learn the corpus, say so
  as a standing output contract in every brief, not as an afterthought.

("the protocol below" is the numbered protocol in `./SKILL.md`; the rule
sentences above are carried there too, because they are rules.)

---

## Why prespecification is the point

*Rule: the protocol's step 2, and "Ask for that discipline explicitly".*

You will otherwise select the thing that scored highest, sweep it, and discover
an effect that is partly regression to the mean. Codex will catch this if you
let it — in one exchange it excluded a span from its own candidate set on the
grounds that the span had been chosen as a maximum in earlier work, and
hash-stamped its spec file so the prespecification was verifiable after the
fact. Ask for that discipline explicitly; it makes a falsifiable result possible
rather than a persuasive one.

**Report failures faithfully.** A loop that only returns confirming evidence is
worse than no loop.

---

## Closing the loop: what the four parts are for

*Rule: "Close the loop yourself — settle it, then narrate".*

On falsifying with a decomposition: a measured `0.000e+00` on the factor it
blamed ends the discussion; a paragraph does not.

On separating the process criticism from the mechanism: an objection that you
failed to isolate two variables can be entirely correct while the confound it
names turns out to have zero magnitude. You were then right for inadequate
reasons, which is worth saying out loud.

On relaying disagreement upward: a summary of who was right about what is
strictly less useful than a settled answer plus the evidence.

---

## Expect to be corrected in return

Expect to be corrected in return, including on things you asserted while
conceding. A good counterpart also corrects ITS OWN earlier work: in one
exchange it withdrew two named sentences of its critique AND independently
tightened a validity gate it had specified itself, which retroactively weakened
its own falsification. That is the loop working.
