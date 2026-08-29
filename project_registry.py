"""Project identity sourced from Claude's own local project registry."""

import json
import os
import glob


CLAUDE_CONFIG = os.path.expanduser("~/.claude.json")
CLAUDE_SESSIONS_DIR = os.path.expanduser(
    "~/Library/Application Support/Claude/claude-code-sessions")


def claude_project_roots(config_path=None):
    """Return normalized roots registered by Claude Code."""
    path = config_path or CLAUDE_CONFIG
    try:
        with open(path, encoding="utf-8") as f:
            projects = (json.load(f) or {}).get("projects") or {}
    except Exception:
        return []
    if not isinstance(projects, dict):
        return []
    return sorted({os.path.abspath(root) for root in projects if root})


def project_for_path(path, roots=None, config_path=None):
    """Map a path to Claude's longest registered ancestor project."""
    if not path:
        return None
    target = os.path.abspath(path)
    candidates = []
    for root in roots if roots is not None else claude_project_roots(config_path):
        root = os.path.abspath(root)
        if target == root or target.startswith(root.rstrip(os.sep) + os.sep):
            candidates.append(root)
    return max(candidates, key=len) if candidates else None


def project_label(root):
    """Claude's local project list displays the registered root's basename."""
    return os.path.basename((root or "").rstrip(os.sep)) or "codex"


def claude_chat_projects(roots=None, session_dir=None):
    """Map persisted Claude session ids to their registered project roots."""
    base = session_dir or CLAUDE_SESSIONS_DIR
    found = {}
    for path in glob.glob(os.path.join(base, "*", "*", "*.json")):
        if os.path.basename(path).startswith("deleted_"):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                session = json.load(f)
        except Exception:
            continue
        origin = session.get("originCwd") or session.get("cwd")
        root = project_for_path(origin, roots=roots)
        if not root:
            continue
        for session_id in (session.get("sessionId"), session.get("cliSessionId")):
            if session_id:
                found[session_id] = root
    return found


def project_for_chat(chat_id, roots=None, session_dir=None):
    """Resolve a Claude chat's project from its persisted session origin."""
    if not chat_id:
        return None
    return claude_chat_projects(roots=roots, session_dir=session_dir).get(chat_id)


def rollout_creation_cwd(path):
    """Read the immutable creation cwd from a Codex rollout."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if record.get("type") != "session_meta":
                    continue
                cwd = (record.get("payload") or {}).get("cwd")
                return os.path.abspath(cwd) if cwd else None
    except OSError:
        return None
    return None


def reconcile_projects(registry, records, config_path=None, session_dir=None):
    """Repair thread pins from rollout creation paths and Claude project roots.

    Existing chat identity fields are preserved. An existing custom label is
    preserved only when it already belongs to the authoritative project root.
    """
    registry.setdefault("threads", {})
    registry.setdefault("roots", {})
    registry.setdefault("chats", {})
    changed = False
    roots = claude_project_roots(config_path)
    root_set = set(roots)
    chat_projects = claude_chat_projects(roots=roots, session_dir=session_dir)
    for record in records:
        tid = record.get("id")
        old = registry["threads"].get(tid) or {}
        creation = rollout_creation_cwd(
            record.get("rollout_path") or record.get("path"))
        # A CLI-created thread belongs to its driving Claude chat even when its
        # sandbox cwd points into another checkout. UI-created threads have no
        # chat binding, so their immutable rollout creation path is the source.
        root = chat_projects.get(old.get("chat_id"))
        if not root and old.get("root") in root_set:
            root = old["root"]
        if not root:
            root = project_for_path(creation, roots=roots)
        if not tid or not root:
            continue
        name = registry["roots"].get(root) or project_label(root)
        entry = {**old, "root": root, "name": name}
        if entry != old:
            registry["threads"][tid] = entry
            changed = True
        if root not in registry["roots"]:
            registry["roots"][root] = name
            changed = True
    return changed
