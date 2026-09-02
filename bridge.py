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
import base64, glob, hashlib, json, os, queue, re, shutil, sqlite3, subprocess, sys, threading, time
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
DRAFTS = {}
DRAFT_LOCK = threading.Lock()
DRAFT_EPOCH = 0

TRANSCRIPT_PAGE_ROWS = 240
CURSOR_VERSION = 1


class TranscriptCursorError(ValueError):
    """A page cursor no longer identifies the same journal boundary."""


def _text_of(content):
    if isinstance(content, str):
        return content
    out = ""
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                out += part.get("text") or part.get("content") or ""
            elif isinstance(part, str):
                out += part
    return out


def _error_text(error):
    if isinstance(error, str):
        return error
    if not isinstance(error, dict):
        return "Unknown API error" if error is None else str(error)
    lines = []
    message = error.get("message")
    if message:
        lines.append(str(message))
    details = error.get("additionalDetails")
    if details and details != message:
        lines.append(str(details))
    info = error.get("codexErrorInfo", error.get("codex_error_info"))
    if info:
        if isinstance(info, dict):
            kind = info.get("type") or info.get("code") or json.dumps(info)
        else:
            kind = str(info)
        if kind and not any(kind in line for line in lines):
            lines.append(f"type: {kind}")
    return "\n".join(lines) or json.dumps(error)


def _rollout_sources(thread_id):
    base = os.path.expanduser("~/.codex/sessions")
    state_db = os.path.expanduser("~/.codex/state_5.sqlite")
    if os.path.isfile(state_db):
        try:
            connection = sqlite3.connect(state_db)
            try:
                row = connection.execute(
                    "SELECT rollout_path FROM threads WHERE id = ?", (thread_id,)
                ).fetchone()
            finally:
                connection.close()
            if row and row[0] and os.path.isfile(row[0]):
                path = row[0]
                return [{"path": path, "stat": os.stat(path)}]
        except (sqlite3.Error, OSError):
            pass
    paths = sorted(f for f in glob.glob(os.path.join(base, "**", "*.jsonl"),
                                        recursive=True) if thread_id in f)
    return [{"path": path, "stat": os.stat(path)} for path in paths]


def _revision(sources):
    return ";".join(f"{s['stat'].st_mtime_ns}:{s['stat'].st_size}" for s in sources)


def _generation(sources):
    identity = [[s["path"], s["stat"].st_dev, s["stat"].st_ino] for s in sources]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()[:24]


def _boundary_digest(source, offset):
    """Fingerprint the immutable prefix at a cursor while allowing appends."""
    size = source["stat"].st_size
    if offset < 0 or offset > size:
        raise TranscriptCursorError("cursor is beyond the journal")
    with open(source["path"], "rb") as fh:
        fh.seek(max(0, offset - 96))
        sample = fh.read(offset - max(0, offset - 96))
    return hashlib.sha256(sample).hexdigest()[:20]


def _encode_cursor(thread_id, sources, file_index, offset):
    if not sources:
        return ""
    payload = {"v": CURSOR_VERSION, "t": thread_id,
               "g": _generation(sources), "f": file_index, "o": offset,
               "b": _boundary_digest(sources[file_index], offset)}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value, thread_id, sources):
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(raw)
        file_index, offset = int(payload["f"]), int(payload["o"])
    except Exception as exc:
        raise TranscriptCursorError("malformed transcript cursor") from exc
    if payload.get("v") != CURSOR_VERSION or payload.get("t") != thread_id:
        raise TranscriptCursorError("transcript cursor belongs to another thread or version")
    if payload.get("g") != _generation(sources):
        raise TranscriptCursorError("transcript journal was replaced")
    if not 0 <= file_index < len(sources):
        raise TranscriptCursorError("transcript cursor names a missing journal")
    if payload.get("b") != _boundary_digest(sources[file_index], offset):
        raise TranscriptCursorError("transcript cursor boundary changed")
    return file_index, offset


def _first_coord(sources):
    return (0, 0) if sources else None


def _complete_file_end(source):
    """Byte boundary after the last newline-terminated record."""
    stop = source["stat"].st_size
    if not stop:
        return 0
    with open(source["path"], "rb") as fh:
        fh.seek(stop - 1)
        if fh.read(1) == b"\n":
            return stop
        position = stop
        while position:
            take = min(65536, position)
            position -= take
            fh.seek(position)
            chunk = fh.read(take)
            newline = chunk.rfind(b"\n")
            if newline >= 0:
                return position + newline + 1
    return 0


def _end_coord(sources):
    return (len(sources) - 1, _complete_file_end(sources[-1])) if sources else None


