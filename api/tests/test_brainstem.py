"""Brainstem-link unit tests — the commander/brainstem split, on the bench.

Run:  cd api && python -m unittest tests.test_brainstem -v

The split (docs/hardware.md §8) put every sensor behind an ESP32 on USB serial,
and this suite is the reason that architecture is testable at all: the transport
is INJECTED, so every rule below runs against an in-memory pipe with no devkit,
no pyserial and no GPIO stack — which is exactly the machine this file is being
written on. What the firmware does at the far end is mirrored by hand-built
frames here; firmware/brainstem/brainstem.ino and api/brainstem.py's docstring
are the contract both sides answer to.

The failures these tests exist to prevent are the split's own versions of the
ones this repo keeps burying: a link that dies leaving the last frame's numbers
on screen; a chip fault swallowed on its way through the serial hop; a bench
mode that forgets to announce itself; a leak latch that a link dropout talks the
console down from; a thruster group that quietly pretends to exist on a machine
with no GPIO.

stdlib unittest only — and deliberately NO pydantic anywhere in the import
chain, so this suite loads (and the split stays guarded) on a python where the
protocol suites report DEPS.
"""

from __future__ import annotations

import json
import logging
import queue
import time
import unittest

from brainstem import COMMANDS, LEAK_SAMPLE_HZ, PROTO_VERSION, BrainstemLink, find_port
from config import settings
from hardware import LEAK_UNKNOWN, RealHardware
from nav.config import settings as nav_settings


class FakeClock:
    """An injectable monotonic clock, advanced by hand — the same trick
    DeviceHealth is built around, one layer up."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


class FakeTransport:
    """The ESP32's end of the cable, driven from the test.

    feed() queues a frame for the reader thread; sent[] records every command
    the Pi side wrote, already parsed, so an assertion reads intent rather than
    bytes.
    """

    def __init__(self) -> None:
        self._in: queue.Queue[bytes] = queue.Queue()
        self.sent: list[dict] = []
        self.closed = False

    def feed(self, obj: dict) -> None:
        self._in.put((json.dumps(obj) + "\n").encode())

    def feed_raw(self, raw: bytes) -> None:
        self._in.put(raw)

    def readline(self) -> bytes:
        try:
            return self._in.get(timeout=0.02)
        except queue.Empty:
            return b""

    def write(self, data: bytes) -> None:
        self.sent.append(json.loads(data.decode()))

    def close(self) -> None:
        self.closed = True


def tlm(**over) -> dict:
    """A healthy telemetry frame, exactly as the firmware builds one; override
    per test. The defaults describe a vehicle with everything answering."""
    frame = {
        "t": "tlm",
        "seq": 1,
        "ms": 123,
        "mode": "real",
        "heading": 284.0,
        "mag_cal": 3,
        "gyro_z": 0.1,
        "accel_fwd": 0.0,
        "pitch": 1.0,
        "roll": -0.5,
        "press_psi": 15.2,
        "water_c": 12.0,
        "pack_v": 12.4,
        "pack_a": 0.42,
        "rail_v": 8.0,
        "rail_a": 0.0,
        "ntc_c": 21.0,
        "leak_ok": True,
        "leak_raw": [False, False, False],
        "leak_latch": [False, False, False],
        "leak_boot": [False, False, False],
        "ballast_ml": None,
        "ballast_homed": False,
        "ballast_fault": False,
        "pump": 0,
        "flow_ml": 0.0,
        "speed_hz": 0.0,
        "speed_fresh": False,
        "speed_dir": 0,
        "lamp": 0.0,
        "beacon": False,
        "burn_armed": False,
        "burn_fired": False,
        "reflex_surface": False,
        "undervolt": False,
        "faults": [],
        "absent": [],
    }
    frame.update(over)
    return frame


def wait_for(cond, timeout_s: float = 1.0) -> bool:
    """Poll a condition while the link's real reader thread works."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


