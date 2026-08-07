"""A very small Chrome DevTools Protocol client — enough to take a screenshot.

Chrome's `--screenshot` flag is no use here: it fires on load and then EXITS the
browser, so it can never capture the state a suite has driven the page into. The
only way to photograph the page at the moment a suite finishes is to ask Chrome
over CDP, and CDP is a WebSocket.

So this speaks just enough RFC 6455 to do that: client handshake, masked text
frames out, unmasked (possibly fragmented, possibly 8-byte-length) frames in.
No dependency — the same rule the rest of this repo follows.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import urllib.request
from hashlib import sha1


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

    def call(self, method: str, params: dict | None = None, msg_id: int = 1) -> dict:
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