def _coord_before(a, b):
    return a[0] < b[0] or (a[0] == b[0] and a[1] < b[1])


def _iter_forward(sources, start, end=None):
    """Yield parsed JSONL records in physical byte order, never timestamp order."""
    if not sources:
        return
    for file_index in range(start[0], len(sources)):
        source = sources[file_index]
        offset = start[1] if file_index == start[0] else 0
        stop = (end[1] if end and file_index == end[0]
                else source["stat"].st_size)
        if end and file_index > end[0]:
            break
        with open(source["path"], "rb") as fh:
            fh.seek(offset)
            while fh.tell() < stop:
                line_start = fh.tell()
                line = fh.readline(stop - line_start)
                line_end = fh.tell()
                if not line:
                    break
                if not line.endswith(b"\n") and line_end == source["stat"].st_size:
                    break  # an append still in flight; only complete records are authoritative
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                yield (file_index, line_start), (file_index, line_end), record


def _iter_reverse_file(path, end_offset, block_size=65536):
    """Yield complete lines before end_offset with byte bounds, newest first."""
    with open(path, "rb") as fh:
        cursor = end_offset
        while cursor:
            fh.seek(cursor - 1)
            has_delimiter = fh.read(1) == b"\n"
            content_end = cursor - 1 if has_delimiter else cursor
            search_end, previous_newline = content_end, -1
            while search_end:
                start = max(0, search_end - block_size)
                fh.seek(start)
                index = fh.read(search_end - start).rfind(b"\n")
                if index >= 0:
                    previous_newline = start + index
                    break
                search_end = start
            line_start = previous_newline + 1
            fh.seek(line_start)
            line = fh.read(content_end - line_start)
            if line:
                yield line_start, cursor, line
            cursor = line_start


def _iter_reverse(sources, end):
    for file_index in range(end[0], -1, -1):
        stop = end[1] if file_index == end[0] else sources[file_index]["stat"].st_size
        size = sources[file_index]["stat"].st_size
        if stop == size and stop:
            stop = _complete_file_end(sources[file_index])
        for line_start, line_end, line in _iter_reverse_file(sources[file_index]["path"], stop):
            try:
                record = json.loads(line)
            except Exception:
                continue
            yield (file_index, line_start), (file_index, line_end), record


def _is_turn_start(record):
    payload = record.get("payload") or {}
    return record.get("type") == "event_msg" and payload.get("type") == "task_started"


def _row_id(coord, subindex=0):
    return f"r:{coord[0]}:{coord[1]}:{subindex}"


def _render_records(records):
    rows, calls = [], {}
    last_timestamp = None
    inversions = 0
    for coord, _line_end, rec in records:
        payload = rec.get("payload") or {}
        payload_type, timestamp = payload.get("type"), rec.get("timestamp")
        if timestamp:
            if last_timestamp is not None and timestamp < last_timestamp:
                inversions += 1
            last_timestamp = timestamp
        if rec.get("type") == "event_msg":
            if payload_type == "task_complete" and payload.get("error"):
                rows.append({"id": _row_id(coord), "ts": timestamp, "cls": "err",
                             "who": "API error", "text": _error_text(payload["error"])})
            continue
        if rec.get("type") != "response_item":
            continue
        if payload_type == "message":
            role = payload.get("role")
            if role == "developer":
                continue
            text = _text_of(payload.get("content"))
            if not text.strip():
                continue
            match = re.match(r"\s*<([a-z_]+)>", text)
            tag = match.group(1) if match else None
            if tag in ("recommended_plugins", "skill", "environment_context",
                       "user_instructions"):
                rows.append({"id": _row_id(coord), "ts": timestamp, "cls": "scaffold",
                             "who": tag.replace("_", " "), "text": text})
                continue
            commentary = role == "assistant" and payload.get("phase") == "commentary"
            rows.append({"id": _row_id(coord), "ts": timestamp,
                         "cls": "user" if role == "user" else
                                ("rsn" if commentary else "agent"),
                         "who": "you" if role == "user" else
                                ("codex · thinking" if commentary else "codex"),
                         "text": text})
        elif payload_type == "agent_message":
            text = _text_of(payload.get("content"))
            if text.strip():
                rows.append({"id": _row_id(coord), "ts": timestamp, "cls": "agent",
                             "who": f"agent {payload.get('author') or ''}".strip(),
                             "text": text})
        elif payload_type in ("custom_tool_call", "function_call"):
            command = payload.get("input") or payload.get("arguments") or ""
            if not isinstance(command, str):
                command = json.dumps(command)
            index = len(rows)
            rows.append({"id": _row_id(coord), "ts": timestamp, "cls": "cmd",
                         "who": payload.get("name") or "tool", "text": command})
            if payload.get("call_id"):
                calls[payload["call_id"]] = index
        elif payload_type in ("custom_tool_call_output", "function_call_output"):
            output = payload.get("output")
            if not isinstance(output, str):
                output = json.dumps(output)
            index = calls.get(payload.get("call_id"))
            if index is not None:
                rows[index]["text"] += "\n\n" + output[:6000]
            else:
                rows.append({"id": _row_id(coord), "ts": timestamp, "cls": "tool",
                             "who": "output", "text": output[:6000]})
    return rows, inversions


