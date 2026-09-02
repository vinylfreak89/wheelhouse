import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[1]


def load_codex_run():
    path = REPO / "bin" / "codex-run"
    loader = importlib.machinery.SourceFileLoader("wheelhouse_codex_run", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


codex_run = load_codex_run()


class ChatIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sessions = Path(self.temp.name) / "sessions"
        self.sessions.mkdir()
        self.sessions_patch = mock.patch.object(
            codex_run, "CLAUDE_SESSIONS_DIR", str(self.sessions))
        self.sessions_patch.start()

    def tearDown(self):
        self.sessions_patch.stop()
        self.temp.cleanup()

    def write_session(self, name, **values):
        path = self.sessions / "account" / "project" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values), encoding="utf-8")

    def test_environment_session_id_beats_more_recent_same_cwd_chat(self):
        cwd = os.getcwd()
        self.write_session(
            "selected",
            sessionId="local-selected",
            cliSessionId="cli-selected",
            title="Selected chat",
            cwd=cwd,
            lastActivityAt="2026-08-20T00:00:00Z",
        )
        self.write_session(
            "newer",
            sessionId="local-newer",
            cliSessionId="cli-newer",
            title="Wrong newer chat",
            cwd=cwd,
            lastActivityAt="2026-08-27T00:00:00Z",
        )
        with mock.patch.dict(os.environ, {
            "CLAUDE_CODE_SESSION_ID": "cli-selected",
            "CLAUDE_CODE_HOST_SESSION_ID": "local-selected",
        }):
            self.assertEqual(
                codex_run.chat_identity(),
                ("local-selected", "Selected chat"),
            )

    def test_terminal_fallback_uses_timestamp_not_equal_length_strings(self):
        cwd = os.getcwd()
        self.write_session(
            "older",
            sessionId="local-old",
            title="Older chat",
            cwd=cwd,
            lastActivityAt="2026-08-20T00:00:00Z",
        )
        self.write_session(
            "newer",
            sessionId="local-new",
            title="Newer chat",
            cwd=cwd,
            lastActivityAt="2026-08-27T00:00:00Z",
        )
        with mock.patch.dict(os.environ, {
            "CLAUDE_CODE_SESSION_ID": "",
            "CLAUDE_CODE_HOST_SESSION_ID": "",
        }):
            self.assertEqual(
                codex_run.chat_identity(),
                ("local-new", "Newer chat"),
            )


