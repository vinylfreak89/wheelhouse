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

    def test_node_contract_suite(self):
        result = subprocess.run(
            ["node", "--test", "tests/ui_contracts.test.js"],
            cwd=REPO, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
