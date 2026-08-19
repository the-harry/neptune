"""The brainstem link — every sensor and slow actuator lives behind an ESP32 now.

THE SPLIT (docs/hardware.md §8). The Pi is the commander: camera, thrusters,
navigation, blackbox, tether. The ESP32-WROOM-32 is the brainstem: it owns the
I2C chips (BNO085, MS5837, INA219 ×2), the three leak zones, the NTC, the flow
and PAS pulse counting, the ballast pump loop, the lamp and beacon, and the
burn-wire ARM+FIRE interlock — plus the REFLEXES that must survive a hung Pi
(leak agreement → beacon + bag empty; pack undervolt warning). The two meet on
one USB serial cable, and this module is the Pi's half of that cable.

THE PROTOCOL, in one paragraph (the firmware in firmware/brainstem/ is the other
implementation; the two are kept in step BY HAND and this docstring is the
contract). Line-delimited JSON, UTF-8, one object per line, both directions.
Up (ESP32→Pi), unprompted: a `hello` on boot, `tlm` at 10 Hz, `ack` for every
command, `evt` for discrete events (pump done, homed, burn fired, leak latch).
Down (Pi→ESP32): `{"t":"cmd","id":N,"name":...,"value":...}` — the vocabulary is
COMMANDS below. Every command is acked with its id; acks join the blackbox c_id
chain as the vehicle-executed stage. Unknown fields are ignored by both ends,
unknown commands are acked ok=false — a version skew degrades, never crashes.

LIVENESS IS THE BUS-FRONT RULE, one level up (docs/hardware.md §13). The link
has a DeviceHealth of its own: a frame within `silence_s` or the whole brainstem
is faulted and EVERY reading behind it answers cannot-tell under the one name
"brainstem" — exactly as `i2c` fronts its chips today. When the link is healthy,
the per-chip verdicts are the ESP32's own: it runs the same
streak-or-silence-per-chip rule at the bus, and its `faults`/`absent` lists ride
every telemetry frame in the same vocabulary this repo already speaks
("bno085", "ms5837", "ina219", …). The firmware nulls a dead chip's readings in
the same frame that names it, so the null and the name stay one decision.

TRANSPORT IS INJECTED. The default is a pyserial port found by `find_port()`,
but the constructor takes any object with `readline()/write()/close()`, so every
rule in this file runs on a bench against an in-memory pipe — the same reason
DeviceHealth is clock-injected. pyserial itself is imported lazily inside
SerialTransport: the bench must run without it.

NOTHING HERE BLOCKS THE EVENT LOOP. Reads happen on this module's own thread;
`send()` enqueues and returns; the only method that waits is `request()`, whose
callers accept its stated timeout (leak re-arm — a human pressed a button and a
half-second verdict beats a lie). Snapshot reads are single-reference rebinds
under the GIL, the same discipline as the old sensor cache.
"""

from __future__ import annotations

import glob
import json
import logging
import threading
import time
from collections import deque

from config import settings

# DeviceHealth is the liveness rule the whole vehicle runs on; importing it
# from hardware is safe because hardware never imports this module at module
# scope (RealHardware pulls it lazily inside __init__, same as gpiozero).
from hardware import DeviceHealth

log = logging.getLogger("neptune.brainstem")

# Protocol version this side speaks. The firmware sends its own in `hello`; a
# mismatch is LOGGED, not fatal — unknown fields already degrade safely, and a
# bench with old firmware should light up honestly rather than refuse to exist.
PROTO_VERSION = 1

# The command vocabulary, spelled once. Anything else is acked ok=false by the
# firmware — send nothing that is not in this set.
COMMANDS = frozenset(
    {
        "ping",  # -> ack {ok:true, result:{"ms":...}}
        "pump",  # value -1|0|1  — continuous run: empty | stop | fill
        "pump_ml",  # value ±ml    — metered run, acked started; evt pump_done carries measured
        "trim_home",  # purge-home against the empty bag; zeroes ballast_ml
        "lamp",  # value 0..1   — white lamp duty (LEDC 8 kHz on the ESP32)
        "beacon",  # value 0|1    — red locator, 0.2 s / 1.8 s pattern
        "arm_burn",  # value 0|1    — the ARM half of the interlock (auto-disarms)
        "fire_burn",  # the FIRE half; refused unless armed
        "leak_reset",  # re-arm the leak latches; refused while any zone is wet NOW
        "mock",  # value 0|1    — firmware bench mode: simulated readings, announced
        "kill",  # value chip   — bench-mode fault injection, same names as faults[]
        "revive",  # value chip
        "info",  # -> ack {ok:true, result:{fw, proto, mode, ...}}
        "ring",  # dump the event ring buffer as evt lines (the third witness)
    }
)