class ThreadResolutionTests(unittest.TestCase):
    def setUp(self):
        self.cwd = os.path.abspath(os.getcwd())
        # Thread-resolution fixtures bind their fake records to this test cwd.
        # Do not let a real driving Claude session substitute its own project
        # root and make otherwise deterministic unit tests environment-dependent.
        self.project_patch = mock.patch.object(
            codex_run, "claude_project", return_value=self.cwd)
        self.project_patch.start()
        self.live = [
            {"id": "wrong", "name": "Same title", "cwd": self.cwd,
             "updatedAt": 20},
            {"id": "right", "name": "Renamed by owner", "cwd": self.cwd,
             "updatedAt": 10},
        ]

    def tearDown(self):
        self.project_patch.stop()

    def test_stable_chat_binding_wins_over_title_and_recency(self):
        registry = {"threads": {
            "wrong": {"root": self.cwd, "name": "project",
                      "chat": "Same title", "chat_id": "local-wrong"},
            "right": {"root": self.cwd, "name": "project",
                      "chat": "Same title", "chat_id": "local-right"},
        }, "roots": {}, "chats": {"local-right": "right"}}
        with mock.patch.object(codex_run, "chat_identity",
                               return_value=("local-right", "Same title")), \
             mock.patch.object(codex_run, "_registry", return_value=registry), \
             mock.patch.object(codex_run, "rpc",
                               return_value={"data": self.live}):
            self.assertEqual(codex_run.find_thread("Same title"), "right")

    def test_chat_binding_resolves_from_a_foreign_cwd(self):
        """The chat is the identity; the directory the CLI runs from is not.

        Regression: resolution filtered on root BEFORE matching chat_id, so a
        call from anywhere else -- the tool's own bin/, a sibling project, /tmp
        -- matched nothing, and reuse-or-create then started a SECOND thread
        for a chat that already had one.
        """
        registry = {"threads": {
            "right": {"root": self.cwd, "name": "project",
                      "chat": "Same title", "chat_id": "local-right"},
        }, "roots": {}, "chats": {"local-right": "right"}}
        with mock.patch.object(codex_run, "chat_identity",
                               return_value=("local-right", "Same title")), \
             mock.patch.object(codex_run, "_registry", return_value=registry), \
             mock.patch.object(codex_run, "rpc",
                               return_value={"data": self.live}):
            self.assertEqual(
                codex_run.find_thread("Same title", cwd="/somewhere/else"),
                "right")

    def test_canonical_chat_thread_wins_even_when_cwd_matches_a_duplicate(self):
        registry = {"threads": {
            "wrong": {"root": self.cwd, "name": "project",
                      "chat": "Same title", "chat_id": "local-right"},
            "right": {"root": "/original/project", "name": "project",
                      "chat": "Same title", "chat_id": "local-right"},
        }, "roots": {}, "chats": {"local-right": "right"}}
        with mock.patch.object(codex_run, "chat_identity",
                               return_value=("local-right", "Same title")), \
             mock.patch.object(codex_run, "_registry", return_value=registry), \
             mock.patch.object(codex_run, "rpc",
                               return_value={"data": self.live}):
            self.assertEqual(codex_run.find_thread("Same title"), "right")

    def test_legacy_duplicate_fallback_chooses_oldest_not_matching_cwd(self):
        live = [
            {"id": "older", "name": "Same title", "cwd": "/original",
             "createdAt": 10, "updatedAt": 10},
            {"id": "newer", "name": "Same title", "cwd": self.cwd,
             "createdAt": 20, "updatedAt": 20},
        ]
        registry = {"threads": {
            "older": {"root": "/original", "name": "project",
                       "chat": "Same title", "chat_id": "local-right"},
            "newer": {"root": self.cwd, "name": "project",
                       "chat": "Same title", "chat_id": "local-right"},
        }, "roots": {}, "chats": {}}
        with mock.patch.object(codex_run, "chat_identity",
                               return_value=("local-right", "Same title")), \
             mock.patch.object(codex_run, "_registry", return_value=registry), \
             mock.patch.object(codex_run, "rpc", return_value={"data": live}):
            self.assertEqual(codex_run.find_thread("Same title"), "older")

    def test_legacy_title_binding_remains_resolvable(self):
        registry = {"threads": {
            "right": {"root": self.cwd, "name": "project",
                      "chat": "Same title"},
        }, "roots": {}}
        with mock.patch.object(codex_run, "chat_identity",
                               return_value=("local-right", "Same title")), \
             mock.patch.object(codex_run, "_registry", return_value=registry), \
             mock.patch.object(codex_run, "rpc",
                               return_value={"data": self.live}):
            self.assertEqual(codex_run.find_thread("Same title"), "right")

    def test_explicit_name_stays_distinct_before_session_json_is_flushed(self):
        explicit = {
            "id": "explicit",
            "name": "Scratch thread",
            "cwd": self.cwd,
            "updatedAt": "2026-08-27T01:00:00Z",
        }
        registry = {"threads": {
            "explicit": {"root": self.cwd, "name": "project",
                         "chat": "", "chat_id": "local-right"},
        }, "roots": {}}
        with mock.patch.object(codex_run, "chat_identity",
                               return_value=("local-right", None)), \
             mock.patch.object(codex_run, "_registry", return_value=registry), \
             mock.patch.object(codex_run, "rpc",
                               return_value={"data": [explicit]}):
            self.assertEqual(
                codex_run.find_thread("Scratch thread"),
                "explicit",
            )

    def test_bind_project_lazily_adds_stable_chat_id(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "projects.json"
            registry_path.write_text(json.dumps({
                "threads": {
                    "right": {"root": self.cwd, "name": "project",
                              "chat": "Same title"},
                },
                "roots": {self.cwd: "project"},
            }), encoding="utf-8")
            with mock.patch.object(codex_run, "REGISTRY", str(registry_path)), \
                 mock.patch.object(codex_run, "claude_project",
                                   return_value=self.cwd), \
                 mock.patch.object(codex_run, "chat_identity",
                                   return_value=("local-right", "Same title")):
                codex_run.bind_project("right")
            saved = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["threads"]["right"]["chat_id"], "local-right")
            self.assertEqual(saved["chats"]["local-right"], "right")


