#!/usr/bin/env python3
"""Local bridge: a browser UI + Claude, both driving one Codex app-server.

Why this exists: the ChatGPT desktop app cannot display threads created on a
remote host and offers no way to start one (openai/codex #27284, #22438,
#24280). Rather than fight a half-built UI, we drive the documented app-server
protocol ourselves.

    browser  --POST /rpc-->  bridge  --stdio-->  codex app-server (child)
    browser  <--SSE /events--  bridge  <--- notifications --------'
    Claude   --POST /rpc-->  bridge          (same server, same threads)

Transport is plain newline-delimited JSON-RPC 2.0 over the child's stdio --
`--listen stdio://` is the app-server default, so no socket, no daemon, and no
websocket framing. The bridge owns the process, exactly as the Electron app did.

The protocol has no thread ownership model: several clients may resume the same
thread, so Claude can steer a turn the UI is watching (`turn/steer`).
"""
import json, os, queue, re, shutil, sqlite3, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from project_registry import reconcile_projects

HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(HERE, "ui")
PORT = int(os.environ.get("CODEX_BRIDGE_PORT", "8770"))
def find_codex():
    """Locate the codex binary.

    An app launched from Finder does NOT inherit the shell PATH, so a bare
    "codex" fails there while working fine from a terminal -- which is exactly
    how this broke after the app moved.
    """
    env = os.environ.get("CODEX_BIN")
    if env and os.path.isfile(env):
        return env
    found = shutil.which("codex")
    if found:
        return found
    for c in ("/opt/homebrew/bin/codex", "/usr/local/bin/codex",
              os.path.expanduser("~/.npm-global/bin/codex")):
        if os.path.isfile(c):
            return c
    return "codex"

CODEX = find_codex()

# Extra skill roots to expose to Codex. The app-server forgets these on
# restart, so re-apply them at startup. Point at directories CONTAINING skill
# folders (each with a SKILL.md); Codex namespaces them by the parent dir name.
# Machine-specific roots live in state/skill-roots.json (gitignored) or in
# $CODEX_SKILL_ROOTS, colon-separated. Nothing about one person's checkout
# belongs in the source.
def _skill_roots():
    roots = [os.path.join(HERE, "skills")]
    try:
        with open(os.path.join(HERE, "state", "skill-roots.json"),
                  encoding="utf-8") as f:
            roots += [os.path.expanduser(r) for r in json.load(f)]
    except Exception:
        pass
    roots += [os.path.expanduser(r)
              for r in (os.environ.get("CODEX_SKILL_ROOTS") or "").split(":") if r]
    seen, out = set(), []
    for r in roots:
        if r not in seen and os.path.isdir(r):
            seen.add(r); out.append(r)
    return out


SKILL_ROOTS = _skill_roots()


