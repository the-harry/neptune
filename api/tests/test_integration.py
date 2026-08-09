"""THE WHOLE CHAIN, END TO END — a real server on a real socket, and what the console
would draw from the bytes that come out of it.

WHY THIS SUITE EXISTS WHEN test_liveness ALREADY WALKS THE CHAIN. It does, and it stops
one file short of the operator twice over. test_liveness builds the frame in-process and
reads `json.loads(Telemetry.model_dump_json())` — the bytes, but never sent — and it ends
at the wire, because the client is JavaScript and this is a python suite. The two ends it
leaves unproven are the two ends nobody owns:

  * THE SOCKET. Between `model_dump_json()` and a handheld there is api/main.py's control
    loop, `fill_nav_fields()` stitching navigation's answers over the top, a
    ConnectionManager, a websocket and a TCP connection. Every one of those is a place a
    null can be replaced by something friendlier, and none of them is exercised by
    building a frame and looking at it. Round three's defect lived in exactly that gap:
    rov.py sent `heading=None` and the client received `heading=0.0 card='N'`, because
    two files between them each did something locally defensible.

  * THE LAST FEW CENTIMETRES. client/tests/suites/sensor-loss.js proves the pixels —
    but it proves them against a HAND-WRITTEN frame (`BASE` in that file). A fixture is
    an assumption about what the vehicle sends, and an assumption that is never measured
    is a place the two halves of the system can drift apart while both suites stay green:
    the browser would go on proving that a null renders as '?' while the vehicle quietly
    stopped sending one. So this suite measures the REAL frame and asserts it satisfies
    the contract that browser suite assumes, field by field.

WHAT IS DRIVEN, AND HOW. uvicorn serving api/main.py as shipped, on a port the OS chose,
with the vehicle forced to MockHardware; a websocket client made of `socket` and `struct`
because api/tests may not grow dependencies; an origin POSTed over the same real port so
NAVIGATION IS ACTUALLY ANSWERING while the sensors are killed — which is the only
arrangement in which `fill_nav_fields()`'s heading precedence is under test at all. With
no origin the estimator has nothing to say, nav's answers never enter the frame, and the
one file that broke round three is never asked a question.

Sensors are killed by calling `MockHardware._kill_sensor` / `_stall_*` on the server's own
hardware object. That is a reach into the process rather than a message on the wire, and
it has to be: there is no command that unplugs a chip, and the failure being reproduced is
a connector coming off at 4 m. Everything MEASURED afterwards comes back over the socket.

EVERY PART THE BENCH CAN BREAK, AND THE FIVE THINGS ASKED OF EACH:

    _kill_sensor("ms5837")        depth / pressure                    I2C chip
    _kill_sensor("bno085")        bearing / cardinal / mag_cal /      I2C chip
                                  turn rate / surge / pitch / roll
    _kill_sensor("ina219")        pack volts / amps                   I2C chip
    _stall_leak_sampling(True)    the hull's own state                GPIO, no bus
    _stall_sensor_thread(True)    the loop that samples all of it     software

  1. healthy — the reading is on the wire and is a NUMBER;
  2. the part is stopped MID-STREAM, with the simulation underneath still running;
  3. the wire goes to cannot-tell AND `sensor_faults` NAMES the part;
  4. the reading never becomes a plausible number instead — `0.0` heading is due north,
     `0.0` depth is the surface, `mag_cal` 0 is a compass answering "do not trust me",
     `0.0` V is a claim about a pack, leak `NORMAL` is a positive claim the hull is dry,
     and four bars is a claim the tether is up;
  5. the part is mended and the reading comes back — recovery is half the contract and
     the half that gets skipped.

Then, for every one of those states, WHAT THE CONSOLE WOULD DRAW. There is no browser
here, so the console's own resolution functions are read off disk: the vocabularies
(`LEAK_RANK`, `SENSOR_CHIPS`, `SENSOR_BEHIND`, `HEADING_FLAGS`) are PARSED OUT OF
client/js/core.js at run time rather than copied, and the branch shapes this file mirrors
are PINNED to the exact source text they mirror (see `PINS`). A console that changes its
mind therefore breaks this suite loudly instead of drifting away from it in silence. What
the mirror answers is a decision, never a picture: '?' or a number, which drop, which
badge, which chips on the alert rail. The pixels are client/tests/suites/sensor-loss.js's
job and this file does not pretend otherwise.

TWO KNOWN GAPS, STATED RATHER THAN PAPERED OVER, so a green run cannot be read as a claim
about either.

  * `signal` is a cannot-tell that reaches the wire and stops there. A stalled sensor
    loop sends -1 rather than a frozen four bars, and nothing in client/js reads
    `t.signal` at all, so no readout, glyph or chip changes when it arrives. The
    vehicle's honesty about the link bars is real and IS asserted below; its survival to
    a render decision is not, because there is no render decision to survive to. Same for
    `cpu_c` / `ram_pct` / `disk_gb`, which net.js ingests and nothing draws.

  * `headingFlag()` does not apply the veto `viewFromState()` does. The bearing NUMBER
    goes through a gate that lets the hull's own `sensor_faults` overrule a value it is
    still shipping; the BADGE beside it reads `state.heading` and `state.magCal` straight.
    On this vehicle they cannot disagree, because api/rov.py takes the bearing and the
    calibration mark off one hardware handle and nulls them together — which is a
    property of the wire, so it is asserted here on every frame rather than trusted.

stdlib unittest only, matching the rest of api/tests — see run.py for why there is no
framework here.
"""

from __future__ import annotations

import base64
import contextlib
import http.client
import importlib.util
import json
import logging
import os
import re
import socket
import struct
import sys
import tempfile
import threading
import time
import traceback
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Isolation, BEFORE anything reads the environment
# ---------------------------------------------------------------------------
# This module boots the REAL app, and a real boot writes: a blackbox session journal, a
# dive journal once an origin is set, and — through NavService._recover_orphans — a
# rebuilt .geojson for any journal that never got a clean stop. Dive records are the one
# thing this project produces that cannot be regenerated by re-running anything, so a
# suite that sets a launch point is not allowed anywhere near the real data/ directory.
#
# setdefault, not assignment: whichever test module in this run got here first has
# already pointed the settings at ITS temp tree, and the settings objects are frozen
# dataclasses built at import time — so overwriting the variables now would change the
# environment without changing where anything is written, which is worse than either
# answer on its own. The belt-and-braces check below inspects the RESULT rather than
# trusting the assignment.
_TMP = Path(tempfile.mkdtemp(prefix="neptune-integration-"))
os.environ.setdefault("NAV_DATA_DIR", str(_TMP))
os.environ.setdefault("NAV_DIVES_DIR", str(_TMP / "dives"))
os.environ.setdefault("NAV_AREAS_DIR", str(_TMP / "areas"))
os.environ.setdefault("NAV_LUT_DIR", str(_TMP / "speed_luts"))
os.environ.setdefault("ROV_LOG_DIR", str(_TMP / "log"))
# The vehicle is SIMULATED for this suite, and that is a safety rule rather than a
# convenience: the checks below arm the vehicle and hold the throttle open so the
# simulated world keeps moving underneath a dead sensor. Run against RealHardware on the
# Pi that would spin both propellers in a workshop.
os.environ.setdefault("NEPTUNE_HW", "mock")
# Neither the Trust's national network nor a tile server is something to reach for while
# measuring a socket. Forced again in setUpModule, because by now it has been read.
os.environ.setdefault("NAV_CRT_NATIONAL_AUTO", "0")
# The WOLFANG camera is genuinely not on this bench's network and the boot waits for its
# probe to time out. Shortening the wait changes how long the boot takes and nothing
# about what it finds.
os.environ.setdefault("WOLFANG_T_FAST", "0.25")
os.environ.setdefault("WOLFANG_T_SLOW", "0.25")

import uvicorn  # noqa: E402

from config import settings  # noqa: E402
from nav.config import settings as nav_settings  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
CLIENT_JS = REPO / "client" / "js"
CLIENT_SUITES = REPO / "client" / "tests" / "suites"


