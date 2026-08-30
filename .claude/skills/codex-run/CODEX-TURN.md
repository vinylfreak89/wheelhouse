# Turn protocol (auto-injected by codex-run into every dispatched turn)

This turn was dispatched by an external orchestrator via `codex-run`. You and
the orchestrator are two agents sharing one checkout and one design doc. This
block is standing protocol, not part of the task text. It is the **canonical**
statement of the shared-checkout protocol: the orchestrator's skill links here
instead of restating it, so a protocol change is an edit to THIS file, nowhere
else.

- **Never invoke `codex-run`, `bridge.py`, or the app-server yourself.** Turn,
  bridge, and continuation management belong to the orchestrator; doing them
  from inside a turn is self-invocation against the bridge that owns your own
  app-server.
- **Shared model:** `AGENTS.md` in the project root is a symlink to `CLAUDE.md`
  — the living design doc both agents maintain. Durable findings, corrections,
  and decisions go there, not only in chat. **Never replace the symlink with a
  regular file** (atomic-write paths silently fork the instructions): edit the
  target, and verify the link is intact after any instruction change.
- **Tree lock** (advisory; it works because both sides check): `{LOCKFILE}`,
  JSON `{"owner","pid","note","since"}`, written atomically (tmp + rename).
  The dispatching process normally holds it for your whole turn (owner
  `codex:<thread-prefix>` — that hold is YOURS; do not re-acquire). If no live
  hold covers your turn (e.g. a queued turn), acquire it before writing tracked
  files and release it when done. A lock held by another LIVE owner is a hard
  stop: wait or report, never steal, never reason your edit is small enough.
- **Commits:** commit YOUR OWN work yourself, in logical units, as the work
  completes — never leave finished work uncommitted at turn end. Stage explicit
  paths only, never `git add -A` (the other agent may have in-flight edits);
  do not stage, revert, or rewrite files you did not author. End every commit
  message with exactly this trailer line, once (the upstream Codex standard):
  `Co-authored-by: Codex <noreply@openai.com>`
- **Deliverables live in the repo; scratch goes to /tmp.** Report failures
  verbatim — a falsifying result is a result.
