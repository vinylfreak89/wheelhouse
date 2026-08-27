import json
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
UI = REPO / "ui" / "index.html"
BRIDGE = REPO / "bridge.py"


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, _tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])


class UiStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = UI.read_text(encoding="utf-8")
        cls.bridge = BRIDGE.read_text(encoding="utf-8")

    def test_html_ids_are_unique_and_literal_id_selectors_exist(self):
        parser = IdCollector()
        parser.feed(self.html)
        self.assertEqual(len(parser.ids), len(set(parser.ids)), "duplicate HTML id")
        ids = set(parser.ids)
        referenced = set(re.findall(r'\$\("#([A-Za-z][\w-]*)"\)', self.html))
        self.assertEqual(sorted(referenced - ids), [])

    def test_complete_inline_script_parses(self):
        script = re.search(r"<script>([\s\S]*?)</script>", self.html)
        self.assertIsNotNone(script)
        result = subprocess.run(
            ["node", "--check"], input=script.group(1),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ui_http_endpoints_are_implemented_by_the_bridge(self):
        expected = {
            "/rpc": 'self.path != "/rpc"',
            "/respond": 'self.path == "/respond"',
            "/events": 'self.path == "/events"',
            "/projects": 'self.path == "/projects"',
            "/threadmeta": 'self.path.startswith("/threadmeta")',
            "/transcript": 'self.path.startswith("/transcript")',
        }
        for endpoint, implementation in expected.items():
            self.assertIn(endpoint, self.html)
            self.assertIn(implementation, self.bridge)

    def test_every_literal_ui_rpc_method_exists_in_installed_protocol(self):
        methods = set(re.findall(r'rpc\("([^"]+)"', self.html))
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run([
                "codex", "app-server", "generate-json-schema", "--experimental",
                "--out", temp,
            ], cwd=REPO, check=True, capture_output=True, text=True)
            schema = json.loads(
                (Path(temp) / "codex_app_server_protocol.v2.schemas.json")
                .read_text(encoding="utf-8"))

        advertised = set()

        def walk(value):
            if isinstance(value, dict):
                prop = value.get("properties", {}).get("method")
                if isinstance(prop, dict):
                    advertised.update(prop.get("enum", []))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(schema)
        self.assertEqual(sorted(methods - advertised), [])

    def test_contract_builders_match_current_turn_start_keys(self):
        source = self.html.split("WHEELHOUSE_UI_CONTRACTS_START", 1)[1]
        turn_body = source.split("turnStart(", 1)[1].split("metaRefreshDelays", 1)[0]
        emitted = set(re.findall(r'p\.([A-Za-z][A-Za-z0-9]*)\s*=', turn_body))
        emitted.update(("threadId", "input"))
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run([
                "codex", "app-server", "generate-json-schema", "--experimental",
                "--out", temp,
            ], cwd=REPO, check=True, capture_output=True, text=True)
            turn = json.loads(
                (Path(temp) / "v2" / "TurnStartParams.json")
                .read_text(encoding="utf-8"))
        self.assertEqual(sorted(emitted - set(turn["properties"])), [])
        self.assertTrue(set(turn["required"]).issubset(emitted))

    def test_server_request_dispatch_covers_installed_protocol_exactly(self):
        source = self.html.split("WHEELHOUSE_UI_CONTRACTS_START", 1)[1]
        request_map = source.split("serverRequests:Object.freeze({", 1)[1].split(
            "})", 1)[0]
        handled = set(re.findall(r'"([^"]+)"\s*:', request_map))
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run([
                "codex", "app-server", "generate-json-schema", "--experimental",
                "--out", temp,
            ], cwd=REPO, check=True, capture_output=True, text=True)
            schema = json.loads(
                (Path(temp) / "ServerRequest.json").read_text(encoding="utf-8"))

        advertised = set()

        def walk(value):
            if isinstance(value, dict):
                method = value.get("properties", {}).get("method", {})
                advertised.update(method.get("enum", []))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(schema)
        self.assertEqual(sorted(handled), sorted(advertised))

    def test_rate_limit_read_uses_protocol_null_params(self):
        self.assertIn('rpc("account/rateLimits/read",null,30)', self.html)

    def test_metadata_refresh_does_not_overwrite_next_turn_choices(self):
        body = self.html.split("async function loadMeta", 1)[1].split(
            "let curMeta", 1)[0]
        self.assertNotIn('$("#selModel").value=m.model', body)
        self.assertNotIn("se.value=m.reasoning_effort", body)

    def test_state_changing_notifications_refresh_the_ui(self):
        for method in (
            "thread/settings/updated",
            "thread/tokenUsage/updated",
            "thread/project/updated",
            "project/changed",
            "model/rerouted",
            "account/rateLimits/updated",
            "thread/reverted",
            "thread/compacted",
            "thread/closed",
            "warning",
            "configWarning",
            "guardianWarning",
        ):
            self.assertIn(method, self.html)

    def test_answered_prompts_leave_the_pending_tray(self):
        body = self.html.split("function retireApproval", 1)[1].split(
            "function promptError", 1)[0]
        self.assertIn("logEl.append(d)", body)
        self.assertIn("delete apprEls[id]", body)

    def test_completion_expires_only_prompts_from_the_finished_turn(self):
        body = self.html.split("function expireApprovals", 1)[1].split(
            "function promptError", 1)[0]
        self.assertIn("record.threadId===threadId", body)
        self.assertIn("record.turnId===turnId", body)

    def test_approval_lifecycle_precedes_thread_event_filters(self):
        events = self.html.split("es.onmessage=async ev=>", 1)[1]
        resolved = events.index('if(meth==="serverRequest/resolved")')
        expiry = events.index("expireApprovals(p.threadId")
        agent_filter = events.index("if(p.threadId&&agentCur")
        current_filter = events.index("if(p.threadId&&cur&&p.threadId!==cur)")
        self.assertLess(resolved, agent_filter)
        self.assertLess(expiry, agent_filter)
        self.assertLess(resolved, current_filter)
        self.assertLess(expiry, current_filter)

    def test_reconciliation_replaces_offscreen_without_reopening_thread(self):
        render = self.html.split("function renderTranscript", 1)[1].split(
            "let openGeneration", 1)[0]
        self.assertIn("document.createDocumentFragment()", render)
        self.assertIn("logEl.replaceChildren(staging)", render)
        reconcile = self.html.split("async function reconcile", 1)[1].split(
            "/* ---------- events", 1)[0]
        self.assertIn("renderTranscript(rows", reconcile)
        self.assertIn("if(busy) return", reconcile)
        self.assertNotIn("open_(cur)", reconcile)

    def test_removed_transcript_cache_hook_is_not_called(self):
        self.assertNotIn("txUpdateLast", self.html)

    def test_polling_failures_preserve_last_good_ui_state(self):
        threads = self.html.split("async function loadThreads", 1)[1].split(
            "const isActive", 1)[0]
        self.assertIn("if(!Array.isArray(nextThreads)) return", threads)
        usage = self.html.split("async function loadUsage", 1)[1].split(
            "/* ---------- effective settings", 1)[0]
        self.assertIn("if(r.error||!r.result) return", usage)

    def test_mcp_elicitation_links_reject_non_http_schemes(self):
        elicitation = self.html.split('kind==="elicitation"', 1)[1].split(
            '} else {\n    promptError', 1)[0]
        self.assertIn('/^https?:\\/\\//i', elicitation)
        self.assertIn("accept.disabled=!safeElicitationUrl", elicitation)

    def test_live_entries_autoscroll_only_when_reader_is_at_bottom(self):
        writes = [line.strip() for line in self.html.splitlines()
                  if "logEl.scrollTop=logEl.scrollHeight" in line
                  and "wlogEl" not in line]
        self.assertGreaterEqual(len(writes), 4)
        self.assertTrue(all(line.startswith("if(stick)") for line in writes), writes)

    def test_node_contract_suite(self):
        result = subprocess.run(
            ["node", "--test", "tests/ui_contracts.test.js"],
            cwd=REPO, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