def _load_app_under_test():
    """Execute api/main.py into an app object of this suite's own, and return it.

    NOT `import main`, and the difference matters. `main.app` is a module-level singleton
    with module-level state hanging off it — one BlackBox, one ConnectionManager, one
    NavService, one camera control plane — and by the time this suite runs, another suite
    in the same process has usually already booted that app's lifespan and shut it down.
    Booting the same app a second time is not the same operation as booting it once:
    the lifespan CLOSES `app.state.bb` on the way out while `ws_control` journals every
    new client, and `camera/cgi.py` cannot be restarted at all (see the same note in
    tests/test_network.py, which does this for the same reason).

    Executing the file gives a FIRST boot of a fresh app — a fresh CameraService, hence a
    fresh CgiClient, hence none of that — which is precisely what `uvicorn main:app` gets
    in production. Nothing is stubbed and nothing is skipped.
    """
    path = Path(__file__).resolve().parent.parent / "main.py"
    spec = importlib.util.spec_from_file_location("neptune_main_integration", path)
    if spec is None or spec.loader is None:  # pragma: no cover — main.py is right there
        raise ImportError(f"could not load the app under test from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# api/main.py runs `logging.basicConfig()` when it executes, and this module has to
# execute it at MODULE scope so that a bench missing fastapi/pydantic/uvicorn is reported
# by run.py as DEPS ("never loaded: needs X") rather than as a wall of failing checks.
# Those two facts collide: discovery imports every test_*.py, so merely HAVING this file
# would install a root handler and set the root level, and every other suite in the run
# would start printing INFO lines it has never printed before. The root logger is put
# back exactly as it was found, and the vehicle's own logger is held quiet for the
# duration so the records main.py emits on the way up do not land on a runner that did
# not ask for them.
_root = logging.getLogger()
_saved_root = (_root.handlers[:], _root.level)
_neptune_log = logging.getLogger("neptune")
_saved_neptune = _neptune_log.level
_neptune_log.setLevel(logging.ERROR)
try:
    neptune = _load_app_under_test()
finally:
    _neptune_log.setLevel(_saved_neptune)
    _root.handlers[:] = _saved_root[0]
    _root.setLevel(_saved_root[1])

# Belt and braces on the isolation above. nav.config caches its settings at import, and
# if any module in this process got there first the environment arrived too late — in
# which case this suite would write dives into the operator's real data directory. A
# frozen dataclass is frozen against accident, not against a test that says why.
for _attr, _sub in (("data_dir", ""), ("dives_dir", "dives"), ("areas_dir", "areas"), ("speed_lut_dir", "speed_luts")):
    _want = _TMP / _sub if _sub else _TMP
    if not str(getattr(nav_settings, _attr)).startswith(tempfile.gettempdir()):
        object.__setattr__(nav_settings, _attr, _want)


# ---------------------------------------------------------------------------
# The knobs, named once
# ---------------------------------------------------------------------------
BIND_HOST = "127.0.0.1"  # loopback only — a test must not serve a network
CONTROL_PATH = "/ws/control"

DEFAULT_TIMEOUT_S = 6.0
SERVER_START_TIMEOUT_S = 45.0  # a cold boot brings up nav + the camera probe
SERVER_STOP_TIMEOUT_S = 30.0

# How long each phase is flown. Long enough that the control loop turns many times
# (~15 Hz) and short enough that five parts fit in a suite somebody will actually run.
SETTLE_S = 1.2  # healthy, and again after mending
BROKEN_S = 1.6  # with the part stopped
# Flown and thrown away at the start of every phase, so that nothing already in flight
# when a part was broken can be counted as evidence about the break. ~4 frames at the
# shipped rate — far more than the one period a frame can be mid-build for, far less
# than the phase it precedes. See Dive.drain() for the flake this closes.
LEAD_IN_S = 0.3
# The fewest frames a phase may consist of and still be a measurement. Three is not a
# threshold anybody tuned: it is "more than one, so a single freak frame cannot be the
# whole evidence". A phase that cannot produce it means the control loop is not turning,
# which is a finding in itself and is raised as one rather than skipped past.
MIN_FRAMES = 3

# A launch point on the Birmingham & Fazeley, which is where this vehicle is flown. The
# numbers only have to be a real place on the water; nothing here downloads anything
# (?fetch=false), because a suite measuring a socket may not depend on a hotspot.
ORIGIN = {"lat": 52.48, "lon": -1.90, "accuracy": 5.0, "source": "manual"}

# Frame opcodes (RFC 6455 §5.2), spelled out because this file writes them by hand.
OP_TEXT, OP_CLOSE, OP_PING, OP_PONG = 0x1, 0x8, 0x9, 0xA


class WsClosed(Exception):
    """The server ended this socket. `code` is the close code, or None for an EOF."""

    def __init__(self, code: int | None = None, reason: str = "") -> None:
        super().__init__(f"closed (code={code}) {reason}".strip())
        self.code = code
        self.reason = reason


class Ws:
    """One client connection, made of nothing but a socket.

    NO NEW DEPENDENCIES, and none needed: this is a well-behaved client that connects,
    reads telemetry and sends control frames, which is a few dozen lines of RFC 6455.
    tests/test_network.py holds a deliberately BADLY behaved one for the socket-abuse
    checks; borrowing it here would tie this suite's ability to load to that one's, and
    would drag its module-scope app boot into every run of this file. Two suites that
    cannot be run apart are one suite with two names.
    """

    def __init__(self, port: int, path: str = CONTROL_PATH, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        self.path = path
        self.timeout = timeout
        self._buf = b""
        self.status = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        try:
            self._handshake(port, path)
        except BaseException:
            # A connection that never came up must not survive as an open descriptor:
            # the server would go on counting a client that is not there.
            self.close()
            raise

    def _handshake(self, port: int, path: str) -> None:
        self.sock.connect((BIND_HOST, port))
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        self.sock.sendall(
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {BIND_HOST}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n".encode("ascii")
        )
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            head += chunk
        head, _, self._buf = head.partition(b"\r\n\r\n")
        line = head.decode("latin-1").splitlines()[0] if head else ""
        try:
            self.status = int(line.split(" ")[1])
        except (IndexError, ValueError):
            self.status = 0
        if self.status != 101:
            raise WsClosed(None, f"the upgrade to {path} answered {line!r}, not 101")

    @staticmethod
    def _frame(opcode: int, payload: bytes) -> bytes:
        """One final, masked client frame. Clients MUST mask (RFC 6455 §5.3)."""
        n = len(payload)
        out = bytes([0x80 | opcode])
        if n < 126:
            out += bytes([0x80 | n])
        elif n <= 0xFFFF:
            out += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            out += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        return out + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    def send_json(self, obj: dict) -> None:
        self.sock.sendall(self._frame(OP_TEXT, json.dumps(obj).encode("utf-8")))

    def _fill(self, deadline: float) -> None:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(f"nothing arrived on {self.path} before the deadline")
        self.sock.settimeout(left)
        chunk = self.sock.recv(65536)
        if not chunk:
            raise WsClosed(None, "the peer closed the stream without a close frame")
        self._buf += chunk

    def _need(self, n: int, deadline: float) -> None:
        while len(self._buf) < n:
            self._fill(deadline)

    def _recv_frame(self, deadline: float) -> tuple[int, bytes]:
        self._need(2, deadline)
        opcode = self._buf[0] & 0x0F
        n = self._buf[1] & 0x7F
        off = 2
        if n == 126:
            self._need(4, deadline)
            n = struct.unpack("!H", self._buf[2:4])[0]
            off = 4
        elif n == 127:
            self._need(10, deadline)
            n = struct.unpack("!Q", self._buf[2:10])[0]
            off = 10
        self._need(off + n, deadline)
        payload = self._buf[off : off + n]
        self._buf = self._buf[off + n :]
        return opcode, payload

    def recv_json(self, timeout: float) -> dict:
        """The next application message, answering pings on the way past."""
        deadline = time.monotonic() + timeout
        while True:
            opcode, payload = self._recv_frame(deadline)
            if opcode == OP_PING:
                self.sock.sendall(self._frame(OP_PONG, payload))
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else None
                raise WsClosed(code, payload[2:].decode("utf-8", "replace"))
            parsed = json.loads(payload.decode("utf-8"))
            if not isinstance(parsed, dict):  # pragma: no cover — the vehicle sends objects
                raise AssertionError(f"the vehicle sent a {type(parsed).__name__}, not a JSON object")
            return parsed

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()


class Server:
    """uvicorn on its own thread, bound to a port the OS chose.

    PORT ZERO, READ BACK. The listening socket is created and bound HERE, before uvicorn
    exists, which is the only arrangement in which the assigned port is known before
    anything starts serving on it. Nothing in this file may contain a port number: the
    bench that matters most is the one where the operator has left the real server
    running on 8000, and a suite that collides with it reports a broken vehicle.

    THE EVENT LOOP IS PINNED to stdlib asyncio. uvicorn's "auto" loop calls
    `uvloop.install()` where uvloop is present (the Pi), and that sets a PROCESS-WIDE
    policy every later `asyncio.run` in this test process would inherit. The property
    under test is the app's, not the loop's.
    """

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind((BIND_HOST, 0))
        self.port = int(self.sock.getsockname()[1])
        self._server = uvicorn.Server(
            uvicorn.Config(
                neptune.app,
                log_config=None,
                log_level="critical",
                access_log=False,
                lifespan="on",
                loop="asyncio",
            )
        )
        self._thread: threading.Thread | None = None

    def start(self) -> "Server":
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [self.sock]}, name="neptune-integration-server", daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + SERVER_START_TIMEOUT_S
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError("the server thread ended before the app came up")
            if time.monotonic() > deadline:
                # WHERE IT IS STUCK, NOT MERELY THAT IT IS. A boot that hangs has no wire
                # to inspect and no assertion message to read, and "it did not come up"
                # sends the next person to bisect the suite order by hand.
                where = self._thread_stack()
                self.stop()
                raise RuntimeError(
                    f"the app did not come up within {SERVER_START_TIMEOUT_S:.0f}s; the "
                    f"server thread was here:\n{where}"
                )
            time.sleep(0.02)
        return self

    def _thread_stack(self) -> str:
        ident = self._thread.ident if self._thread is not None else None
        frame = sys._current_frames().get(ident) if ident is not None else None
        if frame is None:
            return "  (the server thread has no frame to report)"
        return "".join(traceback.format_stack(frame))

    def stop(self) -> bool:
        """Ask uvicorn to come down and wait for it. True if the thread actually ended."""
        self._server.should_exit = True
        alive = False
        if self._thread is not None:
            self._thread.join(SERVER_STOP_TIMEOUT_S)
            alive = self._thread.is_alive()
        with contextlib.suppress(OSError):
            self.sock.close()
        return not alive

    def post(self, path: str, body: dict, timeout: float = DEFAULT_TIMEOUT_S) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection(BIND_HOST, self.port, timeout=timeout)
        try:
            conn.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
            res = conn.getresponse()
            return res.status, res.read()
        finally:
            conn.close()


# ===========================================================================
# WHAT THE CONSOLE WOULD DRAW — the client's own decisions, read off disk
# ===========================================================================
# There is no browser in this process, so the console cannot be RUN here. What can be
# done — and is the only thing this file claims to do — is to read the console's own
# resolution functions and assert that the frames a real vehicle emits satisfy the
# contract those functions are written against.
#
# TWO DEFENCES AGAINST THIS BECOMING A LIE OF ITS OWN, because a python re-statement of
# JavaScript is a copy, and a copy drifts:
#
#   1. THE VOCABULARIES ARE NOT COPIED. LEAK_RANK, SENSOR_CHIPS, SENSOR_BEHIND and
#      HEADING_FLAGS are parsed out of client/js/core.js every time this suite runs, so
#      renaming a chip or adding a stage changes what is asserted here in the same commit
#      it changes the console. A table that cannot be found is a hard failure, never an
#      empty default: an extraction that quietly returns {} would make every check below
#      pass while measuring nothing.
#
#   2. THE BRANCHES ARE PINNED. Every decision this file mirrors names the exact line of
#      client source it mirrors (PINS). Whitespace is normalised, so re-indenting is free;
#      changing what the line DOES breaks this suite and sends whoever changed it here to
#      re-derive the mirror. That is the intended cost — the alternative is a mirror that
#      goes on asserting last month's console.
#
# What is mirrored is a DECISION, never a picture: '?' or a number, which drop, which
# badge, which chips. The pixels — shapes, colours, tooltips, hit targets — belong to
# client/tests/suites/sensor-loss.js, which drives a real Chrome.


def _js(name: str) -> str:
    path = CLIENT_JS / name
    if not path.exists():  # pragma: no cover — the console is in this checkout
        raise AssertionError(f"the console file {path} is not in this checkout; nothing here can be verified")
    return path.read_text(encoding="utf-8")


def _norm(text: str) -> str:
    """Whitespace collapsed, so a reformat is free and a logic change is not.

    The non-breaking space is spelled as an escape rather than typed: an invisible
    literal in a python file is one an editor or a formatter can silently turn back
    into an ordinary space, and a pin would then stop matching for a reason nobody
    could see in the diff.
    """
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


def _object_literal(src: str, name: str) -> str:
    """The text of `const <name> = { … }`, brace-matched, comments and strings skipped.

    Scanned rather than regexed because these tables carry paragraphs of prose in single
    quotes and `//` comments between their entries, and both contain braces. Raises when
    the table is not there, because an absent table means the console has been
    restructured and every assertion built on it is now measuring nothing.
    """
    m = re.search(r"const\s+" + re.escape(name) + r"\s*=\s*\{", src)
    if not m:
        raise AssertionError(f"client/js has no `const {name} = {{` — the console's vocabulary has moved")
    i = m.end() - 1
    depth = 0
    quote = ""
    n = len(src)
    while i < n:
        c = src[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "'\"`":
            quote = c
        elif c == "/" and i + 1 < n and src[i + 1] == "/":
            i = src.find("\n", i)
            if i < 0:
                break
            continue
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            i = src.find("*/", i) + 2
            continue
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[m.end() - 1 : i + 1]
        i += 1
    raise AssertionError(f"`const {name}` in client/js never closes its brace")  # pragma: no cover


CORE_JS = _js("core.js")
NET_JS = _js("net.js")
RENDER_JS = _js("render.js")

# ---- the console's vocabularies, as the console spells them ---------------
LEAK_RANK = {k: int(v) for k, v in re.findall(r"([A-Z]+)\s*:\s*(\d+)", _object_literal(CORE_JS, "LEAK_RANK"))}
SENSOR_BEHIND = {
    k: [c.strip().strip("'\"") for c in v.split(",") if c.strip()]
    for k, v in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\[([^\]]*)\]", _object_literal(CORE_JS, "SENSOR_BEHIND"))
}
CHIP_SHORT = dict(
    re.findall(
        r"^\s*'?([A-Za-z0-9_-]+)'?\s*:\s*\{\s*short\s*:\s*'([^']*)'",
        _object_literal(CORE_JS, "SENSOR_CHIPS"),
        re.MULTILINE,
    )
)
FLAG_LABEL = dict(
    re.findall(
        r"^\s*'?([A-Za-z0-9_-]+)'?\s*:\s*\{\s*label\s*:\s*'([^']*)'",
        _object_literal(CORE_JS, "HEADING_FLAGS"),
        re.MULTILINE,
    )
)

# The fields client/tests/suites/sensor-loss.js hand-writes into its healthy frame. Read
# off that suite so the seam between the two halves of the system is measured rather than
# restated: what the browser assumes a vehicle sends is exactly what is checked here.
_BASE_BLOCK = _object_literal((CLIENT_SUITES / "sensor-loss.js").read_text(encoding="utf-8"), "BASE")
BROWSER_FIXTURE_FIELDS = sorted(
    set(re.findall(r"[{,]\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", re.sub(r"'[^']*'", "''", _BASE_BLOCK))) - {"type"}
)

# ---- the branches this file mirrors, pinned to the source they mirror -----
# Each entry is (which console file, what it decides, the exact text). Whitespace is
# normalised on both sides before the search.
PINS: tuple[tuple[str, str, str], ...] = (
    (
        "net.js",
        "a depth that is not a number becomes null, and only a number stamps the clock",
        "if(t.depth!==undefined){ state.depth = (typeof t.depth==='number') ? t.depth : null; "
        "if(state.depth!=null) state.depthAt=Date.now(); }",
    ),
    (
        "net.js",
        "a bearing that is not a number becomes null",
        "if(t.heading!==undefined){ state.heading = (typeof t.heading==='number') ? t.heading : null; "
        "if(state.heading!=null) state.headingAt=Date.now(); }",
    ),
    (
        "net.js",
        "a pack voltage that is not a number becomes null",
        "if(t.battery_v!==undefined){ state.batteryV = (typeof t.battery_v==='number') ? t.battery_v : null; "
        "if(state.batteryV!=null) state.batteryAt=Date.now(); }",
    ),
    (
        "net.js",
        "mag_cal keeps its null rather than folding it into 0",
        "if(t.mag_cal!==undefined) state.magCal = (typeof t.mag_cal==='number') ? t.mag_cal : null;",
    ),
    (
        "net.js",
        "the fault list arrives as the console's own normalised names",
        "if(t.sensor_faults!==undefined) state.sensorFaults = normalizeFaults(t.sensor_faults);",
    ),
    (
        "net.js",
        "the leak stage is taken from the wire as a string",
        "if(typeof t.leak_state==='string') state.leakState=t.leak_state;",
    ),
    (
        "net.js",
        "the secondary instruments share ONE guard, off FLIGHT_METRICS",
        "state[m.key] = (typeof t[m.wire]==='number') ? t[m.wire] : null;",
    ),
    (
        "core.js",
        "a stage this console cannot account for lands on UNKNOWN",
        "const live = (s==='FLOOD' || s==='WARN' || s==='NORMAL') ? s : 'UNKNOWN';",
    ),
    (
        "core.js",
        "the worse of the live stage and the latch is what shows",
        "return (LEAK_RANK[live] >= LEAK_RANK[latched]) ? live : latched;",
    ),
    (
        "core.js",
        "a null bearing outranks every other heading mark",
        "if(state.heading == null || !sensorFresh(state.headingAt)) return 'dead';",
    ),
    ("core.js", "a null mag_cal is NO COMPASS, not an uncalibrated one", "if(state.magCal == null) return 'nomag';"),
    (
        "core.js",
        "which chips stand behind a reading decides whether it is vetoed",
        "return (SENSOR_BEHIND[kind]||[]).filter(c=>list.indexOf(c)>=0);",
    ),
    (
        "core.js",
        "a named chip is a fault on that reading",
        "function faultedNow(kind, faults){ return faultChips(kind, faults).length > 0; }",
    ),
    ("render.js", "one gate turns a measured reading into cannot-tell", "const sensed = (val, at, kind) => {"),
    ("render.js", "a null value is cannot-tell whatever the frame's age", "if(val == null) return null;"),
    (
        "render.js",
        "the hull naming the chip vetoes a number it is still shipping",
        "if(faultedNow(kind, s.sensorFaults)) return null;",
    ),
    (
        "render.js",
        "cannot-tell is the question mark and not the stale dash",
        "const dead = !stale && val==null; setText(el, dead ? '?' : text, stale);",
    ),
    (
        "render.js",
        "the fourth drop is a shape, and UNKNOWN gets it",
        "icon.innerHTML = st==='FLOOD' ? DROP_FLOOD : st==='WARN' ? DROP_WARN "
        ": st==='UNKNOWN' ? DROP_NOSAMPLE : DROP_OK;",
    ),
    (
        "render.js",
        "the stage is on the glyph's class, which the suites read",
        "icon.className = 'leak-'+st.toLowerCase();",
    ),
    (
        "render.js",
        "the hull-unknown chip names the part that stopped sampling",
        "const leakChips = (v.leakStage==='UNKNOWN') ? faultChips('leak', v.sensorFaults) : [];",
    ),
    (
        "render.js",
        "one dead MS5837 is one errand, not two",
        "if(v.depth==null && v.pressure==null) gone.push(['DEPTH & PRESSURE','depth']);",
    ),
    ("render.js", "a blank bearing raises a chip of its own", "if(v.heading==null) gone.push(['BEARING','heading']);"),
    (
        "render.js",
        "a dead pack monitor takes the current with it and says so",
        "if(v.batteryV==null) gone.push([(v.currentA==null && v.currentSeen) "
        "? 'PACK VOLTAGE & CURRENT' : 'PACK VOLTAGE', 'battery']);",
    ),
    (
        "render.js",
        "the chip on the rail names the job, not the part number",
        "const short = chips.indexOf('i2c')>=0 ? 'I2C BUS DOWN' "
        ": chips.length ? chipMeans(chips[0]).short + ' STOPPED' "
        ": 'SENSOR STOPPED';",
    ),
    (
        "render.js",
        "the dead-sensor chip is keyed on the reading it explains",
        "push('dead-'+g[1],'crit',ALERT_ICONS.sensor,'NO ' + g[0] + ' · ' + short,",
    ),
    ("render.js", "only a MEASURED voltage may raise the surface prompt", "if(band.key==='crit' && v.batteryV!=null)"),
    (
        "render.js",
        "anything the vehicle named that nothing explained still gets a chip",
        "const rest = unexplainedFaults(v.sensorFaults, explained);",
    ),
)


def _sources() -> dict[str, str]:
    return {"core.js": CORE_JS, "net.js": NET_JS, "render.js": RENDER_JS}


class Console:
    """The console's decisions about one frame, in the console's own vocabulary.

    A SESSION and not a function, because net.js's ingest is cumulative: the leak latch,
    "has navigation ever committed to an answer", and "has this hull ever spoken this
    field" are all memories that outlive the frame that wrote them, and every one of them
    changes what the screen says. Frames are fed in the order they arrived on the wire.

    THE ONE MODELLING ASSUMPTION, STATED. render.js's gate asks three questions in order —
    is the value null, has the hull named the chip behind it, and did a NUMBER for this
    field arrive recently. This session is fed a live stream, so frames are arriving and
    every field that is a number stamps its own clock as it lands (net.js does exactly
    that, and the pin above holds it there). The third question therefore answers "yes"
    for every number in the frame and "no" for every null, which is the same answer the
    first question gives — so the stale/dropped-frame branch, which is a claim about the
    LINK and not about a sensor, is not exercised here and is not claimed to be. It is
    exercised in the browser, where a link can actually be made to go quiet.
    """

    def __init__(self) -> None:
        # kind, in the console's own SENSOR_BEHIND vocabulary, for each reading this
        # suite judges. The kinds come from the table read off core.js, so a reading
        # whose chips the console stops knowing about fails loudly in setUpModule.
        self.readings = {
            "depth": ("depth", "depth"),
            "pressure": ("pressure", "pressure"),
            "heading": ("heading", "heading"),
            "batteryV": ("battery_v", "battery"),
            "currentA": ("current_a", "current"),
            "turnRate": ("gyro_z_dps", "imu"),
            "surge": ("accel_fwd_ms2", "imu"),
            "pitchDeg": ("pitch_deg", "imu"),
            "rollDeg": ("roll_deg", "imu"),
        }
        self.st: dict = {k: None for k in self.readings}
        self.st.update({k + "Seen": False for k in self.readings})
        self.st.update(
            {
                "sensorFaults": [],
                "magCal": None,
                "leakState": "NORMAL",
                "leakProbeFault": None,
                "alarmLeakStage": "NORMAL",
                "snagged": False,
                "gyroOnly": False,
                "navAnswered": False,
                "snagStood": False,
            }
        )

    # ---- net.js onTelemetry ------------------------------------------------
    @staticmethod
    def _num(v: object) -> float | int | None:
        """JavaScript's `typeof x === 'number'`, which a bool is not."""
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    def ingest(self, t: dict) -> "Console":
        st = self.st
        for key, (wire, _kind) in self.readings.items():
            if wire in t:
                st[key + "Seen"] = True
                st[key] = self._num(t[wire])
        if "mag_cal" in t:
            st["magCal"] = self._num(t["mag_cal"])
        if "sensor_faults" in t:
            raw = t["sensor_faults"]
            items = raw if isinstance(raw, list) else str(raw).split()
            st["sensorFaults"] = [str(c).strip().lower() for c in items if str(c).strip()]
        if isinstance(t.get("leak_state"), str):
            st["leakState"] = t["leak_state"]
        elif isinstance(t.get("leak"), bool):
            st["leakState"] = "FLOOD" if t["leak"] else "NORMAL"
        if t.get("leak") is False or t.get("leak_state") == "NORMAL":
            st["alarmLeakStage"] = "NORMAL"
        if st["leakState"] in ("WARN", "FLOOD"):
            self.latch(st["leakState"])
        if "leak_probe_fault" in t:
            st["leakProbeFault"] = t["leak_probe_fault"] if isinstance(t["leak_probe_fault"], str) else None
        if "snagged" in t:
            st["snagged"] = t["snagged"] if isinstance(t["snagged"], bool) else None
        if "gyro_only" in t:
            st["gyroOnly"] = t["gyro_only"] if isinstance(t["gyro_only"], bool) else None
        if st["snagged"] is True:
            st["navAnswered"], st["snagStood"] = True, True
        elif st["snagged"] is False:
            st["navAnswered"], st["snagStood"] = True, False
        if st["gyroOnly"] in (True, False):
            st["navAnswered"] = True
        return self

    def latch(self, stage: str) -> None:
        if LEAK_RANK.get(stage, 0) > LEAK_RANK.get(self.st["alarmLeakStage"], 0):
            self.st["alarmLeakStage"] = stage

    # ---- core.js / render.js resolution ------------------------------------
    def faulted(self, kind: str) -> list[str]:
        return [c for c in SENSOR_BEHIND.get(kind, []) if c in self.st["sensorFaults"]]

    def sensed(self, key: str) -> float | int | None:
        """render.js viewFromState's one gate: null, or the hull's own admission, or the
        number. See the modelling note in the class docstring for the third question."""
        val = self.st[key]
        if val is None:
            return None
        return None if self.faulted(self.readings[key][1]) else val

    def readout(self, key: str) -> str:
        """What renderSensed would put in the element: '?' or the number."""
        v = self.sensed(key)
        return "?" if v is None else str(v)

    def leak_stage(self) -> str:
        s = self.st["leakState"]
        live = s if s in ("FLOOD", "WARN", "NORMAL") else "UNKNOWN"
        latched = self.st["alarmLeakStage"] or "NORMAL"
        return live if LEAK_RANK.get(live, 0) >= LEAK_RANK.get(latched, 0) else latched

    def leak_glyph_class(self) -> str:
        """renderLeak's `icon.className`, which is what the browser suite asserts on."""
        return "leak-" + self.leak_stage().lower()

    def heading_flag(self) -> str:
        if self.st["heading"] is None:
            return "dead"
        if self.st["magCal"] is None:
            return "nomag"
        suspect = isinstance(self.st["magCal"], int) and self.st["magCal"] < 2
        gyro = self.st["gyroOnly"] is True
        if gyro and suspect:
            return "gyro-mag"
        if gyro:
            return "gyro"
        if suspect:
            return "mag"
        if self.st["gyroOnly"] is None and self.st["navAnswered"]:
            return "nofilter"
        return ""

    def heading_badge(self) -> str:
        return FLAG_LABEL.get(self.heading_flag(), "")

    def alerts(self) -> list[tuple[str, str, str]]:
        """The rail, as (id, kind, text). alertList()'s order, and its wording.

        Only the chips this suite's failures can raise are built. The ballast chips are
        deliberately included, because a hull whose syringe has never been homed raises
        one on every frame and a check that asserted "no chips" without it would be
        asserting something false about a healthy vehicle.
        """
        st = self.st
        out: list[tuple[str, str, str]] = []
        stage = self.leak_stage()
        if stage == "FLOOD":
            out.append(("flood", "crit", "FLOOD · SURFACE NOW"))
        leak_chips = self.faulted("leak") if stage == "UNKNOWN" else []
        if stage == "UNKNOWN":
            named = CHIP_SHORT.get(leak_chips[0], "") + " STOPPED" if leak_chips else "PROBES NOT READ"
            out.append(("leakunknown", "crit", "HULL STATE UNKNOWN · " + named))
        volts = self.sensed("batteryV")
        if volts is not None and volts < 6.6:
            out.append(("batt", "crit", f"BATTERY {volts:.1f}V · SURFACE"))
        if st["snagged"] is True:
            out.append(("snag", "crit", "SNAGGED · NO WAY ON"))
        elif st["snagged"] is None and st["snagStood"]:
            out.append(("snag", "crit", "SNAGGED · UNCONFIRMED"))
        elif st["snagged"] is None and st["navAnswered"]:
            out.append(("snagwatch", "warn", "SNAG WATCH LOST · NAV QUIET"))
        gone: list[tuple[str, str]] = []
        if self.sensed("depth") is None and self.sensed("pressure") is None:
            gone.append(("DEPTH & PRESSURE", "depth"))
        else:
            if self.sensed("depth") is None:
                gone.append(("DEPTH", "depth"))
            if self.sensed("pressure") is None:
                gone.append(("PRESSURE", "pressure"))
        if self.sensed("heading") is None:
            gone.append(("BEARING", "heading"))
        if volts is None:
            both = self.sensed("currentA") is None and st["currentASeen"]
            gone.append(("PACK VOLTAGE & CURRENT" if both else "PACK VOLTAGE", "battery"))
        explained = list(leak_chips)
        for label, kind in gone:
            chips = self.faulted(kind)
            for c in chips:
                if c not in explained:
                    explained.append(c)
            short = (
                "I2C BUS DOWN"
                if "i2c" in chips
                else (CHIP_SHORT.get(chips[0], "") + " STOPPED" if chips else "SENSOR STOPPED")
            )
            out.append(("dead-" + kind, "crit", "NO " + label + " · " + short))
        rest = [c for c in st["sensorFaults"] if c not in explained]
        if rest:
            out.append(("faults", "warn", "NOT ANSWERING · " + " · ".join(CHIP_SHORT.get(c, c.upper()) for c in rest)))
        if stage == "WARN":
            out.append(("leakwarn", "warn", "WATER COLLECTING · FINISH UP"))
        if st["leakProbeFault"]:
            out.append(("probe", "warn", "LEAK PROBE FAULT · " + str(st["leakProbeFault"]).upper()))
        return out

    def alert_ids(self) -> list[str]:
        return [a[0] for a in self.alerts()]

    def alert_text(self, alert_id: str) -> str:
        for i, _kind, text in self.alerts():
            if i == alert_id:
                return text
        return ""


def console_of(frames: list[dict]) -> Console:
    """A console that has watched exactly these frames arrive, in this order."""
    c = Console()
    for f in frames:
        c.ingest(f)
    return c


# ===========================================================================
# The dive: one socket, driven like a client, read like one
# ===========================================================================
class Dive:
    """One control socket on the running server, plus the hooks that break the hull.

    The vehicle is ARMED and flown throughout, so the simulated world keeps moving
    underneath whatever is killed. That is the whole point of the fixture: a frozen
    reading and a steady one are only distinguishable while the world moves.
    """

    def __init__(self, server: Server) -> None:
        self.server = server
        self.hw = neptune.app.state.hw
        self.rov = neptune.app.state.rov
        self.ws = Ws(server.port)
        self.seen: list[dict] = []  # every telemetry frame this suite has ever read

    # ---- flying ------------------------------------------------------------
    def _tick(self, throttle: float, steer: float, ballast: str, window: float = 0.05) -> list[dict]:
        """One turn: feed the watchdog, and take whatever has arrived while doing it."""
        self.ws.send_json({"type": "control", "throttle": throttle, "steer": steer})
        self.ws.send_json({"type": "ballast", "cmd": ballast})
        got: list[dict] = []
        poll = time.monotonic() + window
        while time.monotonic() < poll:
            try:
                msg = self.ws.recv_json(max(0.01, poll - time.monotonic()))
            except TimeoutError:
                break
            if msg.get("type") == "telemetry":
                got.append(msg)
        return got

    def drain(self) -> int:
        """Throw away everything already queued on this socket, and say how much there was.

        THE FLAKE THIS EXISTS FOR, AND IT IS THE ONE A SUITE LIKE THIS CANNOT AFFORD.
        A phase must contain only frames the vehicle built while that phase was TRUE.
        The receive buffer does not care: while this file is asserting, the server goes
        on broadcasting at telemetry_hz into a socket nobody is reading, so the first
        thing a read after a kill sees is a stack of frames from BEFORE it. Measured on
        this bench: a class that had spent several seconds asserting opened its next
        phase on frames a hundred and thirty sequence numbers old, and a depth that was
        still on the wire because the sensor had not been killed yet was reported as a
        sensor that refused to die — intermittently, which is worse than always.
        """
        seen = 0
        while True:
            try:
                msg = self.ws.recv_json(0.02)
            except TimeoutError:
                return seen
            if msg.get("type") == "telemetry":
                seen += 1

    def fly(self, seconds: float, throttle: float = 0.6, steer: float = 0.45, ballast: str = "hold") -> list[dict]:
        """Send what a console sends and read what the vehicle answers, for `seconds`.

        Control frames go out continuously because the vehicle's watchdog is real: a
        client that stops talking is a tether that has come out, and the failsafe cuts
        the thrusters — which would stop the world moving in the middle of a check that
        depends on it moving.

        WHAT COMES BACK IS THIS PHASE AND NOTHING OLDER. The socket is drained, then
        flown for a lead-in whose frames are discarded, then drained again — so the
        first frame collected was written by the server after everything already in
        flight had been thrown away, which is the only construction that makes "the
        wire went to cannot-tell" a statement about the kill rather than about the
        buffer. See drain().

        THE PLUNGER IS PARKED ON THE WAY OUT. `ballast_pump` is a DIRECTION and not a
        pulse: the syringe goes on travelling until something tells it to stop, so a
        phase that left it filling would drive it onto its stop during the seconds this
        suite spends asserting, and the next phase would open on a sub pinned at the
        bottom of its range with a depth that cannot move. That is the exact condition
        under which "the reading did not freeze" proves nothing.
        """
        self.drain()
        lead = time.monotonic() + LEAD_IN_S
        while time.monotonic() < lead:
            self._tick(throttle, steer, ballast)
        self.drain()
        out: list[dict] = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            got = self._tick(throttle, steer, ballast)
            out.extend(got)
            self.seen.extend(got)
        if ballast != "hold":
            self.ws.send_json({"type": "ballast", "cmd": "hold"})
        if len(out) < MIN_FRAMES:
            raise AssertionError(
                f"only {len(out)} telemetry frame(s) arrived in {seconds:.1f}s on a "
                f"{settings.telemetry_hz:.0f} Hz link — the control loop is not turning, "
                f"and nothing measured below would mean anything"
            )
        return out

    def moving_ballast(self) -> str:
        """Whichever way the syringe still has room to travel.

        A depth that cannot change is a depth whose freeze nobody could detect, so the
        direction is chosen from where the plunger actually is rather than written down
        once and hoped for.
        """
        for f in reversed(self.seen):
            level = f.get("ballast_level")
            if isinstance(level, (int, float)):
                return "empty" if level > 0.5 else "fill"
        return "fill"

    def command(self, name: str, value: object = None) -> None:
        self.ws.send_json({"type": "command", "name": name, "value": value, "c_id": f"int-{name}"})

    # ---- breaking and mending ---------------------------------------------
    def kill(self, chip: str) -> None:
        self.hw._kill_sensor(chip)

    def revive(self, chip: str) -> None:
        self.hw._revive_sensor(chip)

    def stall(self, subsystem: str) -> None:
        {"leak-probes": self.hw._stall_leak_sampling, "sensor-thread": self.hw._stall_sensor_thread}[subsystem](True)

    def unstall(self, subsystem: str) -> None:
        {"leak-probes": self.hw._stall_leak_sampling, "sensor-thread": self.hw._stall_sensor_thread}[subsystem](False)

    def break_part(self, part: str) -> None:
        (self.kill if part in self.hw.DEVICES else self.stall)(part)

    def mend_part(self, part: str) -> None:
        (self.revive if part in self.hw.DEVICES else self.unstall)(part)

    # ---- back to a known hull ---------------------------------------------
    def quiesce(self) -> None:
        """Put the vehicle back where every check expects to find it: whole, and dry.

        Done through the hardware object rather than over a socket because this is
        fixture work, not a measurement — every measurement below comes off the wire.
        """
        for chip in self.hw.DEVICES:
            self.hw._revive_sensor(chip)
        for sub in self.hw.SUBSYSTEMS:
            self.unstall(sub)
        self.hw._set_leak("NORMAL")
        self.hw._set_probe_wet_at_boot(False, False)
        self.hw._jam_paddle(False)
        # And the plunger is parked, for the reason fly() states: a direction outlives
        # the phase that set it.
        self.ws.send_json({"type": "ballast", "cmd": "hold"})

    def close(self) -> None:
        self.ws.close()


SERVER: Server | None = None
DIVE: Dive | None = None
_SAVED: dict = {}


def setUpModule() -> None:
    """One server, one boot, one socket, for the whole file.

    NOT A STYLE CHOICE. The app under test has exactly one `app.state`, and its lifespan
    REPLACES hw / rov / manager / camera on the way up and CLOSES the blackbox on the way
    down, so a second server over the same app does not isolate anything — it corrupts
    what is already running. The good side of it is that the later checks are served by
    the same process the earlier ones broke, so a vehicle that never recovered from a
    killed chip takes the rest of the suite with it instead of being replaced by a fresh
    instance that hides the corpse.
    """
    global SERVER, DIVE
    _SAVED["hardware_backend"] = settings.hardware_backend
    object.__setattr__(settings, "hardware_backend", "mock")
    _SAVED["crt_national_auto"] = nav_settings.crt_national_auto
    object.__setattr__(nav_settings, "crt_national_auto", False)
    # Quiet, deliberately, and put back afterwards. Every check below drives the vehicle
    # into a state it is supposed to complain about — a chip killed, a sampler stopped,
    # a throttle held open — so without this the suite prints a page of warnings that are
    # the fixtures working correctly. A module that permanently silenced the vehicle's
    # logger would be a worse bug than the noise, so the level is restored.
    _SAVED["log_levels"] = {
        name: logging.getLogger(name).level for name in ("neptune", "uvicorn.error", "uvicorn.access", "uvicorn.asgi")
    }
    for name in _SAVED["log_levels"]:
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        SERVER = Server().start()
        # A LAUNCH POINT, so navigation is actually answering while the sensors die.
        # Without an origin the estimator has nothing to estimate from, nav's answers
        # never enter the frame at all, and `fill_nav_fields()` — the file that broke
        # round three — is never asked a question. ?fetch=false so nothing reaches for a
        # network this bench does not have.
        status, body = SERVER.post("/api/origin?fetch=false", ORIGIN)
        if status != 200:
            raise RuntimeError(f"the vehicle refused a launch point: HTTP {status} {body[:200]!r}")
        DIVE = Dive(SERVER)
        DIVE.quiesce()
        DIVE.command("ballast_home")  # so the syringe is a reading and not an unknown
        DIVE.command("arm", True)
        DIVE.fly(SETTLE_S)
    except BaseException:
        # unittest does not call tearDownModule when setUpModule raised, so everything
        # borrowed above is handed back here or it stays borrowed for the whole run.
        _teardown()
        raise


def _teardown() -> None:
    global SERVER, DIVE
    if DIVE is not None:
        with contextlib.suppress(Exception):
            DIVE.quiesce()
            DIVE.command("disarm", True)
            DIVE.close()
        DIVE = None
    stopped = True
    if SERVER is not None:
        stopped = SERVER.stop()
        SERVER = None
    for name, level in _SAVED.pop("log_levels", {}).items():
        logging.getLogger(name).setLevel(level)
    if "hardware_backend" in _SAVED:
        object.__setattr__(settings, "hardware_backend", _SAVED.pop("hardware_backend"))
    if "crt_national_auto" in _SAVED:
        object.__setattr__(nav_settings, "crt_national_auto", _SAVED.pop("crt_national_auto"))
    if not stopped:
        # SAID OUT LOUD, not swallowed. A test process that walks away from a thread
        # still serving a port poisons whatever runs next, and "the suite finished" is
        # not the same claim as "the server it started is gone".
        raise RuntimeError(
            f"the test server was asked to stop and was still running {SERVER_STOP_TIMEOUT_S:.0f}s later"
        )


def tearDownModule() -> None:
    _teardown()


def dive() -> Dive:
    assert DIVE is not None, "the dive fixture never came up"
    return DIVE


# ===========================================================================
# Shared assertions about a frame
# ===========================================================================
# Which wire fields each of the console's own reading-kinds stands for. Written against
# SENSOR_BEHIND's keys so the contradiction check below is stated in the vocabulary the
# console uses to decide what to blank — the null and the name are one decision read
# twice (design §24.5), and this is the second reading of it.
KIND_FIELDS: dict[str, tuple[str, ...]] = {
    "depth": ("depth",),
    "pressure": ("pressure",),
    "heading": ("heading", "heading_card", "mag_cal"),
    "imu": ("gyro_z_dps", "accel_fwd_ms2", "pitch_deg", "roll_deg"),
    "battery": ("battery_v",),
    "current": ("current_a",),
}


class WireCase(unittest.TestCase):
    """Assertions every frame off this vehicle has to satisfy, whatever is broken."""

    def assertNoContradiction(self, frame: dict) -> None:
        """A chip may not be named while a reading it measures is still a number.

        The two can only ever disagree in one direction — the hull admitting a part has
        stopped while still shipping a value measured by it — and that direction is the
        one that reaches the operator as a confident number with an explanation of why
        it cannot be trusted sitting beside it.
        """
        faults = [str(c).lower() for c in frame.get("sensor_faults") or []]
        for kind, fields in KIND_FIELDS.items():
            named = [c for c in SENSOR_BEHIND.get(kind, []) if c in faults]
            if not named:
                continue
            for field in fields:
                self.assertIsNone(
                    frame.get(field),
                    f"seq={frame.get('seq')}: the vehicle names {named} in sensor_faults and "
                    f"still sends {field}={frame.get(field)!r} — the null and the name are one "
                    f"decision, and this frame reads it two different ways",
                )
        if [c for c in SENSOR_BEHIND.get("leak", []) if c in faults]:
            self.assertNotEqual(
                "NORMAL",
                frame.get("leak_state"),
                f"seq={frame.get('seq')}: nothing is sampling the probes and the hull is "
                f"still certified dry — NORMAL is a positive claim, not a fallback",
            )

    def assertCardinalAgrees(self, frame: dict) -> None:
        """The letter may never outlive the number it restates."""
        if frame.get("heading") is None:
            self.assertIsNone(
                frame.get("heading_card"),
                f"seq={frame.get('seq')}: heading is null and heading_card is "
                f"{frame.get('heading_card')!r} — a compass point nothing is measuring",
            )

    def assertEveryFrame(self, frames: list[dict]) -> None:
        self.assertGreaterEqual(len(frames), MIN_FRAMES, "too few frames to call this a measurement")
        for f in frames:
            self.assertNoContradiction(f)
            self.assertCardinalAgrees(f)


# ===========================================================================
# The fixture is real
# ===========================================================================
class HarnessTest(WireCase):
    """Before anything is concluded from this suite: prove it is measuring a vehicle."""

    def test_the_frames_come_off_a_socket_and_not_out_of_a_function(self):
        frames = dive().fly(SETTLE_S)
        self.assertTrue(all(f.get("type") == "telemetry" for f in frames))
        seqs = [f["seq"] for f in frames if f.get("seq") is not None]
        self.assertEqual(seqs, sorted(seqs), "the sequence numbers went backwards on one socket")
        self.assertEqual(len(seqs), len(set(seqs)), "the same frame arrived twice")

    def test_the_hull_under_test_is_the_simulated_one(self):
        # A safety rule and not a formality: the checks below arm the vehicle and hold
        # the throttle open. Run against RealHardware that spins two propellers.
        self.assertTrue(dive().fly(SETTLE_S)[-1].get("mock"), "this suite must never run against a real hull")

    def test_navigation_is_answering_by_the_time_the_sensors_are_killed(self):
        # The whole point of POSTing a launch point. With nav quiet, fill_nav_fields()
        # never stamps anything and the file that broke round three is never asked.
        last = dive().fly(SETTLE_S)[-1]
        self.assertIsNotNone(last.get("speed_src"), "navigation contributed nothing to this frame")
        self.assertIsNotNone(last.get("snagged"), "nav is not answering, so its heading rule is untested")

    def test_a_kill_reaches_the_vehicle_this_socket_is_reading(self):
        d = dive()
        d.kill("ms5837")
        try:
            self.assertIn("ms5837", d.fly(BROKEN_S)[-1].get("sensor_faults") or [])
        finally:
            d.revive("ms5837")
            d.fly(SETTLE_S)

    def test_a_typo_in_a_kill_does_not_quietly_exercise_a_healthy_vehicle(self):
        with self.assertRaises(ValueError):
            dive().kill("bn0085")

    def test_the_world_keeps_moving_under_a_dead_sensor(self):
        d = dive()
        pump = d.moving_ballast()
        before = d.fly(SETTLE_S, ballast=pump)[-1]["depth"]
        d.kill("ms5837")
        d.fly(BROKEN_S, ballast=pump)
        d.revive("ms5837")
        after = d.fly(SETTLE_S, ballast=pump)[-1]["depth"]
        self.assertIsNotNone(before)
        self.assertIsNotNone(after)
        self.assertNotEqual(
            before,
            after,
            "the sub did not move while its depth sensor was dead, so nothing below can "
            "tell a blanked reading from a frozen one",
        )


# ===========================================================================
# The console contract this suite mirrors is the console's own
# ===========================================================================
class ClientContractTest(unittest.TestCase):
    """The mirror is pinned to the console. If the console moved, say so here first."""

    def test_the_vocabularies_were_actually_found(self):
        self.assertEqual({"NORMAL": 0, "UNKNOWN": 1, "WARN": 2, "FLOOD": 3}, LEAK_RANK)
        for kind in ("depth", "pressure", "heading", "imu", "battery", "current", "leak"):
            self.assertIn(kind, SENSOR_BEHIND, f"the console no longer says which chips stand behind {kind!r}")
        for chip in ("ms5837", "bno085", "ina219", "i2c", "leak-probes", "sensor-thread"):
            self.assertIn(chip, CHIP_SHORT, f"the console has no plain-English name for {chip!r}")
        for flag in ("dead", "nomag", "mag", "gyro", "nofilter"):
            self.assertIn(flag, FLAG_LABEL, f"the console has dropped the {flag!r} heading badge")

    def test_the_vehicle_and_the_console_use_the_same_names_for_the_same_parts(self):
        hw = dive().hw
        for chip in hw.DEVICES:
            self.assertIn(chip, CHIP_SHORT, f"the vehicle can fault {chip!r} and the console cannot name it")
        for sub in hw.SUBSYSTEMS:
            self.assertIn(sub, CHIP_SHORT, f"the vehicle can fault {sub!r} and the console cannot name it")

    def test_every_branch_this_file_mirrors_is_still_in_the_console(self):
        srcs = {name: _norm(text) for name, text in _sources().items()}
        missing = [(f, what, snippet) for f, what, snippet in PINS if _norm(snippet) not in srcs[f]]
        self.assertEqual(
            [],
            missing,
            "client/js has changed under this suite's mirror. Each line below is a "
            "decision this file re-states in python and can no longer find in the "
            "console; re-derive the mirror rather than deleting the pin:\n"
            + "\n".join(f"  {f}: {what}\n      {snippet}" for f, what, snippet in missing),
        )

    def test_the_browser_suites_hand_written_frame_is_what_this_vehicle_sends(self):
        """The seam between the two halves of the system, measured rather than assumed.

        client/tests/suites/sensor-loss.js proves the pixels against a frame it writes
        itself. That frame is an assumption about the vehicle, and an assumption nobody
        measures is where two green suites drift apart: the browser would go on proving
        a null renders as '?' while the hull quietly stopped sending one.
        """
        self.assertGreater(len(BROWSER_FIXTURE_FIELDS), 20, "the browser suite's healthy frame could not be read")
        frame = dive().fly(SETTLE_S)[-1]
        absent = [f for f in BROWSER_FIXTURE_FIELDS if f not in frame]
        self.assertEqual(
            [],
            absent,
            f"the browser's sensor-loss suite builds its healthy hull out of fields this "
            f"vehicle does not send: {absent}. Those checks are then proving something "
            f"about a frame no hull produces.",
        )


# ===========================================================================
# One part at a time: healthy -> stopped -> named -> never plausible -> back
# ===========================================================================
class PartCase:
    """The five questions asked of every part the bench can break.

    A MIXIN AND NOT A TestCase, deliberately. A shared base that is itself a TestCase is
    collected by discovery and has to be skipped out of the run — and a skip is reported
    by api/tests/run.py as a check that did not happen, which is exactly what this suite
    is here to stop anything pretending about. Nothing is lost: the concrete classes
    below inherit both this and WireCase, so every method still runs against a real
    TestCase.

    Each subclass names the part, the wire fields that must go to cannot-tell, and the
    plausible values that must never appear in their place. The phases are flown once
    per class in setUpClass — the vehicle is one process and one hull, so re-breaking it
    for every question would multiply the run time by the number of questions asked.
    """

    PART = ""
    CHIP = ""  # what sensor_faults must name
    NULL_FIELDS: tuple[str, ...] = ()
    # Those of NULL_FIELDS that are not numbers. heading_card is the only one on this
    # frame: it is a RESTATEMENT of a number rather than one, and it has to be checked
    # for presence rather than for type or it fails a healthy hull.
    TEXT_FIELDS: tuple[str, ...] = ()
    # field -> the values that would be a MEASUREMENT rather than a cannot-tell
    FORBIDDEN: dict[str, tuple[object, ...]] = {}
    # "auto" means "whichever way the syringe can still travel" — see Dive.moving_ballast.
    BALLAST = "hold"

    healthy: list[dict] = []
    broken: list[dict] = []
    mended: list[dict] = []
    pump = "hold"

    @classmethod
    def setUpClass(cls) -> None:
        d = dive()
        d.quiesce()
        cls.pump = d.moving_ballast() if cls.BALLAST == "auto" else cls.BALLAST
        cls.healthy = d.fly(SETTLE_S, ballast=cls.pump)
        d.break_part(cls.PART)
        cls.broken = d.fly(BROKEN_S, ballast=cls.pump)
        d.mend_part(cls.PART)
        cls.mended = d.fly(SETTLE_S, ballast=cls.pump)

    @classmethod
    def tearDownClass(cls) -> None:
        dive().quiesce()

    # ---- 1. healthy --------------------------------------------------------
    def test_1_healthy_the_readings_are_numbers_on_the_wire(self):
        for f in self.healthy:
            for field in self.NULL_FIELDS:
                if field in self.TEXT_FIELDS:
                    self.assertIsNotNone(f.get(field), f"{field} is already absent on a healthy hull")
                    continue
                self.assertIsInstance(
                    f.get(field),
                    (int, float),
                    f"{field} is not a number on a healthy hull, so nothing below can " f"prove it stopped being one",
                )

    def test_1_healthy_no_part_is_named(self):
        self.assertEqual([], self.healthy[-1].get("sensor_faults"), "a healthy bench vehicle named a faulty part")

    # ---- 2 & 3. stopped, and named ----------------------------------------
    def test_3_the_wire_goes_to_cannot_tell(self):
        for f in self.broken:
            for field in self.NULL_FIELDS:
                self.assertIsNone(
                    f.get(field),
                    f"seq={f.get('seq')}: {self.PART} has stopped and {field}={f.get(field)!r} is "
                    f"still on the wire",
                )

    def test_3_the_part_is_named(self):
        for f in self.broken:
            self.assertIn(
                self.CHIP,
                f.get("sensor_faults") or [],
                f"seq={f.get('seq')}: the reading is gone and nothing says which part to go "
                f"and look at — a blank with no cause reads as a dashboard glitch",
            )

    # ---- 4. never a plausible number instead ------------------------------
    def test_4_the_reading_never_becomes_a_plausible_number(self):
        for f in self.broken:
            for field, bad in self.FORBIDDEN.items():
                self.assertNotIn(
                    f.get(field),
                    bad,
                    f"seq={f.get('seq')}: {field}={f.get(field)!r} is not a cannot-tell, it is "
                    f"a measurement — and one an operator would act on",
                )

    def test_4_the_reading_did_not_freeze_at_its_last_good_value(self):
        last_good = {field: self.healthy[-1].get(field) for field in self.NULL_FIELDS}
        for f in self.broken:
            for field, value in last_good.items():
                if value is None:
                    continue
                self.assertNotEqual(
                    value,
                    f.get(field),
                    f"seq={f.get('seq')}: {field} is still reading its last good {value!r} — "
                    f"a frozen reading and a steady one look identical",
                )

    # ---- 5. it comes back --------------------------------------------------
    def test_5_the_reading_comes_back(self):
        last = self.mended[-1]
        for field in self.NULL_FIELDS:
            why = (
                f"{field} is still blank after {self.PART} was mended — a gauge that goes "
                f"blank and stays blank is its own fault, and one nobody finds until a dive"
            )
            if field in self.TEXT_FIELDS:
                self.assertIsNotNone(last.get(field), why)
            else:
                self.assertIsInstance(last.get(field), (int, float), why)
        self.assertNotIn(self.CHIP, last.get("sensor_faults") or [], "the mended part is still named as faulted")

    # ---- the invariants, on every frame of every phase ---------------------
    def test_no_frame_in_any_phase_contradicts_itself(self):
        self.assertEveryFrame(self.healthy + self.broken + self.mended)

    # ---- what the console would draw --------------------------------------
    def test_the_console_shows_a_question_mark_and_names_the_part(self):
        """Overridden by the two subsystems, which blank no number at all."""
        before = console_of(self.healthy)
        for key, (wire, _kind) in before.readings.items():
            if wire in self.NULL_FIELDS:
                self.assertNotEqual("?", before.readout(key), f"{key} was already cannot-tell on a healthy hull")
        during = console_of(self.healthy + self.broken)
        for key, (wire, _kind) in during.readings.items():
            if wire in self.NULL_FIELDS:
                self.assertEqual(
                    "?",
                    during.readout(key),
                    f"the console would still draw a number for {key} with {self.PART} stopped",
                )
        after = console_of(self.healthy + self.broken + self.mended)
        for key, (wire, _kind) in after.readings.items():
            if wire in self.NULL_FIELDS:
                self.assertNotEqual("?", after.readout(key), f"{key} never came back on the console")


class DepthSensorTest(PartCase, WireCase):
    """The MS5837 that let go at 4.33 m and went on answering its last depth."""

    PART = CHIP = "ms5837"
    NULL_FIELDS = ("depth", "pressure")
    # 0.0 m is AT THE SURFACE and surface pressure is a specific, checkable claim about a
    # chip that is not answering. Both are written into the permanent dive log, where
    # they are indistinguishable from a real sample.
    FORBIDDEN = {"depth": (0.0, 0), "pressure": (0.0, 0, settings.surface_pressure_psi)}
    BALLAST = "auto"  # the sub must go on sinking while its depth sensor is dead

    def test_the_depth_that_comes_back_is_where_the_sub_is_now(self):
        """The half of the contract that only a moving world can prove.

        A blank reading is easy; a blank reading over a sub that went on descending is
        the thing. If the number that returns is the number that left, either nothing
        moved — in which case none of the checks above measured anything — or the
        vehicle kept the last one warm while pretending it had none.
        """
        before, after = self.healthy[-1]["depth"], self.mended[-1]["depth"]
        self.assertNotEqual(
            before,
            after,
            f"depth left at {before} m and came back at {after} m with the ballast "
            f"{self.pump}ing throughout — the world did not move under the dead sensor",
        )

    def test_the_console_raises_one_errand_for_one_dead_chip(self):
        c = console_of(self.healthy + self.broken)
        self.assertIn("dead-depth", c.alert_ids())
        self.assertEqual("NO DEPTH & PRESSURE · DEPTH SENSOR STOPPED", c.alert_text("dead-depth"))
        self.assertNotIn("dead-pressure", c.alert_ids(), "one connector must not raise two errands")
        self.assertNotIn("faults", c.alert_ids(), "the named chip was not accounted for by the blanked reading")

    def test_the_console_stops_accusing_anyone_once_it_is_mended(self):
        self.assertEqual([], console_of(self.healthy + self.broken + self.mended).alert_ids())

    def test_a_dead_depth_sensor_does_not_take_navigation_down(self):
        self.assertIsNotNone(self.broken[-1].get("speed_src"), "navigation went quiet because a depth sensor died")


class CompassTest(PartCase, WireCase):
    """The BNO085 — six readings, one chip, and the bearing everything is built on."""

    PART = CHIP = "bno085"
    NULL_FIELDS = ("heading", "heading_card", "mag_cal", "gyro_z_dps", "accel_fwd_ms2", "pitch_deg", "roll_deg")
    TEXT_FIELDS = ("heading_card",)
    # 0.0 heading is DUE NORTH on a heading-up radar; 'N' is that claim spelled as a
    # letter; mag_cal 0 is "a compass answered, and it says it is uncalibrated", which is
    # the strongest thing that can be said about a bearing short of trusting it; and a
    # turn rate of 0.0 deg/s beside a blank bearing says the sub is holding a course.
    FORBIDDEN = {
        "heading": (0.0, 0),
        "heading_card": ("N",),
        "mag_cal": (0, 3),
        "gyro_z_dps": (0.0, 0),
        "accel_fwd_ms2": (0.0, 0),
        "pitch_deg": (0.0, 0),
        "roll_deg": (0.0, 0),
    }

    def test_the_bearing_that_comes_back_is_the_one_the_sub_is_on_now(self):
        """The sub is under helm throughout, so a bearing that returns unchanged is one
        the vehicle kept warm rather than measured again."""
        before, after = self.healthy[-1]["heading"], self.mended[-1]["heading"]
        self.assertNotEqual(before, after, f"the sub turned under a blind compass and came back on {after}°")

    def test_navigation_does_not_stamp_a_bearing_over_the_silence(self):
        """`fill_nav_fields()` rule 3, over a real socket, with nav actually answering.

        This is the exact frame round three shipped: rov.py sent heading=None and the
        estimator's own cannot-tell default (0.0) was stamped over it, so the console
        received a confident DUE NORTH beside a NO COMPASS badge and a "bno085 not
        answering" fault, and the heading-up radar swung the whole map north on it.
        """
        for f in self.broken:
            self.assertIsNone(f.get("heading"), f"seq={f.get('seq')}: a bearing survived a silent compass")
        self.assertIsNotNone(self.broken[-1].get("speed_src"), "nav was not answering, so nothing was stamped anyway")

    def test_the_mark_that_qualifies_a_bearing_goes_with_the_bearing(self):
        for f in self.broken:
            self.assertIsNone(
                f.get("gyro_only"),
                f"seq={f.get('seq')}: 'coasting on the gyro, on purpose' beside a blank "
                f"bearing describes a number that is not on screen",
            )

    def test_the_bearing_and_the_badge_beside_it_describe_the_same_number(self):
        """heading and mag_cal come off ONE chip, and the console depends on it.

        The number goes through render.js's gate, which lets the hull's own fault list
        VETO a value it is still shipping. `headingFlag()` does not: it reads
        `state.heading` and `state.magCal` straight, with no `faultedNow` anywhere in it.
        The two therefore agree only for as long as the vehicle nulls the bearing and the
        calibration mark in the same frame — which api/rov.py does because it takes both
        off the same hardware handle. The instant a hull ships one without the other, the
        console draws a question mark for the bearing and a badge describing a number
        that is not on screen. So the coupling is asserted here, on every frame of every
        phase, rather than left as a comment in the client.
        """
        for f in self.healthy + self.broken + self.mended:
            self.assertEqual(
                f.get("heading") is None,
                f.get("mag_cal") is None,
                f"seq={f.get('seq')}: heading={f.get('heading')!r} and "
                f"mag_cal={f.get('mag_cal')!r} — one chip answered for one of them and "
                f"not the other, and the console's bearing and its badge now disagree",
            )

    def test_the_console_says_NO_BEARING_and_not_one_of_its_three_neighbours(self):
        c = console_of(self.healthy + self.broken)
        self.assertEqual("dead", c.heading_flag())
        self.assertEqual("NO BEARING", c.heading_badge())
        self.assertIn("dead-heading", c.alert_ids())
        self.assertEqual("NO BEARING · COMPASS STOPPED", c.alert_text("dead-heading"))

    def test_the_console_raises_one_chip_for_the_whole_module(self):
        ids = console_of(self.healthy + self.broken).alert_ids()
        self.assertEqual(["dead-heading"], [i for i in ids if i.startswith("dead-")])
        self.assertNotIn("faults", ids)

    def test_the_badge_clears_when_the_compass_answers_again(self):
        c = console_of(self.healthy + self.broken + self.mended)
        self.assertEqual(
            "", c.heading_flag(), f"the console still qualifies a bearing that is back: {c.heading_badge()}"
        )
        self.assertEqual([], c.alert_ids())

    def test_a_compass_that_answers_badly_is_not_a_compass_that_is_gone(self):
        """mag_cal 0 and mag_cal null are different facts with different errands.

        Driven here rather than assumed, because it is the one pair a cannot-tell is
        easiest to spell as: 0 says a chip answered and reports itself uncalibrated —
        swing the sub through a few figure-eights — and null says there is nothing there
        to swing.
        """
        d = dive()
        d.hw._set_mag_cal(0)
        try:
            frames = d.fly(SETTLE_S, ballast="hold")
            self.assertEqual(0, frames[-1].get("mag_cal"))
            self.assertIsInstance(frames[-1].get("heading"), (int, float), "a badly calibrated compass still bears")
            self.assertEqual([], frames[-1].get("sensor_faults"), "an uncalibrated compass is not a faulted part")
            c = console_of(frames)
            self.assertEqual("mag", c.heading_flag())
            self.assertEqual("MAG?", c.heading_badge())
        finally:
            d.hw._set_mag_cal(3)
            d.fly(SETTLE_S, ballast="hold")


class PackMonitorTest(PartCase, WireCase):
    """The INA219 — volts and amps off one chip, so they die together."""

    PART = CHIP = "ina219"
    NULL_FIELDS = ("battery_v", "current_a")
    # 0.0 V is the most alarming number this gauge can show and no vehicle has ever been
    # at it while transmitting: an absent sensor used to reach the rail as
    # "BATTERY 0.0V · SURFACE", a critical alarm invented whole by the missing chip.
    FORBIDDEN = {"battery_v": (0.0, 0), "current_a": (0.0, 0)}

    def test_the_console_raises_no_alarm_about_a_pack_nobody_measured(self):
        c = console_of(self.healthy + self.broken)
        self.assertNotIn("batt", c.alert_ids(), "an absent INA219 raised the SURFACE prompt")
        self.assertIn("dead-battery", c.alert_ids())
        self.assertEqual("NO PACK VOLTAGE & CURRENT · PACK MONITOR STOPPED", c.alert_text("dead-battery"))

    def test_the_pack_reads_as_a_2s_pack_when_it_is_back(self):
        volts = self.mended[-1]["battery_v"]
        self.assertTrue(6.0 <= volts <= 8.6, f"battery_v={volts} is not a 2S Li-ion pack")


class LeakProbeTest(PartCase, WireCase):
    """The two wires in the bottom of the hull, and the sampler that stopped reading them.

    NOT A CHIP, so nothing here blanks a number: `leak_state` is a required string on the
    wire and its cannot-tell is the word UNKNOWN. Same rule, different spelling — and the
    one that is easiest to forget, because NORMAL is a positive claim that looks like a
    safe default.
    """

    PART = CHIP = "leak-probes"
    NULL_FIELDS = ()

    def test_1_healthy_the_readings_are_numbers_on_the_wire(self):
        for f in self.healthy:
            self.assertEqual("NORMAL", f.get("leak_state"), "the hull is not certified dry to begin with")
            self.assertIs(False, f.get("leak"))

    def test_3_the_wire_goes_to_cannot_tell(self):
        for f in self.broken:
            self.assertEqual(
                "UNKNOWN",
                f.get("leak_state"),
                f"seq={f.get('seq')}: nobody is sampling the probes and the wire still says "
                f"{f.get('leak_state')!r}",
            )

    def test_4_the_reading_never_becomes_a_plausible_number(self):
        for f in self.broken:
            self.assertNotEqual("NORMAL", f.get("leak_state"), "NORMAL is a claim the hull is dry, not a fallback")
            self.assertIs(
                True,
                f.get("leak"),
                "the one-bit answer is NOT-CERTIFIED-DRY, and a bool-only client reading "
                "false here is handed the one claim UNKNOWN exists to withhold",
            )

    def test_4_the_reading_did_not_freeze_at_its_last_good_value(self):
        self.assertEqual("NORMAL", self.healthy[-1]["leak_state"])
        self.assertEqual("UNKNOWN", self.broken[-1]["leak_state"])

    def test_5_the_reading_comes_back(self):
        self.assertEqual("NORMAL", self.mended[-1].get("leak_state"))
        self.assertNotIn(self.CHIP, self.mended[-1].get("sensor_faults") or [])

    def test_the_console_shows_a_question_mark_and_names_the_part(self):
        healthy = console_of(self.healthy)
        self.assertEqual("leak-normal", healthy.leak_glyph_class())
        broken = console_of(self.healthy + self.broken)
        self.assertEqual(
            "leak-unknown",
            broken.leak_glyph_class(),
            "the console would draw the green struck-through drop — 'both probes were "
            "read and neither was wet' — over a hull nothing is watching",
        )
        self.assertIn("leakunknown", broken.alert_ids())
        self.assertEqual("HULL STATE UNKNOWN · LEAK PROBES STOPPED", broken.alert_text("leakunknown"))
        self.assertNotIn(
            "faults", broken.alert_ids(), "the sampler was named twice, once as a fault nothing draws from"
        )
        mended = console_of(self.healthy + self.broken + self.mended)
        self.assertEqual("leak-normal", mended.leak_glyph_class())
        self.assertEqual([], mended.alert_ids())

    def test_water_already_found_is_not_talked_back_down_to_cannot_tell(self):
        """An established fact does not expire because the sampler stopped.

        Wet outranks cannot-tell in both directions this matters: on the wire, where the
        vehicle keeps reporting WARN with nobody sampling, and on the console, where the
        latch a WARN raised must survive the stage going UNKNOWN.
        """
        d = dive()
        try:
            d.hw._set_leak("WARN")
            wet = d.fly(SETTLE_S, ballast="hold")
            self.assertEqual("WARN", wet[-1]["leak_state"])
            d.stall("leak-probes")
            still = d.fly(BROKEN_S, ballast="hold")
            for f in still:
                self.assertEqual(
                    "WARN",
                    f.get("leak_state"),
                    f"seq={f.get('seq')}: water that had already reached a probe was talked "
                    f"back down to cannot-tell when the sampler stopped",
                )
            c = console_of(wet + still)
            self.assertEqual("leak-warn", c.leak_glyph_class())
            self.assertIn("leakwarn", c.alert_ids())
        finally:
            d.unstall("leak-probes")
            d.hw._set_leak("NORMAL")
            d.fly(SETTLE_S, ballast="hold")

    def test_a_broken_probe_pair_is_reported_rather_than_believed(self):
        """A dead probe reads dry forever, which is the one failure this design hides."""
        d = dive()
        try:
            d.hw._set_probe_wet(True, True)
            d.hw._set_probe_wet_at_boot(True, False)
            frames = d.fly(SETTLE_S, ballast="hold")
            self.assertIsNotNone(frames[-1].get("leak_probe_fault"), "a probe wet at power-on was believed")
            c = console_of(frames)
            self.assertIn("probe", c.alert_ids())
            self.assertNotEqual("leak-normal", c.leak_glyph_class(), "a faulted probe certified the hull dry")
        finally:
            d.hw._set_probe_wet(False, False)
            d.hw._set_probe_wet_at_boot(False, False)
            d.fly(SETTLE_S, ballast="hold")

    def test_unsampled_probes_do_not_take_navigation_down(self):
        self.assertIsNotNone(self.broken[-1].get("speed_src"))


class SensorThreadTest(PartCase, WireCase):
    """The loop that samples everything — the failure that hides all the others.

    Its cannot-tells have no null to spend either: the hull goes UNKNOWN and the link
    bars go to -1 rather than a frozen four.
    """

    PART = CHIP = "sensor-thread"
    NULL_FIELDS = ()

    def test_1_healthy_the_readings_are_numbers_on_the_wire(self):
        for f in self.healthy:
            self.assertGreaterEqual(f.get("signal"), 0, "the bars are not being reported to begin with")
            self.assertEqual("NORMAL", f.get("leak_state"))

    def test_3_the_wire_goes_to_cannot_tell(self):
        for f in self.broken:
            self.assertLess(
                f.get("signal"),
                0,
                f"seq={f.get('seq')}: {f.get('signal')} bars off a sampler that has stopped — "
                f"bars are a claim the tether is up",
            )
            self.assertEqual("UNKNOWN", f.get("leak_state"))

    def test_4_the_reading_never_becomes_a_plausible_number(self):
        for f in self.broken:
            self.assertNotEqual(4, f.get("signal"), "four frozen bars")
            self.assertNotEqual("NORMAL", f.get("leak_state"))

    def test_4_the_reading_did_not_freeze_at_its_last_good_value(self):
        self.assertGreaterEqual(self.healthy[-1]["signal"], 0)
        self.assertLess(self.broken[-1]["signal"], 0)

    def test_5_the_reading_comes_back(self):
        last = self.mended[-1]
        self.assertGreaterEqual(last.get("signal"), 0)
        self.assertEqual("NORMAL", last.get("leak_state"))
        self.assertNotIn(self.CHIP, last.get("sensor_faults") or [])

    def test_the_console_shows_a_question_mark_and_names_the_part(self):
        broken = console_of(self.healthy + self.broken)
        self.assertEqual("leak-unknown", broken.leak_glyph_class())
        self.assertIn("leakunknown", broken.alert_ids())
        self.assertEqual("HULL STATE UNKNOWN · SENSOR LOOP STOPPED", broken.alert_text("leakunknown"))
        self.assertNotIn("faults", broken.alert_ids())
        mended = console_of(self.healthy + self.broken + self.mended)
        self.assertEqual("leak-normal", mended.leak_glyph_class())
        self.assertEqual([], mended.alert_ids())

    def test_the_probes_stop_with_the_loop_that_samples_them(self):
        # The mock inherits the Pi's coupling rather than modelling two failures the
        # vehicle cannot have: on the Pi the probes are sampled BY this thread.
        for f in self.broken:
            self.assertIn(self.CHIP, f.get("sensor_faults") or [])
            self.assertIs(True, f.get("leak"))

    def test_a_dead_sampler_does_not_take_navigation_down(self):
        self.assertIsNotNone(self.broken[-1].get("speed_src"))


# ===========================================================================
# Everything at once, and everything healthy
# ===========================================================================
class WholeHullTest(WireCase):
    """The two ends of the ladder: nothing wrong, and everything wrong at once."""

    def test_a_healthy_hull_says_nothing_is_wrong_and_means_it(self):
        frames = dive().fly(SETTLE_S, ballast="hold")
        last = frames[-1]
        for field in ("depth", "pressure", "heading", "mag_cal", "battery_v", "current_a", "gyro_z_dps", "pitch_deg"):
            self.assertIsNotNone(last.get(field), f"{field} is cannot-tell on a hull with nothing wrong with it")
        self.assertEqual([], last.get("sensor_faults"))
        self.assertEqual("NORMAL", last.get("leak_state"))
        c = console_of(frames)
        self.assertEqual([], c.alert_ids(), "a healthy hull put chips on the alert rail")
        self.assertEqual("leak-normal", c.leak_glyph_class())
        self.assertEqual("", c.heading_flag())

    def test_one_dead_bus_reads_as_every_chip_on_it_and_the_console_keeps_up(self):
        """All three I2C chips at once — one unplugged connector, on this vehicle.

        The console has to survive naming three parts in one frame without any of them
        falling through unexplained: a fault the screen drops on the floor is the
        round-three mistake in miniature.
        """
        d = dive()
        healthy = d.fly(SETTLE_S, ballast="hold")
        for chip in ("ms5837", "bno085", "ina219"):
            d.kill(chip)
        try:
            broken = d.fly(BROKEN_S, ballast="hold")
            for f in broken:
                self.assertNoContradiction(f)
                self.assertCardinalAgrees(f)
                self.assertEqual(["bno085", "ina219", "ms5837"], sorted(f.get("sensor_faults") or []))
            c = console_of(healthy + broken)
            self.assertEqual(
                ["dead-depth", "dead-heading", "dead-battery"],
                [i for i in c.alert_ids() if i.startswith("dead-")],
            )
            self.assertNotIn("faults", c.alert_ids(), "a named part explained nothing on screen")
            self.assertNotIn("batt", c.alert_ids())
            self.assertEqual("NO BEARING", c.heading_badge())
        finally:
            for chip in ("ms5837", "bno085", "ina219"):
                d.revive(chip)
            d.fly(SETTLE_S, ballast="hold")

    def test_everything_comes_back_together(self):
        d = dive()
        d.quiesce()
        frames = d.fly(SETTLE_S, ballast="hold")
        self.assertEqual([], frames[-1].get("sensor_faults"))
        self.assertEqual([], console_of(frames).alert_ids())
