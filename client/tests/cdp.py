"""A very small Chrome DevTools Protocol client — enough to take a screenshot, and
to ask V8 which lines of client/js actually ran.

Chrome's `--screenshot` flag is no use here: it fires on load and then EXITS the
browser, so it can never capture the state a suite has driven the page into. The
only way to photograph the page at the moment a suite finishes is to ask Chrome
over CDP, and CDP is a WebSocket.

So this speaks just enough RFC 6455 to do that: client handshake, masked text
frames out, unmasked (possibly fragmented, possibly 8-byte-length) frames in.
No dependency — the same rule the rest of this repo follows.

The second half of the file is coverage: Profiler.takePreciseCoverage gives back
character ranges of each script that ran, and Source maps those ranges onto the
lines of the file on disk. That half is deliberately arithmetic on a string rather
than a second CDP domain — see start_coverage and Source for why.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import urllib.request
from hashlib import sha1


def devtools_port(profile_dir: str, timeout: float = 20.0) -> int:
    """The debugging port Chrome actually opened, read from its own notebook.

    The runner launches with --remote-debugging-port=0 and asks here rather than
    picking a number itself. Picking one means finding a free port, closing it, and
    hoping it is still free when Chrome gets there — a race that is lost silently: the
    port is taken, Chrome fails to listen, and the only symptom is every screenshot
    going missing with a connection error that names nothing. Chrome writes the port it
    got to <user-data-dir>/DevToolsActivePort (line 1) the moment it is listening, on
    Windows, macOS and Linux alike.
    """
    import time
    f = os.path.join(profile_dir, "DevToolsActivePort")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            first = open(f, "r", encoding="utf-8").read().split("\n")[0].strip()
            if first.isdigit():
                return int(first)
        except OSError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Chrome never wrote {f} - no debugging port to photograph through")


def _page_ws_url(port: int, timeout: float = 15.0) -> str:
    """Find the page target. Chrome takes a moment to open the port after launch."""
    import time
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                targets = json.loads(r.read().decode("utf-8"))
            pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            if pages:
                return pages[0]["webSocketDebuggerUrl"]
            last = "no page target yet"
        except Exception as exc:  # noqa: BLE001 — Chrome is still starting
            last = str(exc)
        time.sleep(0.25)
    raise RuntimeError(f"CDP not reachable on port {port}: {last}")


class WS:
    def __init__(self, url: str, timeout: float = 30.0):
        # ws://127.0.0.1:PORT/devtools/page/ID
        self._id = 0
        rest = url.split("://", 1)[1]
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        expect = base64.b64encode(
            sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        head = self._read_until(b"\r\n\r\n").decode("latin-1")
        if "101" not in head.split("\r\n")[0] or expect.lower() not in head.lower():
            raise RuntimeError("WebSocket handshake refused by Chrome")
        self._buf = b""

    def _read_until(self, marker: bytes) -> bytes:
        buf = b""
        while marker not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("Chrome closed the connection during the handshake")
            buf += chunk
        return buf

    def _recv_exactly(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("Chrome closed the CDP connection")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, obj: dict) -> None:
        payload = json.dumps(obj).encode("utf-8")
        header = bytearray([0x81])                  # FIN + text
        n = len(payload)
        mask_bit = 0x80                             # clients MUST mask
        if n < 126:
            header.append(mask_bit | n)
        elif n < (1 << 16):
            header.append(mask_bit | 126)
            header += struct.pack(">H", n)
        else:
            header.append(mask_bit | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv(self) -> dict:
        """One complete message, reassembling continuation frames."""
        data = b""
        while True:
            b0, b1 = self._recv_exactly(2)
            fin = b0 & 0x80
            opcode = b0 & 0x0F
            length = b1 & 0x7F                      # server frames are never masked
            if length == 126:
                length = struct.unpack(">H", self._recv_exactly(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exactly(8))[0]
            payload = self._recv_exactly(length)
            if opcode == 0x9:                       # ping -> pong, or Chrome drops us
                self.sock.sendall(b"\x8a\x80" + os.urandom(4))
                continue
            if opcode == 0x8:
                raise RuntimeError("Chrome closed the CDP connection")
            data += payload
            if fin:
                return json.loads(data.decode("utf-8"))

    def call(self, method: str, params: dict | None = None,
             msg_id: int | None = None) -> dict:
        # An id of our own when the caller does not care. Screenshots are one call and
        # can name their own number; a coverage session makes a dozen down one socket,
        # and reusing an id there means a reply that arrives late is read as the answer
        # to a question asked afterwards.
        if msg_id is None:
            self._id += 1
            msg_id = self._id
        self.send({"id": msg_id, "method": method, "params": params or {}})
        while True:
            msg = self.recv()
            if msg.get("id") == msg_id:             # everything else is an event
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def close(self):
        try:
            self.sock.close()
        except Exception:  # noqa: BLE001
            pass


# Surfaces that are alive by nature and will never photograph the same way twice:
# satellite tiles arrive from the network asynchronously, and the vehicle is moving.
# Hidden ONLY for the layout portrait, never for the record shot.
#
# The second rule freezes every animation and transition. The console pulses things on
# purpose — a FLOOD hull, a crit alert chip, a blinking amber glyph — and a pulse is a
# different colour in every frame, so a portrait containing one is not reproducible.
# Measured: ballast-syringe drifted 0.006%-0.24% between identical runs with no code
# change at all, against a 0.10% tolerance. That is a gate that fails at random, and a
# gate that fails at random is one people learn to re-run until it passes. Freezing is
# the honest fix; raising the tolerance past the noise would have blinded the check to
# the very layout changes it exists to catch.
_HIDE_LIVE = ("var s=document.createElement('style');"
              "s.id='__test_layout';"
              "s.textContent='#map-canvas,#maplibre-map,#video-layer{visibility:hidden!important}"
              "*,*::before,*::after{animation:none!important;transition:none!important;"
              "animation-play-state:paused!important}';"
              "document.head.appendChild(s); true")


def capture_png(port: int, hide_live: bool = False) -> bytes:
    """PNG bytes of the page as it stands right now.

    hide_live=True suppresses the map imagery and the video before the shot. Those
    are the only genuinely non-deterministic surfaces on the screen, and leaving
    them in makes two identical runs differ by more than half their pixels — which
    turns a regression check into noise, and noise into something everyone ignores.
    """
    ws = WS(_page_ws_url(port))
    try:
        if hide_live:
            ws.call("Runtime.evaluate", {"expression": _HIDE_LIVE, "returnByValue": True}, msg_id=7)
        res = ws.call("Page.captureScreenshot",
                      {"format": "png", "captureBeyondViewport": False}, msg_id=8)
        return base64.b64decode(res["data"])
    finally:
        ws.close()


# ---------------------------------------------------------------------------
# COVERAGE — which lines of client/js a run actually executed
# ---------------------------------------------------------------------------

def start_coverage(port: int) -> WS:
    """Switch V8's precise coverage on, BEFORE the page under test has loaded.

    Two facts about Profiler.startPreciseCoverage decide the shape of everything
    around it:

      * IT COUNTS FROM THE MOMENT IT IS CALLED. A session opened after the dashboard
        has loaded has already missed every line that ran during boot, which is most
        of them, and would report a console that barely executes. So when coverage is
        asked for the runner launches Chrome at about:blank, starts the profiler here,
        and only then navigates to the suite URL.
      * IT BELONGS TO THIS CLIENT. Chrome resets the domains a client enabled when
        that client disconnects, so the socket returned here has to stay open for the
        whole life of the suite. Closing it and reconnecting to take the numbers at
        the end collects nothing at all — which looks exactly like a console that ran
        no code, and is the one failure mode that could be mistaken for a finding.

    detailed=True asks for block-level ranges: an if-branch that never ran comes back
    as a range with count 0 nested inside a function whose count is 1. Without it the
    answer is only "was this function called", which is not line coverage. callCount
    is False because nothing here counts how OFTEN a line ran, only whether it did.
    """
    ws = page_session(port)
    ws.call("Profiler.enable")
    ws.call("Profiler.startPreciseCoverage", {"callCount": False, "detailed": True})
    return ws


def page_session(port: int) -> WS:
    """A CDP session on the page target, for a caller that will keep it.

    Public because the runner needs a plain one as a fallback: when coverage cannot be
    started the page is still sitting on about:blank waiting to be sent somewhere, and
    a suite that never loaded because of the MEASUREMENT would be reported as a suite
    that never loaded, which is a finding about the console that is not true.
    """
    return WS(_page_ws_url(port))


def navigate(ws: WS, url: str) -> None:
    """Send the page to the suite URL. See start_coverage for why it is not the launch URL."""
    ws.call("Page.navigate", {"url": url})


def take_coverage(ws: WS) -> list[dict]:
    """What has run so far, one entry per SCRIPT: {url, scriptId, functions: [...]}.

    Per script and NOT collapsed per URL on purpose. A suite may boot a second copy of
    the whole console in an iframe (crt-overlay does, to test the ?sim=1 page as a real
    page), and that produces two scripts with the same URL, different scriptIds and
    different numbers. The only correct way to combine them is to work out what each
    one covered and take the union of the lines: merging their function lists first
    would let one script's "this never ran" range overwrite the other's "it ran".
    """
    return [e for e in ws.call("Profiler.takePreciseCoverage").get("result", [])
            if e.get("url")]


_IDENT = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")
# After one of these a '/' opens a regular expression; after anything else (a name, a
# number, a ')' or a ']') it is division. Getting this wrong is not fatal — see _scan.
_BEFORE_REGEX = frozenset("(,=:[!&|?{};+-*%^~<>")
_REGEX_WORDS = frozenset(
    "return typeof instanceof in of new delete void case do else yield await throw".split())


def _scan(text: str) -> tuple[bytearray, list[int]]:
    """(mask, line_starts). mask[i] is 1 where character i is CODE.

    Code means: not whitespace, and not inside a comment. Everything else counts —
    a string literal is code, a lone `}` is code — because the question this mask
    exists to answer is "did the thing on this line run", and both of those have an
    answer. Comments and blank lines have no answer, so they are not asked and never
    land in a denominator.

    WHY A SCANNER AND NOT A REGEX. `"http://"` is not a comment and `/["']/` is not a
    string, and a stripper that gets either wrong swallows the rest of the file. So
    this walks the text in the six states JavaScript actually has, and it is written
    to FAIL SHORT rather than fail long: an unterminated string or a misjudged regular
    expression is abandoned at the end of its line, because neither can legally span
    one. The worst a wrong guess can do is misjudge the one line it is on.

    Offsets are UTF-16 code units, because that is what V8 reports: an astral
    character is one Python character and two units, so it is emitted twice. Line
    endings are left exactly as they are on disk — the file is served byte for byte,
    so a CRLF file is two units per line ending in V8's arithmetic too, and reading
    the file in text mode (which would eat the \\r) shifts every offset after line one.
    """
    mask = bytearray()
    line_starts = [0]
    i, n = 0, len(text)
    state = "code"
    quote = ""          # which quote character opened the current string
    esc = False         # the previous character was a backslash
    in_class = False    # inside [...] of a regular expression, where / is literal
    subs: list[int] = []  # brace depth inside each open ${ } of a template literal
    prev = ""           # the last code character, for the regex/division decision
    word = ""           # the identifier ending at it, if it is one
    gap = False         # whitespace since that character

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        here = state                       # the state this character is judged in

        if state == "code":
            if ch == "/" and nxt == "/":
                # `here` moves too, so the slash that OPENS the comment is not counted
                # as code. Without that a comment-only line has one code character on
                # it and lands in the denominator - which on files this heavily
                # commented is most of them, and every percentage comes out lower for
                # a reason that has nothing to do with what ran.
                state = here = "line"
            elif ch == "/" and nxt == "*":
                state = here = "block"
            elif ch == "/" and (prev == "" or prev in _BEFORE_REGEX or word in _REGEX_WORDS):
                state, in_class = "regex", False
            elif ch in "\"'":
                state, quote, esc = "string", ch, False
            elif ch == "`":
                state, esc = "template", False
            elif ch == "{" and subs:
                subs[-1] += 1
            elif ch == "}" and subs:
                if subs[-1] == 0:
                    subs.pop()
                    state, esc = "template", False
                else:
                    subs[-1] -= 1
        elif state == "line":
            if ch == "\n":
                state = "code"
        elif state == "block":
            if ch == "*" and nxt == "/":
                # Both characters belong to the comment; leave together so a `*/` at
                # the end of a line cannot be read as the start of anything.
                mask += b"\x00\x00"
                i += 2
                state = "code"
                continue
        elif state == "string":
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                state = "code"
            elif ch == "\n":
                state = "code"          # unterminated: a JS string cannot cross a line
        elif state == "template":
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "`":
                state = "code"
            elif ch == "$" and nxt == "{":
                subs.append(0)
                mask += b"\x01\x01"
                i += 2
                state = "code"
                prev, word, gap = "{", "", False
                continue
        elif state == "regex":
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "[":
                in_class = True
            elif ch == "]":
                in_class = False
            elif ch == "/" and not in_class:
                state = "code"
            elif ch == "\n":
                state = "code"          # a regex cannot cross a line either

        is_code = not ch.isspace() and here not in ("line", "block")
        v = 1 if is_code else 0
        mask.append(v)
        if ord(ch) > 0xFFFF:
            mask.append(v)              # one Python character, two UTF-16 units
        if ch == "\n":
            line_starts.append(len(mask))

        if here == "code" and ch.isspace():
            gap = True                  # ends an identifier without forgetting it
        elif is_code:
            if here == "code" and ch in _IDENT:
                word = ch if (gap or prev not in _IDENT) else word + ch
            else:
                word = ""
            prev, gap = ch, False
        i += 1

    return mask, line_starts


class Source:
    """One client script, as V8 measured it and as it sits on disk.

    Built once per file per run; asked once per suite what that suite covered.
    """

    def __init__(self, text: str):
        self._mask, self._starts = _scan(text)
        self.units = len(self._mask)                # length in V8's own arithmetic
        self.lines = len(self._starts)
        self.code_lines = frozenset(
            ln for ln in range(1, self.lines + 1) if 1 in self._line_slice(self._mask, ln))

    def _line_slice(self, buf, ln: int):
        start = self._starts[ln - 1]
        end = self._starts[ln] if ln < self.lines else len(buf)
        return buf[start:end]

    def covered_lines(self, functions: list[dict]) -> set[int] | None:
        """Which CODE lines this script entry executed, or None if it is not this file.

        None is a cannot-tell and is reported as one. V8's offsets are into the source
        the browser was served; if that is not the source read from disk — a file edited
        mid-run, a stale service-worker copy, a path that matched the wrong file — then
        every offset points somewhere else and the number produced would be confident
        and wrong. The length of the script's own top-level range is the check: it is
        the whole file, so if it disagrees with what was read, nothing here is claimed.

        Ranges arrive as a tree: a function's whole extent, then the blocks inside it
        that ran a different number of times. Applying them outermost-first (start
        ascending, then longest first) lets each nested range correct its parent, which
        is exactly what "the function ran but this else-branch did not" means.
        """
        spans = [(r["startOffset"], r["endOffset"], r.get("count", 0))
                 for fn in functions for r in fn.get("ranges", ())]
        if not spans:
            return None
        if max(e for _, e, _ in spans) != self.units:
            return None
        cov = bytearray(self.units)
        for start, end, count in sorted(spans, key=lambda s: (s[0], -s[1])):
            cov[start:end] = b"\x01" * (end - start) if count else bytes(end - start)
        hit = set()
        for ln in self.code_lines:
            code = self._line_slice(self._mask, ln)
            ran = self._line_slice(cov, ln)
            if any(c and r for c, r in zip(code, ran)):
                hit.add(ln)
        return hit
