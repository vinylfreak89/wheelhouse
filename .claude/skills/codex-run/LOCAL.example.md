# Local codex-run overlay

Use `LOCAL.md` beside `SKILL.md` for operator- or project-specific instructions
that should not be published with Wheelhouse. The public skill reads it first
when it exists, and `.gitignore` prevents it from being staged accidentally.

Good overlay content includes:

- relationships between local instruction files, including symlink targets;
- private paths and directories that dispatched agents must not touch;
- temporary edit embargoes or approval boundaries;
- exact runner modes and entrypoints for private staged pipelines; and
- output restrictions for work whose source material must remain private.

Keep reusable Wheelhouse behavior in `SKILL.md`. Keep local project names,
paths, session titles, proprietary workflow details, and current private policy
in `LOCAL.md`.