def make_link(clock=None) -> tuple[BrainstemLink, FakeTransport]:
    tr = FakeTransport()
    link = BrainstemLink(tr, clock=clock or time.monotonic).start()
    return link, tr


def sent_named(tr: FakeTransport, name: str) -> list[dict]:
    return [m for m in tr.sent if m.get("name") == name]


class LinkProtocolTest(unittest.TestCase):
    def tearDown(self) -> None:
        self.link.close()

    def test_a_frame_updates_the_snapshot_and_the_link_comes_up(self):
        self.link, tr = make_link()
        self.assertFalse(self.link.link_ok())
        tr.feed(tlm(heading=101.5))
        self.assertTrue(wait_for(lambda: self.link.snapshot() is not None))
        self.assertTrue(self.link.link_ok())
        self.assertEqual(self.link.value("heading"), 101.5)

    def test_never_answered_is_down_not_merely_quiet(self):
        # A port that opened but said nothing is not a brainstem. The health
        # starts faulted (never-answered), and every reading behind it is None.
        self.link, _ = make_link()
        self.assertFalse(self.link.link_ok())
        self.assertEqual(self.link.faults(), ("brainstem",))
        self.assertIsNone(self.link.value("heading"))

    def test_silence_takes_the_link_down_and_the_front_name_stands_alone(self):
        # THE BUS-FRONT RULE, one level up: after 1.5 s of no frames the one
        # name "brainstem" fronts everything — the chips behind a dead link are
        # unknowable and naming them would claim knowledge nobody has.
        clock = FakeClock()
        self.link, tr = make_link(clock=clock)
        tr.feed(tlm(faults=["bno085"]))
        self.assertTrue(wait_for(lambda: self.link.snapshot() is not None))
        self.assertEqual(self.link.faults(), ("bno085",))
        self.assertEqual(self.link.value("pack_v"), 12.4)
        clock.t += 2.0  # fifteen missed frames later…
        self.assertFalse(self.link.link_ok())
        self.assertEqual(self.link.faults(), ("brainstem",))
        self.assertIsNone(self.link.value("pack_v"), "a dead link must not serve the last frame")

    def test_the_vehicles_own_faults_pass_through_verbatim(self):
        self.link, tr = make_link()
        tr.feed(tlm(faults=["ms5837", "bno085"], absent=["ms5837"], press_psi=None))
        self.assertTrue(wait_for(lambda: self.link.snapshot() is not None))
        self.assertEqual(self.link.faults(), ("bno085", "ms5837"))
        self.assertEqual(self.link.absent(), ("ms5837",))
        # The null and the name arrive in the same frame — one decision, twice.
        self.assertIsNone(self.link.value("press_psi"))

    def test_seq_gaps_are_counted_not_alarmed(self):
        self.link, tr = make_link()
        tr.feed(tlm(seq=1))
        tr.feed(tlm(seq=5))
        self.assertTrue(wait_for(lambda: self.link.seq_gaps == 3))
        self.assertTrue(self.link.link_ok())

    def test_an_ack_resolves_a_waiting_request(self):
        self.link, tr = make_link()
        tr.feed(tlm())
        self.assertTrue(wait_for(lambda: self.link.link_ok()))

        # The transport's write happens synchronously inside request(); feed
        # the matching ack from a helper thread the moment it appears.
        import threading

        def answer():
            wait_for(lambda: len(sent_named(tr, "ping")) == 1)
            tr.feed({"t": "ack", "id": sent_named(tr, "ping")[0]["id"], "ok": True, "result": {"ms": 5}})

        threading.Thread(target=answer, daemon=True).start()
        ack = self.link.request("ping", timeout_s=1.0)
        self.assertIsNotNone(ack)
        self.assertTrue(ack["ok"])

    def test_an_unanswered_request_times_out_to_none(self):
        self.link, tr = make_link()
        tr.feed(tlm())
        self.assertTrue(wait_for(lambda: self.link.link_ok()))
        self.assertIsNone(self.link.request("ping", timeout_s=0.1))

    def test_an_unknown_command_is_refused_on_this_side(self):
        self.link, tr = make_link()
        with self.assertLogs("neptune.brainstem", level="WARNING"):
            self.assertIsNone(self.link.send("format_sd"))
        self.assertEqual(tr.sent, [])

    def test_boot_chatter_and_garbage_do_not_kill_the_reader(self):
        self.link, tr = make_link()
        tr.feed_raw(b"ets Jul 29 2019 12:21:46\r\n")
        tr.feed_raw(b"\xff\xfe not json\n")
        tr.feed(tlm(heading=7.0))
        self.assertTrue(wait_for(lambda: self.link.value("heading") == 7.0))

    def test_bench_mode_rides_every_frame(self):
        # Announced simulation is the only acceptable kind: the mode is not a
        # handshake fact that can be missed, it is on every frame.
        self.link, tr = make_link()
        tr.feed(tlm(mode="bench"))
        self.assertTrue(wait_for(lambda: self.link.bench_mode))
        tr.feed(tlm(mode="real", seq=2))
        self.assertTrue(wait_for(lambda: not self.link.bench_mode))

    def test_events_are_kept_and_logged(self):
        self.link, tr = make_link()
        with self.assertLogs("neptune.brainstem", level="INFO"):
            tr.feed({"t": "evt", "name": "pump_done", "ml": 49.8})
            self.assertTrue(wait_for(lambda: len(self.link.events) == 1))
        self.assertEqual(self.link.events[0]["name"], "pump_done")

    def test_a_protocol_version_skew_is_logged_not_fatal(self):
        self.link, tr = make_link()
        with self.assertLogs("neptune.brainstem", level="WARNING"):
            tr.feed({"t": "hello", "fw": "x", "proto": PROTO_VERSION + 1})
            self.assertTrue(wait_for(lambda: self.link.hello is not None))
        self.assertTrue(self.link.link_ok())