def _groups_before(sources, boundary, target_rows):
    groups, current, row_count = [], [], 0
    for record in _iter_reverse(sources, boundary):
        current.append(record)
        if _is_turn_start(record[2]):
            group = list(reversed(current))
            groups.append(group)
            row_count += len(_render_records(group)[0])
            current = []
            if row_count >= target_rows:
                break
    if current:
        groups.append(list(reversed(current)))
    groups.reverse()
    records = [record for group in groups for record in group]
    start = records[0][0] if records else boundary
    return records, start


def _groups_after(sources, boundary, target_rows):
    groups, current, row_count = [], [], 0
    end = _end_coord(sources) or boundary
    for record in _iter_forward(sources, boundary):
        if _is_turn_start(record[2]) and current:
            groups.append(current)
            row_count += len(_render_records(current)[0])
            if row_count >= target_rows:
                end = record[0]
                current = []
                break
            current = []
        current.append(record)
    if current:
        groups.append(current)
        end = current[-1][1]
    return [record for group in groups for record in group], end


def _page(thread_id, sources, *, direction="tail", cursor="", limit=TRANSCRIPT_PAGE_ROWS):
    if not sources:
        return {"rows": [], "revision": "", "generation": _generation(sources),
                "startCursor": "", "endCursor": "", "hasEarlier": False,
                "hasLater": False, "journalWarning": ""}
    beginning, journal_end = _first_coord(sources), _end_coord(sources)
    if direction == "tail":
        boundary = journal_end
        records, page_start = _groups_before(sources, boundary, limit)
        page_end = boundary
    elif direction == "before":
        boundary = _decode_cursor(cursor, thread_id, sources)
        records, page_start = _groups_before(sources, boundary, limit)
        page_end = boundary
    elif direction == "after":
        boundary = _decode_cursor(cursor, thread_id, sources)
        records, page_end = _groups_after(sources, boundary, limit)
        page_start = boundary
    else:
        raise TranscriptCursorError("unknown transcript page direction")
    rows, inversions = _render_records(records)
    warning = (f"source journal page contains {inversions} timestamp inversion(s); "
               "displayed order follows source provenance" if inversions else "")
    return {"rows": rows, "revision": _revision(sources),
            "generation": _generation(sources),
            "startCursor": _encode_cursor(thread_id, sources, *page_start),
            "endCursor": _encode_cursor(thread_id, sources, *page_end),
            "hasEarlier": _coord_before(beginning, page_start),
            "hasLater": _coord_before(page_end, journal_end),
            "journalWarning": warning}


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
        if self.path.startswith("/transcript/search"):
            return self._transcript_search()
        if self.path.startswith("/transcript"):
            return self._transcript()
        if self.path.startswith("/draft"):
            return self._draft()
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

    def _draft(self):
        """Return the unsent composer text for exactly one thread.

        Drafts deliberately live in bridge memory, not browser storage and not
        the transcript journal. They survive a WebView reload, remain isolated
        by thread id, and disappear when Wheelhouse itself exits.
        """
        from urllib.parse import urlparse, parse_qs
        tid = (parse_qs(urlparse(self.path).query).get("id") or [""])[0]
        if not tid:
            return self._send(400, b'{"error":"no id"}')
        global DRAFT_EPOCH
        with DRAFT_LOCK:
            text = (DRAFTS.get(tid) or {}).get("text", "")
            DRAFT_EPOCH += 1
            epoch = DRAFT_EPOCH
            DRAFTS[tid] = {"text": text, "epoch": epoch, "sequence": -1}
        return self._send(200, json.dumps({"text": text, "epoch": epoch}).encode())

    def _transcript(self):
        """Return one complete-turn page directly from the rollout journal."""
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        tid = (q.get("id") or [""])[0]
        known_revision = (q.get("revision") or [""])[0]
        if not tid:
            return self._send(400, b'{"error":"no id"}')
        try:
            sources = _rollout_sources(tid)
            revision = _revision(sources)
            if known_revision and known_revision == revision:
                return self._send(200, json.dumps({
                    "unchanged": True, "revision": revision,
                }).encode())
            direction = "tail"
            cursor = ""
            if (q.get("before") or [""])[0]:
                direction, cursor = "before", q["before"][0]
            elif (q.get("after") or [""])[0]:
                direction, cursor = "after", q["after"][0]
            limit = max(1, min(1000, int((q.get("limit") or [TRANSCRIPT_PAGE_ROWS])[0])))
            result = _page(tid, sources, direction=direction, cursor=cursor, limit=limit)
            return self._send(200, json.dumps(result).encode())
        except TranscriptCursorError as exc:
            return self._send(409, json.dumps({"error": str(exc),
                                               "cursorInvalid": True}).encode())
        except Exception as exc:
            return self._send(200, json.dumps({"rows": [], "error": str(exc)}).encode())

    def _transcript_search_events(self, tid, query, direction="forward", anchor=""):
        """Yield NDJSON-ready progress and one result without retaining an index."""
        sources = _rollout_sources(tid)
        total = sum(source["stat"].st_size for source in sources)
        needle = query.casefold()
        anchor_key = None
        match = re.fullmatch(r"r:(\d+):(\d+):(\d+)", anchor or "")
        if match:
            anchor_key = tuple(map(int, match.groups()))
        scanned_base = 0
        next_progress = 0
        first_match = last_match = None
        chosen = None
        records = []

        def consider(group):
            nonlocal first_match, last_match, chosen
            rows, _ = _render_records(group)
            for row in rows:
                if needle not in f"{row.get('who', '')}\n{row.get('text', '')}".casefold():
                    continue
                parts = tuple(map(int, row["id"].split(":")[1:]))
                candidate = (row, group[0][0])
                if first_match is None:
                    first_match = candidate
                last_match = candidate
                if direction == "backward":
                    if anchor_key is None or parts < anchor_key:
                        chosen = candidate
                elif chosen is None and (anchor_key is None or parts > anchor_key):
                    chosen = candidate

        for file_index, source in enumerate(sources):
            for record in _iter_forward(sources, (file_index, 0),
                                        (file_index, source["stat"].st_size)):
                if _is_turn_start(record[2]) and records:
                    consider(records)
                    records = []
                    if direction == "forward" and chosen is not None:
                        break
                records.append(record)
                scanned = scanned_base + record[1][1]
                if scanned >= next_progress:
                    yield {"type": "progress", "scanned": scanned, "total": total,
                           "percent": 100 if not total else min(100, scanned * 100 / total)}
                    next_progress = scanned + 1024 * 1024
            if direction == "forward" and chosen is not None:
                break
            if records:
                consider(records)
                records = []
            scanned_base += source["stat"].st_size
        if chosen is None:
            chosen = last_match if direction == "backward" else first_match
        yield {"type": "progress", "scanned": total, "total": total, "percent": 100}
        if chosen:
            row, group_start = chosen
            cursor = _encode_cursor(tid, sources, *group_start)
            page = _page(tid, sources, direction="after", cursor=cursor)
            yield {"type": "match", "rowId": row["id"], "page": page}
        else:
            yield {"type": "done", "found": False}

    def _transcript_search(self):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        tid = (q.get("id") or [""])[0]
        query = (q.get("q") or [""])[0]
        direction = (q.get("direction") or ["forward"])[0]
        anchor = (q.get("anchor") or [""])[0]
        if not tid or not query or direction not in ("forward", "backward"):
            return self._send(400, b'{"error":"id, q, and valid direction are required"}')
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for event in self._transcript_search_events(tid, query, direction, anchor):
                self.wfile.write(json.dumps(event).encode() + b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            try:
                self.wfile.write(json.dumps({"type": "error", "error": str(exc)}).encode() + b"\n")
                self.wfile.flush()
            except Exception:
                pass

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
        if self.path == "/draft":
            tid, text = req.get("id"), req.get("text")
            epoch, sequence = req.get("epoch"), req.get("sequence")
            if not isinstance(tid, str) or not tid:
                return self._send(400, b'{"error":"no id"}')
            if not isinstance(text, str):
                return self._send(400, b'{"error":"text must be a string"}')
            if not isinstance(epoch, int) or not isinstance(sequence, int):
                return self._send(400, b'{"error":"epoch and sequence must be integers"}')
            with DRAFT_LOCK:
                current = DRAFTS.get(tid)
                if (not current or current["epoch"] != epoch or
                        sequence <= current["sequence"]):
                    return self._send(409, b'{"error":"stale draft write"}')
                current.update(text=text, sequence=sequence)
            return self._send(200, b'{"ok":true}')
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