# How stale the newest frame may be before the LINK is faulted. The firmware
# sends at 10 Hz, so 1.5 s is fifteen missed frames — a dead cable, a wedged
# firmware, or an unplugged board, never jitter.
LINK_SILENCE_S = 1.5

# The leak zones, in wire order. Index i of every leak_* array is this zone.
LEAK_ZONES = ("fwd", "mid", "aft")

# The ESP32 samples its leak zones at 10 Hz and latches after the same
# five-sample debounce the Pi used to run (docs/hardware.md §13). Named here
# because the pin-to-console latency budget is derived from it
# (api/tests/test_latency.py) and a magic number inside the firmware would let
# that budget go stale in silence.
LEAK_SAMPLE_HZ = 10.0


def find_port() -> str | None:
    """The brainstem's serial port, or None if nothing plausible is plugged in.

    Order: the explicit setting, the udev name the Pi installs
    (deploy/udev/99-neptune-brainstem.rules pins /dev/ttyESP by serial number),
    then the USB-serial patterns a devkit enumerates as — on the Pi/laptop
    (ttyUSB*/ttyACM*) and on a Mac bench (cu.usbserial*/cu.SLAB*/cu.wch*).

    DELIBERATELY NARROW. A Mac always has /dev/cu.Bluetooth-Incoming-Port and
    friends, and "some serial device exists" must not put a real backend on a
    port nothing NEPTUNE ever answers on — the handshake in RealHardware guards
    the rest, but not scanning junk is cheaper than timing out on it.
    """
    if settings.brainstem_port:
        return settings.brainstem_port
    for pattern in (
        "/dev/ttyESP",
        "/dev/ttyUSB*",
        "/dev/ttyACM*",
        "/dev/cu.usbserial*",
        "/dev/cu.SLAB_USBtoUART*",
        "/dev/cu.wchusbserial*",
    ):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return None


class SerialTransport:
    """pyserial, behind the three methods BrainstemLink actually needs.

    Import is lazy and inside __init__ for the standing reason: this file is
    imported on benches that have never installed pyserial, and a module-scope
    import would take the whole server down to report a missing nicety.
    """

    def __init__(self, port: str, baud: int) -> None:
        import serial  # noqa: PLC0415 — lazy on purpose, see docstring

        # timeout: the reader thread's poll granularity. write_timeout keeps a
        # wedged USB buffer from stalling a command send into the event loop's
        # patience; a command that cannot be written in 200 ms is a command the
        # link health is about to explain anyway.
        self._ser = serial.Serial(port, baudrate=baud, timeout=0.25, write_timeout=0.2)
        self.port = port

    def readline(self) -> bytes:
        return self._ser.readline()

    def write(self, data: bytes) -> None:
        self._ser.write(data)

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:  # noqa: BLE001
            pass