def make_real(clock=None, first_frame=None) -> tuple[RealHardware, FakeTransport, BrainstemLink]:
    """RealHardware over an injected link, on a machine with no GPIO.

    The thruster group faults (there is no gpiozero here) and the backend still
    constructs — the breadboard-on-a-laptop case the split exists for.
    """
    tr = FakeTransport()
    link = BrainstemLink(tr, clock=clock or time.monotonic).start()
    tr.feed(first_frame or tlm())
    hw = RealHardware(link=link)
    return hw, tr, link


class RealHardwareSplitTest(unittest.TestCase):
    """The backend as it behaves with a brainstem and no GPIO stack."""

    def setUp(self) -> None:
        logging.getLogger("neptune.hw").setLevel(logging.CRITICAL)
        logging.getLogger("neptune.brainstem").setLevel(logging.CRITICAL)
        self.addCleanup(logging.getLogger("neptune.hw").setLevel, logging.NOTSET)
        self.addCleanup(logging.getLogger("neptune.brainstem").setLevel, logging.NOTSET)

    def test_it_constructs_with_a_brainstem_and_no_gpio_and_refuses_to_arm(self):
        hw, tr, link = make_real()
        self.addCleanup(hw.close)
        self.assertFalse(hw._gpio_available(), "this bench must not have a GPIO stack")
        self.assertIn("thrusters", hw.sensor_faults())
        hw.set_armed(True)
        self.assertFalse(hw._armed, "arming with no bridges hands the operator a dead stick")

    def test_it_refuses_to_construct_with_neither_half(self):
        # No GPIO AND a link that never says anything NEPTUNE-shaped: both
        # halves down is a bench, and auto belongs on the honest simulator.
        tr = FakeTransport()
        link = BrainstemLink(tr, clock=time.monotonic).start()
        old = RealHardware.HELLO_WAIT_S
        RealHardware.HELLO_WAIT_S = 0.1
        try:
            with self.assertRaises(RuntimeError):
                RealHardware(link=link)
        finally:
            RealHardware.HELLO_WAIT_S = old
        self.assertTrue(tr.closed, "a port that answered nothing must be released")

    def test_readbacks_map_the_frame_and_nulls_survive(self):
        hw, tr, link = make_real(
            first_frame=tlm(
                heading=100.0,
                press_psi=16.4,
                pack_v=12.2,
                pack_a=1.5,
                gyro_z=2.5,
                accel_fwd=0.3,
                pitch=4.0,
                roll=-2.0,
                mag_cal=2,
                water_c=9.5,
                rail_v=8.1,
                rail_a=3.2,
            )
        )
        self.addCleanup(hw.close)
        self.assertEqual(hw.read_heading(), (100.0 + nav_settings.imu_yaw_offset_deg) % 360.0)
        self.assertEqual(hw.read_pressure(), 16.4)
        self.assertEqual(hw.read_voltage(), 12.2)
        self.assertEqual(hw.read_current_a(), 1.5)
        self.assertEqual(hw.read_gyro_z_dps(), 2.5)
        self.assertEqual(hw.read_accel_fwd_ms2(), 0.3)
        self.assertEqual(hw.read_pitch_roll(), (4.0, -2.0))
        self.assertEqual(hw.read_mag_cal(), 2)
        self.assertEqual(hw.read_water_c(), 9.5)
        self.assertEqual(hw.read_rail_v(), 8.1)
        self.assertEqual(hw.read_rail_a(), 3.2)
        # And a chip the VEHICLE nulled stays null here — no resurrection.
        tr.feed(tlm(seq=2, heading=None, mag_cal=None, faults=["bno085"]))
        self.assertTrue(wait_for(lambda: hw.read_heading() is None))
        self.assertIsNone(hw.read_mag_cal())
        self.assertIn("bno085", hw.sensor_faults())

    def test_a_dead_link_fronts_everything_under_one_name(self):
        clock = FakeClock()
        hw, tr, link = make_real(clock=clock)
        self.addCleanup(hw.close)
        self.assertTrue(wait_for(lambda: link.snapshot() is not None))
        link.health.ok(clock.t)  # a frame landed at t
        self.assertEqual(hw.read_voltage(), 12.4)
        clock.t += 5.0  # fifteen missed frames later…
        self.assertIsNone(hw.read_voltage(), "a dead link must not serve the last frame's volts")
        self.assertIsNone(hw.read_heading())
        faults = hw.sensor_faults()
        self.assertIn("brainstem", faults)
        self.assertNotIn("bno085", faults, "chips behind a dead link are unknowable, not named")
        self.assertEqual(hw.sensors_absent(), ())

    def test_leak_zones_map_onto_the_ladder(self):
        hw, tr, link = make_real()
        self.addCleanup(hw.close)
        self.assertTrue(wait_for(lambda: link.snapshot() is not None))
        self.assertEqual(hw.read_leak(), "NORMAL")
        tr.feed(tlm(seq=2, leak_latch=[True, False, False]))
        self.assertTrue(wait_for(lambda: hw.read_leak() == "WARN"))
        # 2-of-3 agreement is corroborated water — the same rule the vehicle's
        # own reflex fires on.
        tr.feed(tlm(seq=3, leak_latch=[True, False, True], reflex_surface=True))
        self.assertTrue(wait_for(lambda: hw.read_leak() == "FLOOD"))

    def test_wet_outranks_cannot_tell_across_a_link_death(self):
        clock = FakeClock()
        hw, tr, link = make_real(clock=clock, first_frame=tlm(leak_latch=[True, True, False]))
        self.addCleanup(hw.close)
        self.assertTrue(wait_for(lambda: hw.read_leak() == "FLOOD"))
        clock.t += 10.0  # the link dies with the water already found
        self.assertEqual(hw.read_leak(), "FLOOD", "a latched flood never decays to UNKNOWN")
        self.assertIn("brainstem", hw.sensor_faults())

    def test_normal_is_earned_not_defaulted(self):
        hw, tr, link = make_real(first_frame=tlm(leak_ok=False))
        self.addCleanup(hw.close)
        self.assertTrue(wait_for(lambda: link.snapshot() is not None))
        self.assertEqual(hw.read_leak(), LEAK_UNKNOWN, "nobody sampling the probes is not a dry hull")

    def test_a_zone_wet_at_boot_cannot_certify_the_hull(self):
        hw, tr, link = make_real(first_frame=tlm(leak_boot=[False, True, False]))
        self.addCleanup(hw.close)
        self.assertTrue(wait_for(lambda: link.snapshot() is not None))
        self.assertEqual(hw.leak_probe_fault(), "mid", "the fault names the seal to suspect")
        self.assertEqual(hw.read_leak(), LEAK_UNKNOWN)

    def test_ballast_is_unknown_until_the_vehicle_has_homed(self):
        hw, tr, link = make_real()
        self.addCleanup(hw.close)
        self.assertTrue(wait_for(lambda: link.snapshot() is not None))
        self.assertIsNone(hw.get_ballast_level())
        self.assertFalse(hw.ballast_homed())
        tr.feed(tlm(seq=2, ballast_ml=settings.ballast_capacity_ml / 2, ballast_homed=True))
        self.assertTrue(wait_for(lambda: hw.get_ballast_level() == 0.5))
        self.assertTrue(hw.ballast_homed())
        tr.feed(tlm(seq=3, ballast_ml=10.0, ballast_homed=True, ballast_fault=True))
        self.assertTrue(wait_for(lambda: hw.ballast_needs_rehome()))

    def test_speed_maps_pulses_and_direction(self):
        hw, tr, link = make_real(first_frame=tlm(speed_hz=10.0, speed_fresh=True, speed_dir=-1))
        self.addCleanup(hw.close)
        self.assertTrue(wait_for(lambda: link.snapshot() is not None))
        speed, fresh = hw.read_water_speed()
        self.assertTrue(fresh)
        self.assertAlmostEqual(speed, 10.0 * nav_settings.m_per_pulse)
        self.assertEqual(hw.read_speed_dir(), -1)
        tr.feed(tlm(seq=2, speed_hz=10.0, speed_fresh=False))
        self.assertTrue(wait_for(lambda: hw.read_water_speed() == (0.0, False)))

    def test_commands_reach_the_wire_in_the_vehicles_vocabulary(self):
        hw, tr, link = make_real()
        self.addCleanup(hw.close)
        self.assertTrue(wait_for(lambda: link.snapshot() is not None))
        hw.set_light_level("white", 0.6)
        hw.set_light("white", True)
        hw.set_light("green", True)  # the interim beacon mapping
        hw.ballast_pump("fill")
        hw.ballast_pump("empty")
        hw.ballast_pump("hold")
        hw.ballast_home()
        self.assertTrue(wait_for(lambda: len(sent_named(tr, "trim_home")) == 1))
        lamps = sent_named(tr, "lamp")
        self.assertEqual(lamps[-1]["value"], 0.6)
        self.assertEqual(sent_named(tr, "beacon")[-1]["value"], 1)
        self.assertEqual([m["value"] for m in sent_named(tr, "pump")], [1, -1, 0])
        # The commanded lamp state is remembered on this side, per contract.
        self.assertEqual(hw.get_light("white"), (True, 0.6))

    def test_dropweight_is_the_two_step_interlock_in_order(self):
        hw, tr, link = make_real()
        self.addCleanup(hw.close)
        self.assertTrue(wait_for(lambda: link.snapshot() is not None))
        with self.assertLogs("neptune.hw", level="WARNING"):
            logging.getLogger("neptune.hw").setLevel(logging.NOTSET)
            hw.release_dropweight()
        names = [m["name"] for m in tr.sent if m["name"] in ("arm_burn", "fire_burn")]
        self.assertEqual(names, ["arm_burn", "fire_burn"], "ARM must precede FIRE, always")

    def test_leak_reset_relays_the_vehicles_verdict(self):
        hw, tr, link = make_real()
        self.addCleanup(hw.close)
        self.assertTrue(wait_for(lambda: link.snapshot() is not None))

        import threading

        def nack():
            wait_for(lambda: len(sent_named(tr, "leak_reset")) == 1)
            tr.feed({"t": "ack", "id": sent_named(tr, "leak_reset")[0]["id"], "ok": False, "err": "aft"})

        threading.Thread(target=nack, daemon=True).start()
        res = hw.reset_leak_latches()
        self.assertFalse(res["ok"])
        self.assertIn("aft", res["why"], "the refusal must name the wet zone")

        def ack_ok():
            wait_for(lambda: len(sent_named(tr, "leak_reset")) == 2)
            tr.feed({"t": "ack", "id": sent_named(tr, "leak_reset")[1]["id"], "ok": True})

        threading.Thread(target=ack_ok, daemon=True).start()
        res = hw.reset_leak_latches()
        self.assertTrue(res["ok"])
        self.assertEqual(res["rearms"], 1)

    def test_leak_reset_with_no_answer_says_so(self):
        hw, tr, link = make_real()
        self.addCleanup(hw.close)
        self.assertTrue(wait_for(lambda: link.snapshot() is not None))
        res = hw.reset_leak_latches()  # nothing acks on this transport
        self.assertFalse(res["ok"])
        self.assertIn("did not answer", res["why"])

    def test_announced_bench_mode_propagates_to_is_mock(self):
        hw, tr, link = make_real(first_frame=tlm(mode="bench"))
        self.addCleanup(hw.close)
        self.assertTrue(wait_for(lambda: hw.is_mock))
        tr.feed(tlm(seq=2, mode="real"))
        self.assertTrue(wait_for(lambda: not hw.is_mock))


