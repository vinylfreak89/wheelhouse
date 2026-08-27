import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

import sys


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import bridge


class FakeProcess:
    pid = 4321


class FakeApp:
    def __init__(self):
        self.alive = True
        self.last_error = None
        self.p = FakeProcess()
        self.calls = []
        self.notifications = []
        self.responses = []
        self.broadcasts = []

    def call(self, method, params, timeout=300):
        self.calls.append((method, params, timeout))
        return {"result": {"method": method}}

    def notify(self, method, params):
        self.notifications.append((method, params))

    def respond(self, request_id, result):
        self.responses.append((request_id, result))

    def _broadcast(self, message):
        self.broadcasts.append(message)


def handler(path, body=b""):
    instance = object.__new__(bridge.Handler)
    instance.path = path
    instance.headers = {"Content-Length": str(len(body))}
    instance.rfile = io.BytesIO(body)
    sent = []

    def send(code, payload=b"", ctype="application/json"):
        sent.append((code, payload, ctype))

    instance._send = send
    return instance, sent


class HandlerContractTests(unittest.TestCase):
    def setUp(self):
        self.app = FakeApp()
        self.app_patch = mock.patch.object(bridge, "APP", self.app)
        self.app_patch.start()

    def tearDown(self):
        self.app_patch.stop()

    def test_status_contract(self):
        request, sent = handler("/status")
        request.do_GET()
        self.assertEqual(sent[0][0], 200)
        self.assertEqual(json.loads(sent[0][1]), {
            "alive": True,
            "error": None,
            "pid": 4321,
        })

    def test_rpc_forwards_method_params_and_timeout(self):
        payload = json.dumps({
            "method": "thread/list",
            "params": {"limit": 7},
            "timeout": 19,
        }).encode()
        request, sent = handler("/rpc", payload)
        request.do_POST()
        self.assertEqual(
            self.app.calls,
            [("thread/list", {"limit": 7}, 19)],
        )
        self.assertEqual(json.loads(sent[0][1]), {
            "result": {"method": "thread/list"},
        })

    def test_notify_and_approval_response_contracts(self):
        payload = json.dumps({
            "method": "initialized",
            "params": {},
            "notify": True,
        }).encode()
        request, sent = handler("/rpc", payload)
        request.do_POST()
        self.assertEqual(self.app.notifications, [("initialized", {})])
        self.assertEqual(json.loads(sent[0][1]), {"ok": True})

        payload = json.dumps({"id": 12, "result": {"decision": "accept"}}).encode()
        request, sent = handler("/respond", payload)
        request.do_POST()
        self.assertEqual(
            self.app.responses,
            [(12, {"decision": "accept"})],
        )
        self.assertEqual(json.loads(sent[0][1]), {"ok": True})

    def test_bad_json_missing_method_and_unknown_path(self):
        request, sent = handler("/rpc", b"{")
        request.do_POST()
        self.assertEqual((sent[0][0], json.loads(sent[0][1])),
                         (400, {"error": "bad json"}))

        request, sent = handler("/rpc", b"{}")
        request.do_POST()
        self.assertEqual((sent[0][0], json.loads(sent[0][1])),
                         (400, {"error": "no method"}))

        request, sent = handler("/unknown", b"{}")
        request.do_POST()
        self.assertEqual(sent[0][0], 404)

    def test_threadmeta_tolerates_a_reduced_state_database_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            codex_state = home / ".codex"
            codex_state.mkdir()
            database = codex_state / "state_5.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, model TEXT, cwd TEXT)"
            )
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?)",
                ("thread-1", "gpt-test", "/work/project"),
            )
            connection.commit()
            connection.close()

            registry = home / "projects.json"
            registry.write_text(json.dumps({"threads": {}, "roots": {}}),
                                encoding="utf-8")
            request, sent = handler("/threadmeta?id=thread-1")
            request.REGISTRY = str(registry)
            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                request._threadmeta()
            data = json.loads(sent[0][1])
            self.assertEqual(data["model"], "gpt-test")
            self.assertEqual(data["cwd"], "/work/project")
            self.assertEqual(data["project"], "project")


class AppServerFramingTests(unittest.TestCase):
    def make_app(self):
        app = object.__new__(bridge.AppServer)
        app.wlock = threading.Lock()
        app.idlock = threading.Lock()
        app.pending = {}
        app.subscribers = []
        app._id = 0
        app._wire = mock.Mock()
        return app

    def test_call_writes_json_rpc_and_returns_matching_response(self):
        app = self.make_app()
        written = []

        def write(message):
            written.append(message)
            app.pending[message["id"]].put({
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"ok": True},
            })

        app._write = write
        self.assertEqual(
            app.call("thread/list", {"limit": 2}, timeout=0.1),
            {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}},
        )
        self.assertEqual(written, [{
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread/list",
            "params": {"limit": 2},
        }])

    def test_notify_and_response_frames(self):
        app = self.make_app()
        written = []
        app._write = written.append
        app.notify("initialized", {})
        app.respond(9, {"decision": "decline"})
        self.assertEqual(written, [
            {"jsonrpc": "2.0", "method": "initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 9,
             "result": {"decision": "decline"}},
        ])


if __name__ == "__main__":
    unittest.main()