class ProjectRepairTests(unittest.TestCase):
    def test_origin_comes_from_rollout_not_mutable_thread_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            rollout = Path(temp) / "rollout.jsonl"
            rollout.write_text(json.dumps({
                "type": "session_meta",
                "payload": {"cwd": "/original/project"},
            }) + "\n", encoding="utf-8")
            with mock.patch.object(codex_run, "rpc", return_value={
                "thread": {"cwd": "/later/runtime/cwd", "path": str(rollout)},
            }):
                self.assertEqual(
                    codex_run.thread_origin_cwd("legacy"),
                    "/original/project",
                )

    def test_bulk_repair_does_not_stamp_callers_chat_onto_legacy_threads(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "projects.json"
            registry_path.write_text(json.dumps({
                "threads": {}, "roots": {},
            }), encoding="utf-8")
            rollout = Path(temp) / "rollout.jsonl"
            rollout.write_text(json.dumps({
                "type": "session_meta",
                "payload": {"cwd": "/original/project"},
            }) + "\n", encoding="utf-8")

            def fake_rpc(method, params=None, timeout=None):
                if method == "thread/list":
                    return {"data": [{
                        "id": "legacy", "cwd": "/later/runtime/cwd",
                    }]}
                if method == "thread/read":
                    return {"thread": {
                        "cwd": "/later/runtime/cwd", "path": str(rollout),
                    }}
                raise AssertionError(method)

            with mock.patch.object(codex_run, "REGISTRY", str(registry_path)), \
                 mock.patch.object(codex_run, "rpc", side_effect=fake_rpc), \
                 mock.patch.object(codex_run, "claude_project",
                                   side_effect=lambda path, prefer_session=False:
                                   "/original/project"):
                fixed, skipped = codex_run.repair_projects()

            self.assertEqual((fixed, skipped), (1, []))
            saved = json.loads(registry_path.read_text(encoding="utf-8"))
            entry = saved["threads"]["legacy"]
            self.assertEqual(entry["root"], "/original/project")
            self.assertEqual(entry["name"], "project")
            self.assertEqual(entry["chat"], "")
            self.assertNotIn("chat_id", entry)

    def test_unreadable_creation_metadata_is_skipped_not_guessed(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "projects.json"
            registry_path.write_text(json.dumps({
                "threads": {}, "roots": {},
            }), encoding="utf-8")

            def fake_rpc(method, params=None, timeout=None):
                if method == "thread/list":
                    return {"data": [{"id": "legacy", "cwd": "/mutable"}]}
                if method == "thread/read":
                    return {"thread": {"cwd": "/mutable"}}
                raise AssertionError(method)

            with mock.patch.object(codex_run, "REGISTRY", str(registry_path)), \
                 mock.patch.object(codex_run, "rpc", side_effect=fake_rpc):
                fixed, skipped = codex_run.repair_projects()

            self.assertEqual((fixed, skipped), (0, ["legacy"]))
            saved = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["threads"], {})

    def test_project_alias_targets_claude_root_not_runtime_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "projects.json"
            registry_path.write_text(json.dumps({
                "threads": {"t": {"root": "/project", "name": "project"}},
                "roots": {}, "chats": {},
            }), encoding="utf-8")
            with mock.patch.object(codex_run, "REGISTRY", str(registry_path)), \
                 mock.patch.object(codex_run, "project_cwd",
                                   return_value="/project/runtime/subdir"), \
                 mock.patch.object(codex_run, "claude_project",
                                   return_value="/project"), \
                 mock.patch.object(sys, "argv",
                                   ["codex-run", "project", "--name", "Alias"]):
                codex_run.main()
            saved = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["roots"], {"/project": "Alias"})
            self.assertEqual(saved["threads"]["t"]["name"], "Alias")


class ApprovalDefaultTests(unittest.TestCase):
    """The CLI and the UI must agree on what an approval mode IS.

    `auto-review` is not an approvalPolicy value: the protocol wants
    approvalPolicy=on-request PLUS approvalsReviewer=auto_review. The CLI used
    to write `approvalPolicy` straight from its argument, so it could not
    express the mode the UI offers, and it defaulted to `never` -- fully
    autonomous, and more permissive than the session driving it.
    """

    def test_auto_review_maps_to_both_protocol_fields(self):
        self.assertEqual(
            codex_run.approval_overrides("auto-review"),
            {"approvalPolicy": "on-request", "approvalsReviewer": "auto_review"})

    def test_other_modes_pass_through_with_user_as_reviewer(self):
        for mode in ("never", "on-request", "untrusted"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    codex_run.approval_overrides(mode),
                    {"approvalPolicy": mode, "approvalsReviewer": "user"})

    def test_empty_mode_sends_nothing_so_the_server_default_stands(self):
        self.assertEqual(codex_run.approval_overrides(None), {})
        self.assertEqual(codex_run.approval_overrides(""), {})

    def test_new_threads_default_to_auto_review_not_never(self):
        sent = {}

        def fake_rpc(method, params=None, timeout=None):
            if method == "thread/start":
                sent.update(params or {})
                return {"thread": {"id": "t-new"}}
            return {}

        with mock.patch.object(codex_run, "find_thread", return_value=None), \
             mock.patch.object(codex_run, "rpc", side_effect=fake_rpc), \
             mock.patch.object(codex_run, "bind_project"), \
             mock.patch.object(codex_run, "chat_identity",
                               return_value=("local-x", "Chat")):
            codex_run.new("Chat", cwd=os.getcwd())

        self.assertEqual(sent.get("approvalPolicy"), "on-request")
        self.assertEqual(sent.get("approvalsReviewer"), "auto_review")
        self.assertNotEqual(sent.get("approvalPolicy"), "never")