class PortDiscoveryTest(unittest.TestCase):
    def test_the_explicit_setting_wins(self):
        object.__setattr__(settings, "brainstem_port", "/dev/ttyTEST")
        try:
            self.assertEqual(find_port(), "/dev/ttyTEST")
        finally:
            object.__setattr__(settings, "brainstem_port", "")

    def test_junk_serial_devices_are_never_mistaken_for_a_brainstem(self):
        # A Mac always has /dev/cu.Bluetooth-Incoming-Port and a debug console;
        # get_hardware()'s auto mode landing on the honest mock depends on none
        # of them matching. On a machine with a real devkit plugged in this
        # returns that port — which is the other correct answer, so the
        # assertion is about what must NEVER match rather than about None.
        port = find_port()
        if port is not None:
            self.assertNotIn("Bluetooth", port)
            self.assertNotIn("debug", port)
            self.assertNotIn("wlan", port)


class ContractConstantsTest(unittest.TestCase):
    def test_the_leak_budget_rate_matches_the_pi_side_derivation(self):
        # tests/test_latency.py derives the debounce budget from RealHardware's
        # SENSOR_HZ / LEAK_SAMPLE_DIVIDER; the firmware samples at LEAK_SAMPLE_HZ.
        # These are one figure written twice, and this is the check that keeps
        # them from drifting apart in silence.
        self.assertEqual(RealHardware.SENSOR_HZ / RealHardware.LEAK_SAMPLE_DIVIDER, LEAK_SAMPLE_HZ)

    def test_every_command_this_side_can_send_is_in_the_shared_vocabulary(self):
        for name in ("pump", "pump_ml", "trim_home", "lamp", "beacon", "arm_burn", "fire_burn", "leak_reset"):
            self.assertIn(name, COMMANDS)


if __name__ == "__main__":
    unittest.main()
