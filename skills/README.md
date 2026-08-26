# Drop-in skills

`bridge.py` registers this directory as a skill root at startup, so anything
you put here becomes available to the agent without further configuration.
Each skill is a folder containing a `SKILL.md`; the agent namespaces them by
the parent directory name.

Extra roots outside this repo can be added in `state/skill-roots.json` or via
`$CODEX_SKILL_ROOTS` (colon-separated).

Skill folders themselves are gitignored — what you drop here is yours, and is
not part of the tool.