class AppServer:
    """Owns one `codex app-server` child and multiplexes it."""

    def __init__(self):
        self.wlock = threading.Lock()
        self.idlock = threading.Lock()
        self.pending = {}        # request id -> reply queue
        self.subscribers = []    # SSE listener queues
        self._id = 0
        self.alive = True
        self.last_error = None
        self.p = subprocess.Popen(
            [CODEX, "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, env={**os.environ, "RUST_LOG": os.environ.get("RUST_LOG", "error")})
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()

    def _stderr(self):
        for line in iter(self.p.stderr.readline, b""):
            s = line.decode("utf-8", "replace").rstrip()
            if s:
                self.last_error = s
                print("[app-server]", s[:300], file=sys.stderr, flush=True)

    def _reader(self):
        try:
            for line in iter(self.p.stdout.readline, b""):
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                mid = msg.get("id")
                if mid is not None and ("result" in msg or "error" in msg):
                    self._wire("in", None, mid,
                               msg.get("error") or msg.get("result"),
                               err="error" in msg)
                    q = self.pending.pop(mid, None)
                    if q:
                        q.put(msg)
                else:
                    # server->client request (approvals) or notification
                    self._broadcast(msg)
        except Exception as e:
            self.last_error = str(e)
        finally:
            self.alive = False
            self._broadcast({"method": "bridge/disconnected",
                             "params": {"error": self.last_error}})

    def _wire(self, direction, method, mid, payload, err=False):
        """Mirror protocol traffic to the UI so it can show what is actually
        being sent to the app-server. Truncated: some payloads are huge."""
        try:
            blob = json.dumps(payload)[:4000] if payload is not None else ""
        except Exception:
            blob = "<unserialisable>"
        self._broadcast({"method": "bridge/wire", "params": {
            "dir": direction, "rpc": method, "id": mid,
            "payload": blob, "err": err, "t": time.time()}})

    def _broadcast(self, msg):
        for q in list(self.subscribers):
            try:
                q.put_nowait(msg)
            except Exception:
                try: self.subscribers.remove(q)
                except ValueError: pass

    def _write(self, obj):
        data = (json.dumps(obj) + "\n").encode()
        with self.wlock:
            self.p.stdin.write(data)
            self.p.stdin.flush()

    def call(self, method, params=None, timeout=300):
        with self.idlock:
            self._id += 1
            mid = self._id
        q = queue.Queue()
        self.pending[mid] = q
        m = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            m["params"] = params
        self._wire("out", method, mid, params)
        self._write(m)
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            self.pending.pop(mid, None)
            return {"error": {"code": -1, "message": f"timeout waiting for {method}"}}


    def notify(self, method, params=None):
        m = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            m["params"] = params
        self._wire("out", method, None, params)
        self._write(m)

    def respond(self, req_id, result):
        """Answer a server->client request (e.g. an approval)."""
        self._write({"jsonrpc": "2.0", "id": req_id, "result": result})

    def respond_error(self, req_id, error):
        """Reject a server->client request with a JSON-RPC error object."""
        self._write({"jsonrpc": "2.0", "id": req_id, "error": error})

    def subscribe(self):
        q = queue.Queue(maxsize=4000)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        try: self.subscribers.remove(q)
        except ValueError: pass


APP = None

CWD_METHODS = frozenset({"thread/start", "thread/settings/update", "turn/start"})


def cwd_error(method, params):
    """Reject working directories that would make the next turn unlaunchable.

    The app-server persists cwd changes without checking that the directory
    exists. A typo therefore survives the settings update and the next turn
    fails before its command process can start. Validate at the shared bridge
    boundary so UI and raw /rpc callers get the same behavior.
    """
    if method not in CWD_METHODS or not isinstance(params, dict) or "cwd" not in params:
        return None
    path = params.get("cwd")
    if not isinstance(path, str) or not os.path.isabs(path):
        return "working directory must be an absolute path"
    if not os.path.isdir(path):
        return f"working directory does not exist or is not a directory: {path}"
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try: self.wfile.write(body)
        except Exception: pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(UI_DIR, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if self.path == "/events":
            return self._sse()
        if self.path.startswith("/transcript"):
            return self._transcript()
        if self.path.startswith("/itemtimes"):
            return self._itemtimes()
        if self.path == "/projects":
            return self._projects()
        if self.path.startswith("/threadmeta"):
            return self._threadmeta()
        if self.path == "/reload":
            # Push a reload to every open view. UI changes need only this --
            # restarting the app kills codex app-server (the bridge's child)
            # and aborts any running turn.
            APP._broadcast({"method": "bridge/reload", "params": {}})
            return self._send(200, b'{"ok":true}')
        if self.path == "/status":
            return self._send(200, json.dumps({
                "alive": APP.alive, "error": APP.last_error, "pid": APP.p.pid}).encode())
        return self._send(404, b"{}")

    def _transcript(self):
        """The WHOLE conversation, render-ready, straight from the rollout JSONL.

        The rollout is the authoritative record: ordered, timestamped, and
        complete -- it holds tool calls and their output, which
        `thread/turns/list` does not replay for legacy-history threads. Reading
        it directly removes the need for any client-side cache, which was the
        source of duplicated, misordered and mis-stamped messages.
        """
        from urllib.parse import urlparse, parse_qs
        import glob
        q = parse_qs(urlparse(self.path).query)
        tid = (q.get("id") or [""])[0]
        if not tid:
            return self._send(400, b'{"error":"no id"}')

        def text_of(c):
            if isinstance(c, str):
                return c
            out = ""
            if isinstance(c, list):
                for part in c:
                    if isinstance(part, dict):
                        out += part.get("text") or part.get("content") or ""
                    elif isinstance(part, str):
                        out += part
            return out

        rows, calls, revision = [], {}, ""
        last_timestamp = None
        timestamp_inversions = 0
        try:
            base = os.path.expanduser("~/.codex/sessions")
            files = sorted(f for f in glob.glob(os.path.join(base, "**", "*.jsonl"),
                                                recursive=True) if tid in f)
            stats = [os.stat(f) for f in files]
            revision = ";".join(
                f"{stat.st_mtime_ns}:{stat.st_size}" for stat in stats)
            for f in files:
                with open(f) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        if rec.get("type") != "response_item":
                            continue
                        pay = rec.get("payload") or {}
                        pt, ts = pay.get("type"), rec.get("timestamp")
                        if ts:
                            if last_timestamp is not None and ts < last_timestamp:
                                timestamp_inversions += 1
                            last_timestamp = ts

                        if pt == "message":
                            role = pay.get("role")
                            if role == "developer":
                                continue          # system scaffolding, not conversation
                            txt = text_of(pay.get("content"))
                            if not txt.strip():
                                continue
                            # Injected scaffolding is not conversation: a single
                            # <recommended_plugins> block is 36k chars and buries
                            # the start of the thread. Mark it so the client can
                            # collapse it instead of rendering it as a message.
                            m = re.match(r"\s*<([a-z_]+)>", txt)
                            tag = m.group(1) if m else None
                            if tag in ("recommended_plugins", "skill",
                                       "environment_context", "user_instructions"):
                                rows.append({"ts": ts, "cls": "scaffold",
                                             "who": tag.replace("_", " "),
                                             "text": txt})
                                continue
                            commentary = role == "assistant" and pay.get("phase") == "commentary"
                            rows.append({"ts": ts,
                                         "cls": "user" if role == "user" else
                                                ("rsn" if commentary else "agent"),
                                         "who": "you" if role == "user" else
                                                ("codex · thinking" if commentary else "codex"),
                                         "text": txt})
                        elif pt == "agent_message":
                            txt = text_of(pay.get("content"))
                            if txt.strip():
                                rows.append({"ts": ts, "cls": "agent",
                                             "who": f"agent {pay.get('author') or ''}".strip(),
                                             "text": txt})
                        elif pt in ("custom_tool_call", "function_call"):
                            cmd = pay.get("input") or pay.get("arguments") or ""
                            if not isinstance(cmd, str):
                                cmd = json.dumps(cmd)
                            idx = len(rows)
                            rows.append({"ts": ts, "cls": "cmd",
                                         "who": pay.get("name") or "tool",
                                         "text": cmd})
                            cid = pay.get("call_id")
                            if cid:
                                calls[cid] = idx
                        elif pt in ("custom_tool_call_output", "function_call_output"):
                            cid = pay.get("call_id")
                            out = pay.get("output")
                            if not isinstance(out, str):
                                out = json.dumps(out)
                            i = calls.get(cid)
                            if i is not None:
                                rows[i]["text"] += "\n\n" + out[:6000]
                            else:
                                rows.append({"ts": ts, "cls": "tool",
                                             "who": "output", "text": out[:6000]})
        except Exception as e:
            return self._send(200, json.dumps({"rows": [], "error": str(e)}).encode())

        warning = (f"source journal contains {timestamp_inversions} timestamp "
                   "inversion(s); displayed order follows source provenance"
                   if timestamp_inversions else "")
        return self._send(200, json.dumps({"rows": rows, "count": len(rows),
                                          "revision": revision,
                                          "journalWarning": warning}).encode())

    def _itemtimes(self):
        """Real per-message timestamps, from the thread's rollout JSONL.

        The protocol gives timestamps on TURNS only -- items carry none -- so a
        20-minute turn would stamp every message inside it identically. The
        rollout records each message with its own `timestamp`, but uses
        different ids (msg_...) than the API (item-N), so they cannot be joined
        by id. Both are chronological, and assistant-message counts match
        exactly, so we serve ordered per-role lists and let the client walk them
        in step with the items it renders.
        """
        from urllib.parse import urlparse, parse_qs
        import glob
        q = parse_qs(urlparse(self.path).query)
        tid = (q.get("id") or [""])[0]
        out = {"user": [], "assistant": []}
        if not tid:
            return self._send(400, b'{"error":"no id"}')
        try:
            base = os.path.expanduser("~/.codex/sessions")
            files = [f for f in glob.glob(os.path.join(base, "**", "*.jsonl"),
                                          recursive=True) if tid in f]
            for f in files:
                with open(f) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        if rec.get("type") != "response_item":
                            continue
                        pay = rec.get("payload") or {}
                        if pay.get("type") != "message":
                            continue
                        role = pay.get("role")
                        ts = rec.get("timestamp")
                        if role not in ("user", "assistant") or not ts:
                            continue
                        # Positional matching is WRONG: the rollout keeps
                        # developer/steering records the API collapses, so the
                        # Nth API message is not the Nth rollout message. Send a
                        # normalised head of the text so the client can match by
                        # CONTENT instead.
                        c = pay.get("content")
                        txt = ""
                        if isinstance(c, str):
                            txt = c
                        elif isinstance(c, list):
                            for part in c:
                                if isinstance(part, dict):
                                    txt += part.get("text") or part.get("content") or ""
                                elif isinstance(part, str):
                                    txt += part
                        head = " ".join(txt.split())[:120]
                        out[role].append({"ts": ts, "head": head})
        except Exception as e:
            out["error"] = str(e)
        return self._send(200, json.dumps(out).encode())

    # A thread's project is pinned in the registry that bin/codex-run writes.
    # threads.cwd is NOT it: a turn's --cwd overwrites that column, which used
    # to drag the conversation into whatever directory the work touched.
    REGISTRY = os.path.join(HERE, "state", "projects.json")

    def _registry(self):
        try:
            with open(self.REGISTRY, encoding="utf-8") as f:
                r = json.load(f)
            return r if isinstance(r, dict) else {}
        except Exception:
            return {}

    def _discover_projects(self, reg):
        """Resolve UI-created threads from Claude's registry and rollouts.

        Discovery is read-only. The bridge and CLI are separate processes; a
        GET handler rewriting their shared registry could lose a concurrent
        chat binding. Claude's registry plus immutable rollout metadata are the
        authority, so recalculating these fields is both safer and sufficient.
        """
        records = []
        db = os.path.expanduser("~/.codex/state_5.sqlite")
        try:
            connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            records = [dict(zip(("id", "rollout_path"), row)) for row in
                       connection.execute("SELECT id,rollout_path FROM threads")]
            connection.close()
        except Exception:
            pass
        reconcile_projects(reg, records)
        return reg

    def _projects(self):
        reg = self._discover_projects(self._registry())
        reg["defaultCwd"] = (os.environ.get("CODEX_DEFAULT_CWD")
                             or os.path.expanduser("~"))
        return self._send(200, json.dumps(reg).encode())

    def _threadmeta(self):
        """Effective per-thread settings. The protocol's thread object does not
        report model / sandbox / approval / reasoning effort, but the state DB
        records what was actually used -- which is what the user needs to see
        instead of the word "default"."""
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        tid = (q.get("id") or [""])[0]
        out = {}
        db = os.path.expanduser("~/.codex/state_5.sqlite")
        try:
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            cur = c.cursor()
            cur.execute("PRAGMA table_info(threads)")
            cols = [r[1] for r in cur.fetchall()]
            want = [w for w in ("model", "reasoning_effort", "sandbox_policy",
                                "approval_mode", "model_provider", "cwd",
                                "cli_version", "tokens_used", "history_mode")
                    if w in cols]
            if tid and want:
                cur.execute(f"SELECT {','.join(want)} FROM threads WHERE id=?", (tid,))
                row = cur.fetchone()
                if row:
                    out = dict(zip(want, row))
            c.close()
        except Exception as e:
            out = {"error": str(e)}
        pin = (self._discover_projects(self._registry()).get("threads") or {}).get(tid)
        if pin:
            out["project"] = pin.get("name")
            out["project_root"] = pin.get("root")
        else:
            # Runtime cwd is deliberately not project membership. Guessing here
            # recreates the relocation bug the registry exists to prevent.
            out["project_root"] = ""
            out["project"] = "(unassigned)"
        return self._send(200, json.dumps(out).encode())

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = APP.subscribe()
        try:
            self.wfile.write(b": open\n\n"); self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(b"data: " + json.dumps(msg).encode() + b"\n\n")
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except Exception:
            pass
        finally:
            APP.unsubscribe(q)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, b'{"error":"bad json"}')
        if self.path == "/respond":
            if "id" not in req:
                return self._send(400, b'{"error":"no id"}')
            if "error" in req:
                APP.respond_error(req["id"], req["error"])
            else:
                APP.respond(req["id"], req.get("result", {}))
            return self._send(200, b'{"ok":true}')
        if self.path != "/rpc":
            return self._send(404, b"{}")
        method = req.get("method")
        if not method:
            return self._send(400, b'{"error":"no method"}')
        invalid_cwd = cwd_error(method, req.get("params"))
        if invalid_cwd:
            body = {"error": {"code": -32602, "message": invalid_cwd}}
            return self._send(200, json.dumps(body).encode())
        if req.get("notify"):
            APP.notify(method, req.get("params"))
            return self._send(200, b'{"ok":true}')
        r = APP.call(method, req.get("params"), timeout=req.get("timeout", 300))
        return self._send(200, json.dumps(r).encode())


def main():
    global APP
    APP = AppServer()
    r = APP.call("initialize", {
        "clientInfo": {"name": "wheelhouse_bridge", "title": "Wheelhouse Bridge", "version": "0.1.0"},
        "capabilities": {"experimentalApi": True}}, timeout=30)
    if "result" not in r:
        print("initialize FAILED:", json.dumps(r)[:400]); sys.exit(1)
    APP.notify("initialized", {})
    print(f"app-server pid {APP.p.pid}; initialize ok  (codex: {CODEX})", flush=True)
    if SKILL_ROOTS:
        r = APP.call("skills/extraRoots/set", {"extraRoots": SKILL_ROOTS}, timeout=30)
        ok = "result" in r
        print(f"skill roots {'registered' if ok else 'FAILED'}: {SKILL_ROOTS}", flush=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    print(f"bridge on http://127.0.0.1:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try: APP.p.terminate()
        except Exception: pass


if __name__ == "__main__":
    main()
