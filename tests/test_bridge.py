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
import project_registry


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
        self.response_errors = []
        self.broadcasts = []

    def call(self, method, params, timeout=300):
        self.calls.append((method, params, timeout))
        return {"result": {"method": method}}

    def notify(self, method, params):
        self.notifications.append((method, params))

    def respond(self, request_id, result):
        self.responses.append((request_id, result))

    def respond_error(self, request_id, error):
        self.response_errors.append((request_id, error))

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
        bridge.DRAFTS.clear()
        bridge.DRAFT_EPOCH = 0
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

    def test_rpc_rejects_an_unlaunchable_working_directory(self):
        for method in ("thread/start", "thread/settings/update", "turn/start"):
            with self.subTest(method=method):
                payload = json.dumps({
                    "method": method,
                    "params": {"threadId": "thread-1", "cwd": "/missing/wheelhouse-cwd"},
                }).encode()
                request, sent = handler("/rpc", payload)
                request.do_POST()
                self.assertEqual(sent[0][0], 200)
                self.assertEqual(json.loads(sent[0][1]), {
                    "error": {
                        "code": -32602,
                        "message": ("working directory does not exist or is not a directory: "
                                    "/missing/wheelhouse-cwd"),
                    },
                })
        self.assertEqual(self.app.calls, [])

    def test_rpc_accepts_an_existing_working_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            payload = json.dumps({
                "method": "thread/settings/update",
                "params": {"threadId": "thread-1", "cwd": temp},
            }).encode()
            request, sent = handler("/rpc", payload)
            request.do_POST()
        self.assertEqual(
            self.app.calls,
            [("thread/settings/update", {"threadId": "thread-1", "cwd": temp}, 300)],
        )
        self.assertEqual(json.loads(sent[0][1]), {
            "result": {"method": "thread/settings/update"},
        })

    def test_rpc_rejects_relative_or_file_working_directories(self):
        with tempfile.NamedTemporaryFile() as file:
            for cwd in ("relative/path", file.name):
                with self.subTest(cwd=cwd):
                    payload = json.dumps({
                        "method": "thread/start", "params": {"cwd": cwd},
                    }).encode()
                    request, sent = handler("/rpc", payload)
                    request.do_POST()
                    error = json.loads(sent[0][1])["error"]
                    self.assertEqual(error["code"], -32602)
        self.assertEqual(self.app.calls, [])

    def test_notify_and_success_response_contracts(self):
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

    def test_error_response_contract(self):
        payload = json.dumps({
            "id": 13,
            "error": {"code": -32601, "message": "unsupported"},
        }).encode()
        request, sent = handler("/respond", payload)
        request.do_POST()
        self.assertEqual(
            self.app.response_errors,
            [(13, {"code": -32601, "message": "unsupported"})],
        )
        self.assertEqual(json.loads(sent[0][1]), {"ok": True})

    def test_response_requires_a_request_id(self):
        request, sent = handler("/respond", b'{"result":{}}')
        request.do_POST()
        self.assertEqual((sent[0][0], json.loads(sent[0][1])),
                         (400, {"error": "no id"}))

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

    def test_drafts_survive_webview_reload_without_crossing_threads(self):
        for thread_id, text in (("thread-a", "unfinished A"),
                                ("thread-b", "unfinished B")):
            request, sent = handler(f"/draft?id={thread_id}")
            request.do_GET()
            epoch = json.loads(sent[0][1])["epoch"]
            payload = json.dumps({"id": thread_id, "text": text,
                                  "epoch": epoch, "sequence": 1}).encode()
            request, sent = handler("/draft", payload)
            request.do_POST()
            self.assertEqual(json.loads(sent[0][1]), {"ok": True})

        request, sent = handler("/draft?id=thread-a")
        request.do_GET()
        thread_a = json.loads(sent[0][1])
        self.assertEqual(thread_a["text"], "unfinished A")
        request, sent = handler("/draft?id=thread-b")
        request.do_GET()
        self.assertEqual(json.loads(sent[0][1])["text"], "unfinished B")

        request, _ = handler(
            "/draft", json.dumps({"id": "thread-a", "text": "",
                                  "epoch": thread_a["epoch"],
                                  "sequence": 1}).encode())
        request.do_POST()
        request, sent = handler("/draft?id=thread-a")
        request.do_GET()
        self.assertEqual(json.loads(sent[0][1])["text"], "")

    def test_draft_contract_rejects_missing_ids_and_non_string_text(self):
        request, sent = handler("/draft", b'{"text":"x"}')
        request.do_POST()
        self.assertEqual(sent[0][0], 400)
        request, sent = handler(
            "/draft", b'{"id":"thread-a","text":12,"epoch":1,"sequence":1}')
        request.do_POST()
        self.assertEqual(sent[0][0], 400)

    def test_new_draft_epoch_rejects_late_writes_from_the_reloaded_page(self):
        request, sent = handler("/draft?id=thread-a")
        request.do_GET()
        old_epoch = json.loads(sent[0][1])["epoch"]
        request, sent = handler("/draft?id=thread-a")
        request.do_GET()
        new_epoch = json.loads(sent[0][1])["epoch"]

        request, sent = handler("/draft", json.dumps({
            "id": "thread-a", "text": "old page", "epoch": old_epoch,
            "sequence": 99,
        }).encode())
        request.do_POST()
        self.assertEqual(sent[0][0], 409)
        request, sent = handler("/draft", json.dumps({
            "id": "thread-a", "text": "new page", "epoch": new_epoch,
            "sequence": 1,
        }).encode())
        request.do_POST()
        self.assertEqual(sent[0][0], 200)

    def test_transcript_preserves_assistant_phase_and_reports_source_inversions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex" / "sessions"
            root.mkdir(parents=True)
            rollout = root / "rollout-thread-a.jsonl"
            records = [
                {"timestamp": "2026-08-30T02:00:00Z", "type": "response_item",
                 "payload": {"type": "message", "role": "assistant",
                             "phase": "commentary",
                             "content": [{"type": "output_text", "text": "working"}]}},
                {"timestamp": "2026-08-30T01:59:59Z", "type": "response_item",
                 "payload": {"type": "message", "role": "assistant",
                             "phase": "final_answer",
                             "content": [{"type": "output_text", "text": "done"}]}},
                {"timestamp": "2026-08-30T02:00:01Z", "type": "event_msg",
                 "payload": {"type": "task_complete", "last_agent_message": None,
                             "error": {
                                 "message": "Selected model is at capacity.",
                                 "codex_error_info": "server_overloaded",
                             }}},
            ]
            rollout.write_text("".join(json.dumps(r) + "\n" for r in records),
                               encoding="utf-8")
            request, sent = handler("/transcript?id=thread-a")
            with mock.patch.dict(os.environ, {"HOME": temp}):
                request.do_GET()
            data = json.loads(sent[0][1])

        self.assertEqual([(r["cls"], r["who"], r["text"]) for r in data["rows"]], [
            ("rsn", "codex · thinking", "working"),
            ("agent", "codex", "done"),
            ("err", "API error",
             "Selected model is at capacity.\ntype: server_overloaded"),
        ])
        self.assertTrue(data["revision"])
        self.assertIn("1 timestamp inversion", data["journalWarning"])

    def test_transcript_revision_probe_does_not_reopen_unchanged_rollout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".codex" / "sessions"
            root.mkdir(parents=True)
            rollout = root / "rollout-thread-a.jsonl"
            rollout.write_text(json.dumps({
                "timestamp": "2026-09-02T00:00:00Z",
                "type": "response_item",
                "payload": {"type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}]},
            }) + "\n", encoding="utf-8")
            stat = rollout.stat()
            revision = f"{stat.st_mtime_ns}:{stat.st_size}"
            request, sent = handler(
                f"/transcript?id=thread-a&revision={revision}")
            with mock.patch.dict(os.environ, {"HOME": temp}), \
                 mock.patch("builtins.open",
                            side_effect=AssertionError("rollout was reparsed")):
                request.do_GET()
            data = json.loads(sent[0][1])

        self.assertEqual(data, {"unchanged": True, "revision": revision})

    def _turn(self, number, timestamp=None, rows=True):
        timestamp = timestamp or f"2026-09-02T00:00:{number:02d}Z"
        records = [{"timestamp": timestamp, "type": "event_msg",
                    "payload": {"type": "task_started"}}]
        if rows:
            records.extend([
                {"timestamp": timestamp, "type": "response_item",
                 "payload": {"type": "message", "role": "user",
                             "content": [{"text": f"question {number}"}]}},
                {"timestamp": timestamp, "type": "response_item",
                 "payload": {"type": "function_call", "name": "test",
                             "call_id": f"call-{number}",
                             "arguments": json.dumps({"turn": number})}},
                {"timestamp": timestamp, "type": "response_item",
                 "payload": {"type": "function_call_output",
                             "call_id": f"call-{number}",
                             "output": f"result {number}"}},
                {"timestamp": timestamp, "type": "response_item",
                 "payload": {"type": "message", "role": "assistant",
                             "phase": "final_answer",
                             "content": [{"text": f"answer {number}"}]}},
            ])
        records.append({"timestamp": timestamp, "type": "event_msg",
                        "payload": {"type": "task_complete"}})
        return records

    def _journal(self, root, thread_id="thread-pages", turns=range(7)):
        sessions = Path(root) / ".codex" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        rollout = sessions / f"rollout-{thread_id}.jsonl"
        records = [record for turn in turns for record in self._turn(turn)]
        rollout.write_text("".join(json.dumps(record) + "\n" for record in records),
                           encoding="utf-8")
        return rollout, records

    def test_transcript_pages_join_without_gaps_duplicates_or_reordering(self):
        with tempfile.TemporaryDirectory() as temp:
            rollout, records = self._journal(temp)
            with mock.patch.dict(os.environ, {"HOME": temp}):
                sources = bridge._rollout_sources("thread-pages")
                expected = bridge._render_records([
                    item for item in bridge._iter_forward(
                        sources, bridge._first_coord(sources))])[0]
                pages = [bridge._page("thread-pages", sources, limit=5)]
                while pages[0]["hasEarlier"]:
                    pages.insert(0, bridge._page(
                        "thread-pages", sources, direction="before",
                        cursor=pages[0]["startCursor"], limit=5))

        actual = [row for page in pages for row in page["rows"]]
        self.assertEqual([row["id"] for row in actual],
                         [row["id"] for row in expected])
        self.assertEqual(len({row["id"] for row in actual}), len(actual))
        commands = [row for row in actual if row["cls"] == "cmd"]
        self.assertTrue(all("result" in row["text"] for row in commands))

    def test_forward_pages_are_the_exact_inverse_of_backward_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            self._journal(temp)
            with mock.patch.dict(os.environ, {"HOME": temp}):
                sources = bridge._rollout_sources("thread-pages")
                first = bridge._page("thread-pages", sources, direction="after",
                                     cursor=bridge._encode_cursor(
                                         "thread-pages", sources, 0, 0), limit=4)
                pages = [first]
                while pages[-1]["hasLater"]:
                    pages.append(bridge._page(
                        "thread-pages", sources, direction="after",
                        cursor=pages[-1]["endCursor"], limit=4))
                expected = bridge._render_records(list(bridge._iter_forward(
                    sources, bridge._first_coord(sources))))[0]
        self.assertEqual([row["id"] for page in pages for row in page["rows"]],
                         [row["id"] for row in expected])

    def test_page_limit_never_splits_a_large_turn_or_tool_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            rollout, _ = self._journal(temp, turns=[])
            records = self._turn(1)
            # One turn has far more rendered rows than the requested limit.
            records[1:1] = [
                {"timestamp": "2026-09-02T00:00:00Z", "type": "response_item",
                 "payload": {"type": "message", "role": "assistant",
                             "content": [{"text": f"part {i}"}]}}
                for i in range(20)]
            rollout.write_text("".join(json.dumps(r) + "\n" for r in records))
            with mock.patch.dict(os.environ, {"HOME": temp}):
                page = bridge._page("thread-pages",
                                    bridge._rollout_sources("thread-pages"), limit=2)
        self.assertGreater(len(page["rows"]), 2)
        command = next(row for row in page["rows"] if row["cls"] == "cmd")
        self.assertIn("result 1", command["text"])

    def test_append_keeps_existing_cursor_valid_and_exposes_only_new_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            rollout, _ = self._journal(temp, turns=range(2))
            with mock.patch.dict(os.environ, {"HOME": temp}):
                sources = bridge._rollout_sources("thread-pages")
                tail = bridge._page("thread-pages", sources, limit=100)
                old_end = tail["endCursor"]
                with rollout.open("a", encoding="utf-8") as fh:
                    fh.write("".join(json.dumps(r) + "\n" for r in self._turn(2)))
                sources = bridge._rollout_sources("thread-pages")
                later = bridge._page("thread-pages", sources, direction="after",
                                     cursor=old_end, limit=100)
        self.assertEqual([row["text"] for row in later["rows"] if row["cls"] == "user"],
                         ["question 2"])

    def test_cursor_rejects_truncation_replacement_mutation_and_foreign_thread(self):
        with tempfile.TemporaryDirectory() as temp:
            rollout, _ = self._journal(temp, turns=range(3))
            with mock.patch.dict(os.environ, {"HOME": temp}):
                sources = bridge._rollout_sources("thread-pages")
                tail = bridge._page("thread-pages", sources, limit=3)
                cursor = tail["startCursor"]
                with self.assertRaises(bridge.TranscriptCursorError):
                    bridge._decode_cursor(cursor, "other-thread", sources)

                original = rollout.read_bytes()
                rollout.write_bytes(original[:20])
                with self.assertRaises(bridge.TranscriptCursorError):
                    bridge._decode_cursor(cursor, "thread-pages",
                                          bridge._rollout_sources("thread-pages"))

                rollout.write_bytes(original)
                sources = bridge._rollout_sources("thread-pages")
                cursor = bridge._page("thread-pages", sources, limit=3)["startCursor"]
                file_index, offset = bridge._decode_cursor(cursor, "thread-pages", sources)
                changed = bytearray(rollout.read_bytes())
                changed[max(0, offset - 5)] ^= 1
                rollout.write_bytes(changed)
                with self.assertRaises(bridge.TranscriptCursorError):
                    bridge._decode_cursor(cursor, "thread-pages",
                                          bridge._rollout_sources("thread-pages"))

                replacement = rollout.with_suffix(".new")
                replacement.write_bytes(original)
                os.replace(replacement, rollout)
                with self.assertRaises(bridge.TranscriptCursorError):
                    bridge._decode_cursor(cursor, "thread-pages",
                                          bridge._rollout_sources("thread-pages"))

    def test_tail_ignores_a_partially_appended_final_record(self):
        with tempfile.TemporaryDirectory() as temp:
            rollout, _ = self._journal(temp, turns=range(2))
            with rollout.open("ab") as fh:
                fh.write(json.dumps(self._turn(9)[1]).encode())  # valid JSON, no newline
            with mock.patch.dict(os.environ, {"HOME": temp}):
                sources = bridge._rollout_sources("thread-pages")
                page = bridge._page("thread-pages", sources, limit=100)
                cursor = page["endCursor"]
                with rollout.open("ab") as fh:
                    fh.write(b"\n")
                later = bridge._page("thread-pages",
                                     bridge._rollout_sources("thread-pages"),
                                     direction="after", cursor=cursor, limit=100)
        self.assertNotIn("question 9", [row["text"] for row in page["rows"]])
        self.assertIn("question 9", [row["text"] for row in later["rows"]])

    def test_empty_turns_do_not_prevent_page_from_reaching_rendered_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            rollout, _ = self._journal(temp, turns=[])
            records = self._turn(1) + self._turn(2, rows=False) + self._turn(3, rows=False)
            rollout.write_text("".join(json.dumps(r) + "\n" for r in records))
            with mock.patch.dict(os.environ, {"HOME": temp}):
                page = bridge._page("thread-pages",
                                    bridge._rollout_sources("thread-pages"), limit=2)
        self.assertIn("question 1", [row["text"] for row in page["rows"]])

    def test_search_progress_is_monotonic_and_search_wraps_in_both_directions(self):
        with tempfile.TemporaryDirectory() as temp:
            self._journal(temp, turns=range(5))
            request, _ = handler("/transcript/search")
            with mock.patch.dict(os.environ, {"HOME": temp}):
                forward = list(request._transcript_search_events(
                    "thread-pages", "ANSWER", "forward"))
                first = next(event for event in forward if event["type"] == "match")
                again = list(request._transcript_search_events(
                    "thread-pages", "answer", "forward", first["rowId"]))
                second = next(event for event in again if event["type"] == "match")
                backward_wrap = list(request._transcript_search_events(
                    "thread-pages", "answer", "backward", first["rowId"]))
                last = next(event for event in backward_wrap if event["type"] == "match")
        progress = [event["percent"] for event in forward if event["type"] == "progress"]
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(progress[-1], 100)
        self.assertNotEqual(first["rowId"], second["rowId"])
        self.assertNotEqual(first["rowId"], last["rowId"])
        self.assertIn(first["rowId"], [row["id"] for row in first["page"]["rows"]])

    def test_search_no_match_finishes_without_a_page(self):
        with tempfile.TemporaryDirectory() as temp:
            self._journal(temp, turns=range(2))
            request, _ = handler("/transcript/search")
            with mock.patch.dict(os.environ, {"HOME": temp}):
                events = list(request._transcript_search_events(
                    "thread-pages", "not in this journal"))
        self.assertEqual(events[-1], {"type": "done", "found": False})
        self.assertEqual(events[-2]["percent"], 100)

    def test_multiple_rollout_files_paginate_in_filename_then_byte_order(self):
        with tempfile.TemporaryDirectory() as temp:
            sessions = Path(temp) / ".codex" / "sessions"
            sessions.mkdir(parents=True)
            for name, turns in (("a-thread-pages.jsonl", range(2)),
                                ("b-thread-pages.jsonl", range(2, 4))):
                records = [record for turn in turns for record in self._turn(turn)]
                (sessions / name).write_text(
                    "".join(json.dumps(record) + "\n" for record in records))
            with mock.patch.dict(os.environ, {"HOME": temp}):
                sources = bridge._rollout_sources("thread-pages")
                tail = bridge._page("thread-pages", sources, limit=3)
                older = bridge._page("thread-pages", sources, direction="before",
                                     cursor=tail["startCursor"], limit=100)
        texts = [row["text"] for row in older["rows"] + tail["rows"]
                 if row["cls"] == "user"]
        self.assertEqual(texts, ["question 0", "question 1", "question 2", "question 3"])

    def test_reverse_reader_handles_records_larger_than_its_block(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "large.jsonl"
            records = [{"n": 1, "payload": "x" * 140000}, {"n": 2}]
            path.write_text("".join(json.dumps(record) + "\n" for record in records))
            source = {"path": str(path), "stat": path.stat()}
            reverse = list(bridge._iter_reverse([source], (0, path.stat().st_size)))
            forward = list(bridge._iter_forward([source], (0, 0)))
        self.assertEqual([record[2]["n"] for record in reverse], [2, 1])
        self.assertEqual(reverse[1][0], forward[0][0])
        self.assertEqual(reverse[1][1], forward[0][1])

    def test_transcript_endpoint_reports_stale_cursor_as_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            rollout, _ = self._journal(temp, turns=range(2))
            with mock.patch.dict(os.environ, {"HOME": temp}):
                sources = bridge._rollout_sources("thread-pages")
                cursor = bridge._page("thread-pages", sources, limit=3)["startCursor"]
                rollout.write_bytes(rollout.read_bytes()[:10])
                request, sent = handler(
                    "/transcript?id=thread-pages&before=" + cursor)
                request.do_GET()
        self.assertEqual(sent[0][0], 409)
        self.assertTrue(json.loads(sent[0][1])["cursorInvalid"])

    def test_database_rollout_path_avoids_a_recursive_session_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            codex = home / ".codex"
            codex.mkdir()
            rollout = home / "exact.jsonl"
            rollout.write_text("{}\n")
            database = codex / "state_5.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT)")
                connection.execute("INSERT INTO threads VALUES (?, ?)",
                                   ("thread-pages", str(rollout)))
                connection.commit()
            finally:
                connection.close()
            with mock.patch.dict(os.environ, {"HOME": temp}), \
                 mock.patch.object(bridge.glob, "glob",
                                   side_effect=AssertionError("recursive scan used")):
                sources = bridge._rollout_sources("thread-pages")
        self.assertEqual([source["path"] for source in sources], [str(rollout)])

    def test_tail_page_does_not_parse_the_older_journal_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            self._journal(temp, turns=range(100))
            with mock.patch.dict(os.environ, {"HOME": temp}):
                sources = bridge._rollout_sources("thread-pages")
                real_loads = json.loads
                with mock.patch.object(bridge.json, "loads", wraps=real_loads) as loads:
                    page = bridge._page("thread-pages", sources, limit=5)
        self.assertTrue(page["hasEarlier"])
        self.assertLess(loads.call_count, 25,
                        "tail loading regressed to parsing the whole journal")

    def test_search_http_stream_is_ndjson_and_finishes_without_content_length(self):
        with tempfile.TemporaryDirectory() as temp:
            self._journal(temp, turns=range(2))
            request = object.__new__(bridge.Handler)
            request.path = ("/transcript/search?id=thread-pages&q=answer"
                            "&direction=forward")
            request.wfile = io.BytesIO()
            response, headers = [], []
            request.send_response = response.append
            request.send_header = lambda key, value: headers.append((key, value))
            request.end_headers = lambda: None
            with mock.patch.dict(os.environ, {"HOME": temp}):
                request._transcript_search()
            events = [json.loads(line) for line in request.wfile.getvalue().splitlines()]
        self.assertEqual(response, [200])
        self.assertIn(("Content-Type", "application/x-ndjson; charset=utf-8"), headers)
        self.assertNotIn("Content-Length", [key for key, _ in headers])
        self.assertEqual(events[-1]["type"], "match")
        self.assertTrue(request.close_connection)

    def test_disconnected_search_client_cancels_scan_without_server_error(self):
        class DisconnectingWriter:
            def __init__(self):
                self.writes = 0

            def write(self, _payload):
                self.writes += 1
                if self.writes > 1:
                    raise BrokenPipeError()

            def flush(self):
                pass

        with tempfile.TemporaryDirectory() as temp:
            self._journal(temp, turns=range(5))
            request = object.__new__(bridge.Handler)
            request.path = "/transcript/search?id=thread-pages&q=answer"
            request.wfile = DisconnectingWriter()
            request.send_response = lambda _code: None
            request.send_header = lambda _key, _value: None
            request.end_headers = lambda: None
            with mock.patch.dict(os.environ, {"HOME": temp}):
                request._transcript_search()  # must not leak the disconnect
        self.assertEqual(request.wfile.writes, 2)

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
            self.assertEqual(data["project"], "(unassigned)")
            self.assertEqual(data["project_root"], "")

    def test_projects_resolve_from_claude_roots_not_runtime_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            codex_state = home / ".codex"
            codex_state.mkdir()
            tos_root = home / "Documents" / "tos-performance"
            blackmagic_root = home / "Documents" / "blackmagic-usb-mac"
            tos_root.mkdir(parents=True)
            blackmagic_root.mkdir(parents=True)
            (home / ".claude.json").write_text(json.dumps({
                "projects": {str(tos_root): {}, str(blackmagic_root): {}},
            }), encoding="utf-8")

            tos_rollout = home / "tos.jsonl"
            tos_rollout.write_text(json.dumps({
                "type": "session_meta",
                "payload": {"cwd": str(tos_root / "ToS GPU render pipeline")},
            }) + "\n", encoding="utf-8")
            blackmagic_rollout = home / "blackmagic.jsonl"
            blackmagic_rollout.write_text(json.dumps({
                "type": "session_meta", "payload": {"cwd": str(blackmagic_root)},
            }) + "\n", encoding="utf-8")

            database = codex_state / "state_5.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, cwd TEXT)"
            )
            connection.executemany("INSERT INTO threads VALUES (?, ?, ?)", [
                ("tos", str(tos_rollout), str(tos_root / "runtime")),
                ("blackmagic", str(blackmagic_rollout), "/unrelated/runtime"),
            ])
            connection.commit()
            connection.close()

            registry = home / "projects.json"
            registry.write_text(json.dumps({
                "threads": {"tos": {
                    "root": str(tos_root / "ToS GPU render pipeline"),
                    "name": "ToS GPU render pipeline",
                    "chat_id": "local-tos",
                }},
                "roots": {}, "chats": {"local-tos": "tos"},
            }), encoding="utf-8")
            request, sent = handler("/projects")
            request.REGISTRY = str(registry)
            with mock.patch.dict(os.environ, {"HOME": str(home)}), \
                 mock.patch.object(project_registry, "CLAUDE_CONFIG",
                                   str(home / ".claude.json")):
                request._projects()
            data = json.loads(sent[0][1])

            self.assertEqual(data["threads"]["tos"], {
                "root": str(tos_root), "name": "tos-performance",
                "chat_id": "local-tos",
            })
            self.assertEqual(data["threads"]["blackmagic"], {
                "root": str(blackmagic_root), "name": "blackmagic-usb-mac",
            })
            # Runtime discovery is intentionally read-only; it cannot race a
            # CLI chat binding and lose fields from the persisted registry.
            saved = json.loads(registry.read_text(encoding="utf-8"))
            self.assertNotIn("blackmagic", saved["threads"])


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
        app.respond_error(10, {"code": -32601, "message": "unsupported"})
        self.assertEqual(written, [
            {"jsonrpc": "2.0", "method": "initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 9,
             "result": {"decision": "decline"}},
            {"jsonrpc": "2.0", "id": 10,
             "error": {"code": -32601, "message": "unsupported"}},
        ])


if __name__ == "__main__":
    unittest.main()