class BrainstemLink:
    """One ESP32, one thread, one snapshot — the Pi's view of everything sensed.

    `snapshot()` hands back the newest telemetry dict (never mutated in place —
    the reader rebinds a fresh dict per frame, so a caller can hold one without
    locks). `link_ok()` is the front verdict every readback gates on. `send()`
    fire-and-forgets a command; `request()` waits briefly for its ack, for the
    few callers whose semantics need the vehicle's answer.
    """

    def __init__(self, transport, clock=time.monotonic) -> None:
        self._transport = transport
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        # The newest tlm frame, verbatim (plus nothing): one reference, rebound
        # whole by the reader, read by anyone. None until the first frame.
        self._frame: dict | None = None
        self.hello: dict | None = None
        # The link's own liveness — the bus-front. never-answered is faulted,
        # so a link that opens a port and hears nothing stays down.
        self.health = DeviceHealth("brainstem", fail_streak=1, silence_s=LINK_SILENCE_S)
        self._next_id = 1
        self._id_lock = threading.Lock()
        # id -> (Event, slot) for requests in flight; resolved by the reader.
        self._pending: dict[int, tuple[threading.Event, dict]] = {}
        # The last few events, for tests and for the log bundle. The REAL third
        # witness is the ring on the ESP32 itself (the `ring` command dumps it);
        # this is just the Pi-side tail of what already crossed the wire.
        self.events: deque[dict] = deque(maxlen=64)
        # Sequence bookkeeping — a gap is a dropped frame worth counting, not
        # worth alarming: telemetry is droppable by design, commands are acked.
        self._last_seq: int | None = None
        self.seq_gaps = 0

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> "BrainstemLink":
        self._thread = threading.Thread(target=self._read_loop, name="neptune-brainstem", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._transport.close()

    def wait_first_frame(self, timeout_s: float) -> bool:
        """Block (construct-time only) until anything valid arrives, or not.

        This is the handshake that keeps a junk serial device from becoming a
        'real' vehicle: a port that never says anything NEPTUNE-shaped inside
        the window is a port we do not have a brainstem on.
        """
        deadline = self._clock() + timeout_s
        while self._clock() < deadline:
            if self._frame is not None or self.hello is not None:
                return True
            time.sleep(0.02)
        return self._frame is not None or self.hello is not None

    # ---- the operator-facing verdicts ------------------------------------
    def link_ok(self, now: float | None = None) -> bool:
        return not self.health.faulted(self._clock() if now is None else now)

    def snapshot(self) -> dict | None:
        """The newest tlm frame, or None. Callers must treat missing keys as
        cannot-tell — old firmware sending fewer fields degrades, never lies."""
        return self._frame

    def value(self, key: str):
        """One reading off the newest frame, gated on the LINK only.

        None when the link is down, the frame is missing the key, or the
        firmware itself sent null — three absences, one honest answer. Per-chip
        gating beyond this is the firmware's job: it nulls a dead chip's fields
        in the same frame that names the chip in faults[].
        """
        if not self.link_ok():
            return None
        frame = self._frame
        if frame is None:
            return None
        return frame.get(key)

    def faults(self) -> tuple[str, ...]:
        """The vocabulary the console names parts in, fronted by the link.

        Link down → ("brainstem",) and nothing else: the chips behind a dead
        link are unknowable, and naming them would claim knowledge the Pi does
        not have — same shape as `i2c` fronting its three chips.
        """
        if not self.link_ok():
            return ("brainstem",)
        frame = self._frame or {}
        return tuple(sorted(str(f) for f in frame.get("faults", ())))

    def absent(self) -> tuple[str, ...]:
        """Which of faults() the ESP32 says have NEVER answered — leaf parts
        only, decided at the bus by the only layer that can know. Empty when
        the link is down: 'cannot tell them apart' is the loud default."""
        if not self.link_ok():
            return ()
        frame = self._frame or {}
        return tuple(sorted(str(f) for f in frame.get("absent", ())))

    @property
    def bench_mode(self) -> bool:
        """True when the firmware says its readings are SIMULATED. Rides every
        frame so it cannot be missed; the Pi surfaces it as telemetry mock=true
        — announced simulation is the only acceptable kind."""
        frame = self._frame
        return bool(frame and frame.get("mode") == "bench")

    # ---- commands --------------------------------------------------------
    def send(self, name: str, value=None, cmd_id: int | None = None) -> int | None:
        """Enqueue one command; returns its id, or None if it could not be
        written. Never blocks past the transport's write timeout. The ack is
        logged by the reader; callers that need it use request(), which
        pre-allocates the id it passes in so the two cannot race apart."""
        if name not in COMMANDS:
            log.warning("brainstem: refusing unknown command %r", name)
            return None
        if cmd_id is None:
            with self._id_lock:
                cmd_id = self._next_id
                self._next_id += 1
        msg: dict = {"t": "cmd", "id": cmd_id, "name": name}
        if value is not None:
            msg["value"] = value
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        try:
            with self._write_lock:
                self._transport.write(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("brainstem: command %s(%r) could not be written: %s", name, value, exc)
            return None
        return cmd_id

    def request(self, name: str, value=None, timeout_s: float = 0.5) -> dict | None:
        """Send and wait briefly for the ack. Returns the ack dict or None.

        The ONLY waiting call in this module, for the few commands whose caller
        must relay the vehicle's verdict (leak_reset's refusal names the wet
        zone). The timeout is short on purpose: it runs on the event loop, and
        a vehicle that cannot ack in half a second has a link problem the
        health verdict is about to name anyway.
        """
        ev = threading.Event()
        slot: dict = {}
        with self._id_lock:  # allocate HERE so no concurrent send() can take this id
            cmd_id = self._next_id
            self._next_id += 1
        self._pending[cmd_id] = (ev, slot)
        try:
            sent = self.send(name, value, cmd_id=cmd_id)
            if sent is None:
                return None
            if not ev.wait(timeout_s):
                return None
            return dict(slot)
        finally:
            self._pending.pop(cmd_id, None)

    # ---- the reader thread ------------------------------------------------
    def _read_loop(self) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = self._transport.readline()
            except Exception as exc:  # noqa: BLE001
                # A transport that raises is a link that is down; the health
                # window will say so within LINK_SILENCE_S. Sleep so a
                # permanently broken port does not spin a core.
                log.debug("brainstem read failed: %s", exc)
                time.sleep(0.25)
                continue
            if not chunk:
                continue
            # pyserial readline() honours the timeout and can return a partial
            # line; reassemble so a slow USB burst cannot split a frame.
            buf += chunk
            if not buf.endswith(b"\n"):
                if len(buf) > 65536:  # a line this long is garbage, not JSON
                    buf = b""
                continue
            lines, buf = buf.split(b"\n"), b""
            for raw in lines:
                raw = raw.strip()
                if raw:
                    self._handle_line(raw)

    def _handle_line(self, raw: bytes) -> None:
        try:
            msg = json.loads(raw)
        except Exception:  # noqa: BLE001
            # Boot chatter: the ESP32 ROM prints plain text on reset. Not a
            # protocol error, not worth a line each.
            log.debug("brainstem: non-JSON line ignored: %r", raw[:80])
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("t")
        now = self._clock()
        if kind == "tlm":
            seq = msg.get("seq")
            if isinstance(seq, int) and self._last_seq is not None and seq > self._last_seq + 1:
                self.seq_gaps += seq - self._last_seq - 1
            if isinstance(seq, int):
                self._last_seq = seq
            self._frame = msg
            self.health.ok(now)
        elif kind == "ack":
            self.health.ok(now)
            pending = self._pending.get(msg.get("id"))
            if pending is not None:
                ev, slot = pending
                slot.update(msg)
                ev.set()
            if not msg.get("ok", False):
                log.warning("brainstem NACK id=%s: %s", msg.get("id"), msg.get("err"))
        elif kind == "evt":
            self.health.ok(now)
            self.events.append(msg)
            # Events are the discrete things a dive log wants: say them in the
            # vehicle's log too, where the blackbox interleaves them.
            log.info("brainstem event: %s %s", msg.get("name"), {k: v for k, v in msg.items() if k not in ("t", "name")})
        elif kind == "hello":
            self.hello = msg
            self.health.ok(now)
            proto = msg.get("proto")
            log.info(
                "brainstem hello: fw=%s proto=%s mode=%s reset=%s",
                msg.get("fw"),
                proto,
                msg.get("mode"),
                msg.get("reset"),
            )
            if proto != PROTO_VERSION:
                log.warning(
                    "brainstem speaks protocol %s, this side speaks %s — unknown fields degrade "
                    "safely, but update the older half",
                    proto,
                    PROTO_VERSION,
                )
        elif kind == "ring":
            # Replayed witness lines from the ESP32's ring buffer; keep them
            # with the events so a bundle picks them up.
            self.events.append(msg)
        else:
            log.debug("brainstem: unknown frame type %r ignored", kind)


def open_link() -> BrainstemLink | None:
    """Find, open and start the real link, or None with the reason logged.

    None is not an error state — it is 'no brainstem on this machine', and the
    caller (RealHardware) turns it into the named fault the console shows.
    """
    port = find_port()
    if port is None:
        log.info("brainstem: no serial port found (set NEPTUNE_BRAINSTEM_PORT to pin one)")
        return None
    try:
        transport = SerialTransport(port, settings.brainstem_baud)
    except Exception as exc:  # noqa: BLE001
        log.warning("brainstem: %s would not open: %s", port, exc)
        return None
    log.info("brainstem: opened %s at %d baud", port, settings.brainstem_baud)
    return BrainstemLink(transport).start()
