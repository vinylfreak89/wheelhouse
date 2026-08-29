import json
from pathlib import Path
import tempfile
import unittest

import sys


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import project_registry


class ClaudeProjectRegistryTests(unittest.TestCase):
    def test_longest_registered_ancestor_is_project_identity(self):
        roots = ["/work", "/work/tos-performance", "/work/other"]
        self.assertEqual(
            project_registry.project_for_path(
                "/work/tos-performance/ToS GPU render pipeline", roots=roots),
            "/work/tos-performance",
        )
        self.assertIsNone(project_registry.project_for_path(
            "/runtime/cwd", roots=roots))

    def test_reconcile_repairs_nested_tos_and_adds_blackmagic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tos = root / "tos-performance"
            blackmagic = root / "blackmagic-usb-mac"
            config = root / ".claude.json"
            config.write_text(json.dumps({
                "projects": {str(tos): {}, str(blackmagic): {}},
            }), encoding="utf-8")
            tos_rollout = root / "tos.jsonl"
            tos_rollout.write_text(json.dumps({
                "type": "session_meta",
                "payload": {"cwd": str(tos / "ToS GPU render pipeline")},
            }) + "\n", encoding="utf-8")
            blackmagic_rollout = root / "blackmagic.jsonl"
            blackmagic_rollout.write_text(json.dumps({
                "type": "session_meta", "payload": {"cwd": str(blackmagic)},
            }) + "\n", encoding="utf-8")
            registry = {
                "threads": {"tos": {
                    "root": str(tos / "ToS GPU render pipeline"),
                    "name": "ToS GPU render pipeline", "chat": "TOS",
                }},
                "roots": {}, "chats": {},
            }

            changed = project_registry.reconcile_projects(registry, [
                {"id": "tos", "rollout_path": str(tos_rollout)},
                {"id": "blackmagic", "rollout_path": str(blackmagic_rollout)},
            ], config_path=str(config))

            self.assertTrue(changed)
            self.assertEqual(registry["threads"]["tos"], {
                "root": str(tos), "name": "tos-performance", "chat": "TOS",
            })
            self.assertEqual(registry["threads"]["blackmagic"], {
                "root": str(blackmagic), "name": "blackmagic-usb-mac",
            })

    def test_rollout_creation_cwd_ignores_later_context_records(self):
        with tempfile.TemporaryDirectory() as temp:
            rollout = Path(temp) / "rollout.jsonl"
            rollout.write_text("\n".join([
                json.dumps({"type": "session_meta",
                            "payload": {"cwd": "/project/root"}}),
                json.dumps({"type": "turn_context",
                            "payload": {"cwd": "/later/runtime/cwd"}}),
            ]) + "\n", encoding="utf-8")
            self.assertEqual(project_registry.rollout_creation_cwd(rollout),
                             "/project/root")

    def test_chat_origin_beats_a_different_registered_runtime_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            owning = root / "option-automation"
            runtime = root / "codex-app"
            config = root / ".claude.json"
            config.write_text(json.dumps({
                "projects": {str(owning): {}, str(runtime): {}},
            }), encoding="utf-8")
            sessions = root / "sessions" / "a" / "b"
            sessions.mkdir(parents=True)
            (sessions / "session.json").write_text(json.dumps({
                "sessionId": "local-owner", "originCwd": str(owning),
            }), encoding="utf-8")
            rollout = root / "rollout.jsonl"
            rollout.write_text(json.dumps({
                "type": "session_meta", "payload": {"cwd": str(runtime)},
            }) + "\n", encoding="utf-8")
            registry = {"threads": {"t": {"chat_id": "local-owner"}},
                        "roots": {}, "chats": {}}

            project_registry.reconcile_projects(
                registry, [{"id": "t", "rollout_path": str(rollout)}],
                config_path=str(config), session_dir=str(root / "sessions"))

            self.assertEqual(registry["threads"]["t"]["root"], str(owning))
            self.assertEqual(registry["threads"]["t"]["name"],
                             "option-automation")


if __name__ == "__main__":
    unittest.main()
