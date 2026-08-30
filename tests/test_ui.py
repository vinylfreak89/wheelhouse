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
NATIVE = REPO / "native" / "main.swift"


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
        cls.native = NATIVE.read_text(encoding="utf-8")

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
            "/draft": 'self.path.startswith("/draft")',
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

    def test_active_working_directory_is_persisted_and_visible(self):
        self.assertIn('rpc("thread/settings/update",payload,30)', self.html)
        self.assertIn('id="cCwd"', self.html)
        self.assertIn('mk("Working directory…"', self.html)
        self.assertIn("p.threadSettings.cwd", self.html)

    def test_every_turn_carries_the_resolved_thread_working_directory(self):
        contracts = self.html.split("WHEELHOUSE_UI_CONTRACTS_START", 1)[1].split(
            "WHEELHOUSE_UI_CONTRACTS_END", 1)[0]
        turn = contracts.split("turnStart(", 1)[1].split(
            "metaRefreshDelays", 1)[0]
        self.assertIn("if(cwd) p.cwd=cwd", turn)
        sent = self.html.split("async function send()", 1)[1].split(
            '$("#send").onclick=send', 1)[0]
        self.assertIn('cwd:curInfo.cwd||cwdCache[threadId]||""', sent)
        opened = self.html.split("async function open_(id)", 1)[1].split(
            "/* ---------- new thread", 1)[0]
        self.assertIn("await loadMeta(id)", opened)

    def test_active_thread_can_route_approvals_to_auto_review(self):
        self.assertIn('id="cAppr"', self.html)
        self.assertIn('mk("Approval routing…"', self.html)
        self.assertIn('approvalsReviewer:"auto_review"', self.html)
        self.assertIn('rpc("thread/settings/update",payload,30)', self.html)
        self.assertIn("opened.approvalsReviewer", self.html)
        self.assertIn("p.threadSettings.approvalsReviewer", self.html)

    def test_command_f_opens_conversation_find_and_full_screen_keeps_native_shortcut(self):
        self.assertIn('id="findbar"', self.html)
        self.assertIn("window.openFind=", self.html)
        self.assertIn("window.findNext=", self.html)
        self.assertIn('messageHandlers.find', self.html)
        self.assertIn('withTitle: "Find…"', self.native)
        self.assertIn('#selector(showFind)', self.native)
        self.assertIn('cfg.userContentController.add(self, name: "find")', self.native)
        self.assertIn('configuration.wraps = true', self.native)
        self.assertIn('fullScreen.keyEquivalentModifierMask = [.command, .control]',
                      self.native)

    def test_usage_renders_all_rate_limit_buckets_and_visible_errors(self):
        body = self.html.split("async function loadUsage", 1)[1].split(
            "/* ---------- effective settings", 1)[0]
        self.assertIn("UIContracts.rateLimitBuckets", body)
        self.assertIn("buckets.map(bucketHtml)", body)
        self.assertIn("usage-error", body)
        self.assertIn("authentication expired", body)

    def test_metadata_refresh_does_not_overwrite_next_turn_choices(self):
        body = self.html.split("async function loadMeta", 1)[1].split(
            "let curMeta", 1)[0]
        self.assertNotIn('$("#selModel").value=m.model', body)
        self.assertNotIn("se.value=m.reasoning_effort", body)

    def test_reload_does_not_infer_effort_from_the_first_catalog_model(self):
        effort = self.html.split("function effortOptions", 1)[1].split(
            "function tierOptions", 1)[0]
        self.assertNotIn("||models[0]", effort)
        self.assertIn("effectiveDefault!==undefined", effort)
        boot = self.html.split("/* ---------- boot ---------- */", 1)[1]
        self.assertIn('effortOptions("", "#selEffort", true, "")', boot)
        meta = self.html.split("async function loadMeta", 1)[1].split(
            "let curMeta", 1)[0]
        self.assertIn('m.reasoning_effort||""', meta)

    def test_unchanged_status_polls_do_not_rebuild_or_reorder_the_sidebar(self):
        threads = self.html.split("async function loadThreads", 1)[1].split(
            "const isActive", 1)[0]
        self.assertIn("UIContracts.stableById(threads,nextThreads)", threads)
        render = self.html.split("function renderList", 1)[1].split(
            "function row", 1)[0]
        self.assertIn("fingerprint===renderedListFingerprint", render)
        usage = self.html.split("async function loadUsage", 1)[1].split(
            "/* ---------- effective settings", 1)[0]
        self.assertIn("html===renderedUsageHtml", usage)

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
        current_filter = events.index(
            "const threadRoute=UIContracts.threadEventRoute")
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

    def test_hot_reload_restores_thread_before_accepting_its_live_events(self):
        self.assertNotIn("sessionStorage", self.html)
        self.assertNotIn("localStorage", self.html)
        self.assertIn('new URLSearchParams(location.hash.slice(1))', self.html)
        opened = self.html.split("async function open_(id)", 1)[1].split(
            "/* ---------- new thread", 1)[0]
        self.assertIn("threadViewReady=false", opened)
        self.assertIn("rememberThreadId(id)", opened)
        self.assertIn("threadViewReady=true", opened)
        boot = self.html.split("/* ---------- boot ---------- */", 1)[1]
        self.assertIn("const restoreThread=rememberedThreadId()", boot)
        self.assertIn("if(restoreThread) await open_(restoreThread)", boot)
        events = self.html.split("es.onmessage=async ev=>", 1)[1]
        route = events.index("UIContracts.threadEventRoute")
        delta = events.index('meth==="item/agentMessage/delta"', route)
        self.assertLess(route, delta)
        self.assertIn('threadRoute==="not-ready"||threadRoute==="other"', events)

    def test_unsent_drafts_are_bridge_backed_and_isolated_by_thread(self):
        self.assertNotIn("sessionStorage", self.html)
        self.assertNotIn("localStorage", self.html)
        self.assertIn('fetch("/draft?id="+encodeURIComponent(threadId))', self.html)
        self.assertIn('const body=JSON.stringify({id:threadId,text,epoch,sequence})',
                      self.html)
        self.assertIn('navigator.sendBeacon("/draft"', self.html)
        self.assertIn("window.prepareReload=", self.html)
        self.assertIn("web.callAsyncJavaScript(", self.native)
        self.assertIn("window.prepareReload", self.native)
        opened = self.html.split("async function open_(id)", 1)[1].split(
            "/* ---------- new thread", 1)[0]
        self.assertIn("await persistDraft(cur,input.value)", opened)
        self.assertIn("input.value=await loadDraft(id)", opened)

    def test_external_user_items_render_live_while_local_echoes_are_deduplicated(self):
        sent = self.html.split("/* ---------- send ---------- */", 1)[1].split(
            "/* ---------- stop", 1)[0]
        self.assertIn("const optimisticUserEchoes=[]", sent)
        self.assertIn("addOptimisticUser(threadId,t", sent)
        events = self.html.split("es.onmessage=async ev=>", 1)[1]
        completed = events.split('else if(meth==="item/completed")', 1)[1].split(
            "else if(/^thread", 1)[0]
        self.assertIn("if(!consumeOptimisticUser(p.threadId,it)) renderItem(it)",
                      completed)
        self.assertNotIn("already shown when we sent it", completed)

    def test_navigation_rebuilds_uncommitted_user_echoes(self):
        render = self.html.split("function renderTranscript", 1)[1].split(
            "let openGeneration", 1)[0]
        self.assertIn("optimisticEchoesMissingFromTranscript", render)
        self.assertIn('add("user",entry.who,entry.text,entry.at)', render)

    def test_live_item_start_reserves_protocol_order_without_visible_empty_rows(self):
        events = self.html.rsplit('else if(meth==="item/started")', 1)[1].split(
            'else if(meth==="item/completed")', 1)[0]
        self.assertIn("renderItem(p.item,p.startedAtMs,{reserve:true})", events)
        self.assertIn('if(!/^user/i.test', events)
        self.assertNotIn("reasonEl=", events)
        self.assertNotIn("streamEl=", events)
        self.assertIn('.msg.pending{display:none}', self.html)

    def test_removed_transcript_cache_hook_is_not_called(self):
        self.assertNotIn("txUpdateLast", self.html)

    def test_polling_failures_preserve_last_good_ui_state(self):
        threads = self.html.split("async function loadThreads", 1)[1].split(
            "const isActive", 1)[0]
        self.assertIn("if(!Array.isArray(nextThreads)) return", threads)
        usage = self.html.split("async function loadUsage", 1)[1].split(
            "/* ---------- effective settings", 1)[0]
        self.assertIn("if(r.error||!r.result){", usage)
        self.assertIn('$("#usage").append(note)', usage)

    def test_mcp_elicitation_links_reject_non_http_schemes(self):
        elicitation = self.html.split('kind==="elicitation"', 1)[1].split(
            '} else {\n    promptError', 1)[0]
        self.assertIn('/^https?:\\/\\//i', elicitation)
        self.assertIn("accept.disabled=!safeElicitationUrl", elicitation)

    def test_live_entries_autoscroll_only_when_reader_is_at_bottom(self):
        writes = [line.strip() for line in self.html.splitlines()
                  if "logEl.scrollTop=logEl.scrollHeight" in line
                  and "wlogEl" not in line]
        self.assertGreaterEqual(len(writes), 2)
        self.assertTrue(all(line.startswith("if(stick)") for line in writes), writes)

    def test_large_command_updates_and_approvals_preserve_scroll_intent(self):
        upsert = self.html.split("function upsertItem", 1)[1].split(
            "function renderItem", 1)[0]
        self.assertIn("UIContracts.mutatePreservingTail(logEl", upsert)
        approval = self.html.split("function approval(", 1)[1].split(
            "/* ---------- reconcile", 1)[0]
        self.assertIn("UIContracts.mutatePreservingTail(logEl", approval)
        self.assertNotIn("scrollIntoView", approval)

    def test_node_suites(self):
        # Discover every tests/*.test.js rather than naming one, so a new
        # JavaScript suite cannot be added and then silently never run.
        suites = sorted(f"tests/{p.name}"
                        for p in Path(__file__).parent.glob("*.test.js"))
        self.assertIn("tests/ui_contracts.test.js", suites)
        result = subprocess.run(
            ["node", "--test", *suites],
            cwd=REPO, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