class TurnSettingInheritanceTests(unittest.TestCase):
    def test_claude_task_inherits_thread_cwd_unless_explicitly_overridden(self):
        with tempfile.TemporaryDirectory() as temp:
            prompt = Path(temp) / "task.txt"
            prompt.write_text("do the task", encoding="utf-8")
            calls = []

            def record(method, params=None, timeout=30):
                calls.append((method, params, timeout))
                return {}

            common = (
                mock.patch.object(codex_run, "rpc", side_effect=record),
                mock.patch.object(codex_run, "transcript", return_value=[]),
                mock.patch.object(codex_run, "wait_idle"),
                mock.patch.object(codex_run, "thread_settings", return_value={}),
                mock.patch.object(codex_run, "lock_acquire", return_value=True),
                mock.patch.object(codex_run, "lock_release"),
            )
            with common[0], common[1], common[2], common[3], common[4], common[5]:
                codex_run.send_file("thread-1", str(prompt))

            turn = next(params for method, params, _ in calls
                        if method == "turn/start")
            self.assertNotIn("cwd", turn)

            calls.clear()
            with mock.patch.object(codex_run, "rpc", side_effect=record), \
                 mock.patch.object(codex_run, "transcript", return_value=[]), \
                 mock.patch.object(codex_run, "wait_idle"), \
                 mock.patch.object(codex_run, "thread_settings", return_value={}), \
                 mock.patch.object(codex_run, "lock_acquire", return_value=True), \
                 mock.patch.object(codex_run, "lock_release"):
                codex_run.send_file("thread-1", str(prompt), cwd=temp)

            turn = next(params for method, params, _ in calls
                        if method == "turn/start")
            self.assertEqual(turn["cwd"], os.path.abspath(temp))


class ThreadResumeTests(unittest.TestCase):
    """send/task must RESUME an idle thread before starting a turn.

    The app-server unloads idle threads from memory; `turn/start` then fails
    'thread not found' (-32600) while read/list/info still resolve the thread.
    `steer` already issues a `thread/read` (the load/resume call) first; send and
    send_file did not, so a send to an idle thread died. Both must resume first.
    """

    def _record(self):
        calls = []

        def rpc(method, params=None, timeout=30):
            calls.append((method, params))
            return {}

        ctx = (
            mock.patch.object(codex_run, "rpc", side_effect=rpc),
            mock.patch.object(codex_run, "transcript", return_value=[]),
            mock.patch.object(codex_run, "wait_idle"),
            mock.patch.object(codex_run, "thread_settings", return_value={}),
            mock.patch.object(codex_run, "lock_acquire", return_value=True),
            mock.patch.object(codex_run, "lock_release"),
        )
        return calls, ctx

    def _assert_resume_precedes_turn(self, calls, tid):
        methods = [m for m, _ in calls]
        self.assertIn("thread/read", methods, "no resume was issued")
        self.assertIn("turn/start", methods)
        self.assertLess(
            methods.index("thread/read"), methods.index("turn/start"),
            "thread/read (resume) must precede turn/start")
        read = next(p for m, p in calls if m == "thread/read")
        self.assertEqual(read.get("threadId"), tid)

    def test_send_resumes_the_thread_before_turn_start(self):
        calls, ctx = self._record()
        with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
            codex_run.send("thread-idle", "hello")
        self._assert_resume_precedes_turn(calls, "thread-idle")

    def test_send_file_resumes_the_thread_before_turn_start(self):
        with tempfile.TemporaryDirectory() as temp:
            prompt = Path(temp) / "prompt.txt"
            prompt.write_text("hello", encoding="utf-8")
            calls, ctx = self._record()
            with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4], ctx[5]:
                codex_run.send_file("thread-idle", str(prompt))
        self._assert_resume_precedes_turn(calls, "thread-idle")


class NewCwdArgTests(unittest.TestCase):
    """`new` takes only an optional cwd -- the name is always the chat title.

    A stray NAME passed in the cwd slot (the skill once documented
    `new [name] [cwd]`) must resolve to None rather than be forwarded as a bogus
    cwd that fails 'working directory does not exist'. Only an existing directory
    is accepted, returned unchanged so downstream resolution is as before.
    """

    def test_existing_directory_is_used_as_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(codex_run._valid_cwd(temp), temp)

    def test_tilde_home_is_accepted_and_returned_unexpanded(self):
        self.assertEqual(codex_run._valid_cwd("~"), "~")

    def test_stray_name_does_not_become_a_cwd(self):
        self.assertIsNone(
            codex_run._valid_cwd("iCloud known-folder FXDetached trace"))

    def test_missing_or_empty_arg_is_none(self):
        self.assertIsNone(codex_run._valid_cwd(None))
        self.assertIsNone(codex_run._valid_cwd(""))


if __name__ == "__main__":
    unittest.main()
