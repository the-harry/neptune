"""WebSocket message contract (Pydantic) — the single source of truth for what
the client and server send each other over /ws/control.

Inbound (client -> server) is a discriminated union on `type`; parse with
`parse_inbound()`, which returns None (and logs) on anything malformed so a bad
frame can never crash the socket. Outbound (server -> client) models are dumped
to dicts by the app.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

log = logging.getLogger("neptune.proto")

# ---- inbound: client -> server -------------------------------------------


class ControlMsg(BaseModel):
    type: Literal["control"]
    throttle: float = 0.0  # -1..1 (clamped on apply)
    steer: float = 0.0  # -1..1


class CameraMsg(BaseModel):
    type: Literal["camera"]
    pan: float = 0.0  # -1..1
    tilt: float = 0.0  # -1..1


class BallastMsg(BaseModel):
    type: Literal["ballast"]
    cmd: Literal["fill", "empty", "hold"]


class CommandMsg(BaseModel):
    type: Literal["command"]
    name: str
    # arm|disarm|stop|surface|ballast_home carry no value; magnet/light_* carry
    # bool; *_level carry float; dropweight carries "release".
    value: Optional[Union[bool, float, str]] = None
    c_id: Optional[str] = None  # correlation id (§3) — carried through every stage, echoed in the ack


class PingMsg(BaseModel):
    type: Literal["ping"]
    t1: Optional[float] = None  # client monotonic ms at send (§2 SNTP) — echoed back in the pong


Inbound = Annotated[
    Union[ControlMsg, CameraMsg, BallastMsg, CommandMsg, PingMsg],
    Field(discriminator="type"),
]
_inbound_adapter: TypeAdapter = TypeAdapter(Inbound)

# Command names the server understands (anything else is logged + ignored).
COMMAND_NAMES = frozenset(
    {
        "arm",
        "disarm",
        "stop",
        "surface",
        "magnet",
        "ballast_home",
        "light_green",
        "light_white",
        "light_green_level",
        "light_white_level",
        "dropweight",
        # RE-ARM THE LEAK DETECTOR. A command and not an endpoint, so it goes through
        # the same recv/validate/apply/ack lifecycle everything else does and lands in
        # the blackbox with a c_id — dismissing the vehicle's strongest claim is
        # exactly the kind of thing that has to be findable in the log afterwards.
        # The hardware layer refuses it outright while a probe is wet.
        "leak_reset",
    }
)


def parse_inbound(raw: str | bytes):
    """Validate a raw WS frame → a message model, or None if malformed."""
    try:
        return _inbound_adapter.validate_json(raw)
    except ValidationError as exc:
        log.debug("dropped malformed frame: %s", exc.errors()[:1])
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("dropped unparseable frame: %s", exc)
        return None


# ---- outbound: server -> client ------------------------------------------


class Telemetry(BaseModel):
    type: Literal["telemetry"] = "telemetry"
    seq: Optional[int] = None  # monotonically increasing frame number (§4 — client gap detection)
    t: Optional[float] = None  # Pi monotonic ms at send (§2/§4 — staleness/max_age)
    armed: bool
    left: float
    right: float
    # 0..1 of the calibrated stepper stroke, or None = NOT HOMED, position
    # genuinely unknown. The syringe axis is open-loop: until homing zeroes the
    # step counter there is no number to send, and sending 0.0 would tell the
    # operator the ballast is empty — a claim the vehicle cannot support.
    ballast_level: Optional[float] = None
    ballast_target: float
    # --- readings whose sensor can STOP ---------------------------------------
    # null on any of these four means THE SENSOR BEHIND IT IS NOT ANSWERING RIGHT
    # NOW. That is a different and far more urgent claim than "this frame is
    # stale": a stale frame is a whole snapshot that arrived late and every number
    # in it was real when it was taken, whereas this is one instrument that has
    # stopped while everything around it keeps updating. The frame is current; the
    # number does not exist.
    #
    # These were plain floats, and that is the hole two review rounds fell into. A
    # required float leaves a dead sensor nowhere to say so, so the hardware layer
    # served its last cached value instead: an MS5837 that died at 4.33 m shipped
    # depth=4.33 in every frame at 15 Hz while the sub descended to 8, and the
    # client — which stamps freshness on arrival — read it as fresh and painted it
    # with a full depth-band tint. There was no lie in the transport and none in
    # the client; the contract simply had no way to spell "I cannot tell".
    #
    # depth and pressure are the same instrument (depth is arithmetic on
    # pressure), so they are null together, always. heading_card is a restatement
    # of heading and cannot outlive it — a confident "NW" beside a blank bearing
    # is read as the bearing.
    depth: Optional[float] = None
    pressure: Optional[float] = None
    heading: Optional[float] = None
    heading_card: Optional[str] = None
    magnet: bool
    light_green: bool
    light_white: bool
    light_green_level: float
    light_white_level: float
    # The old single-bit alarm, and it is NOT simply "WARN or FLOOD" any more: it is
    # "not certified dry". The one value it may never take is the reassuring one on
    # evidence nobody collected — a client that knows only this bool must not be
    # told the hull is dry by a probe that was not read. Over-warning costs a
    # cancelled dive; under-warning costs the sub.
    leak: bool
    # "NORMAL" | "WARN" | "FLOOD" | "UNKNOWN" — the stage the glyph shapes on.
    #
    # UNKNOWN IS THE CANNOT-TELL, SPELLED OUT, because this field has no null to
    # spend: it is a required string and the alarm beside it is a bare bool, so the
    # absence had to be given a name of its own rather than a value borrowed from
    # the three real stages. The leak probes are two wires and a pin — there is no
    # chip to stop answering — and they were sampled inside the same try-block as
    # the I2C ticks, so one raise from a bus chip stopped the sampling entirely and
    # read_leak() went on answering NORMAL at full telemetry rate. Every other gauge
    # correctly blanked and named its chip; the hull-integrity readout, the one that
    # decides whether a dive is recoverable, stayed green on nothing.
    #
    # WET OUTRANKS CANNOT-TELL and the asymmetry is deliberate: water that has
    # reached a probe is an established fact and the sampler dying afterwards does
    # not un-establish it, so FLOOD and WARN never decay to UNKNOWN. Only the
    # REASSURANCE needs liveness. api/hardware.py's leak_state_from() is the single
    # place that verdict is computed.
    leak_state: str
    # 2S pack: 8.4 full / 7.0 warn / 6.6 critical / 6.0 floor. None = THE INA219 IS
    # NOT ANSWERING — same claim as the four readings above, same chip that carries
    # current_a, so the two go null together.
    #
    # This was the last required float on a reading that can stop, and it was
    # required for a stated reason: `battery_band()` in main.py compared it with
    # `<` on the control loop's own path, so a null would have raised inside the
    # journal and taken telemetry, the watchdog and the blackbox down together.
    # rov.py therefore substituted 0.0 — and 0.0 volts is not a neutral filler, it
    # is the most alarming reading the gauge can show. A dead pack monitor painted
    # a red "BATTERY 0.0V · SURFACE" on a vehicle with a full battery: a critical
    # alarm invented entirely by an absent sensor, which is the same lie as a
    # frozen depth pointing the other way. An operator who aborts a dive on it
    # learns to distrust the one alarm that must never be distrusted.
    #
    # battery_band(None) now answers "unknown" and cannot raise, so the null has
    # somewhere honest to land and nothing on the loop's path trips over it.
    battery_v: Optional[float] = None
    signal: int
    link_ms: Optional[int] = None
    # --- ballast truth (open-loop stepper, no position sensor) ----------------
    # homed=False means the counter has never been zeroed against the EMPTY limit
    # switch, so ballast_level above is None. needs_rehome=True means the FULL
    # switch closed at a count that disagrees with the configured span by more
    # than the tolerance: steps were skipped, the count is lying, and the level
    # must not be believed until it is homed again. Skipped steps are surfaced,
    # never swallowed — a silently-wrong syringe is how a sub gets left on the bottom.
    ballast_homed: bool = False
    ballast_needs_rehome: bool = False
    # --- motion truth (paddlewheel / estimator) -------------------------------
    # speed_ms is None when nothing could measure or estimate speed. speed_src
    # names where it came from ("lut" | "paddle" | "kf-lut" | "kf-paddle") so the
    # dashboard can style an estimate differently from a measurement — an estimate
    # never dresses as a measurement. snagged = high thrust, sustained, no speed:
    # the sub is pinned and the map is running away from it.
    speed_ms: Optional[float] = None
    speed_src: Optional[str] = None
    # snagged and gyro_only ARE THREE-VALUED, and the third value is the whole
    # point: False means "navigation looked and says no", None means "navigation
    # cannot say" — not started, no origin, between dives, sensor bus down, loop
    # dead. They were required bools while main.py had already begun assigning
    # None to both, so the server was emitting frames its own contract rejects
    # (Telemetry.model_validate_json on a live frame: 2 validation errors). A
    # contract the server itself violates is worse than no contract — every
    # consumer that trusts it is trusting a document the producer ignores.
    #
    # The two-valued version could not be fixed by choosing a better default,
    # because neither default is neutral: False on both is the pair of REASSURING
    # answers, so navigation dying made the console quieter — a standing snag
    # warning cleared itself and the GYRO badge went out at the exact instant
    # nothing was left to watch them. A subsystem's death must never look like
    # good news.
    snagged: Optional[bool] = None
    # The heading filter is ignoring the compass ON PURPOSE (bad calibration or
    # thrusters polluting the magnetometer) and coasting on the gyro. Shown
    # distinctly from a fault: deliberate is not broken. It qualifies a heading, so
    # it cannot outlive one — main.py nulls it whenever the frame carries no
    # bearing, for the same reason cardinal(None) is None.
    gyro_only: Optional[bool] = None
    # BNO085 magnetometer calibration 0..3; <2 = heading suspect everywhere it is
    # shown. None = NO IMU IS ANSWERING — never wired, or wired and stopped —
    # which is a different thing from 0. 0 is a reading, and a confident one:
    # "a compass answered, and it says it is uncalibrated". They send an operator
    # to do different things. Until this round the hardware layer never produced
    # the null (both backends returned a literal 0 when they had nothing), so this
    # field was Optional in name only and the client's NO COMPASS flag was
    # unreachable on every real hull. Worse, a BNO085 that died mid-dive froze
    # this at 3 — so a frozen bearing shipped wearing the strongest trust mark the
    # system has. If heading is null this must be null too: they are one chip.
    mag_cal: Optional[int] = None
    # --- the rest of what the BNO085 measures ---------------------------------
    # THESE FOUR EXISTED AND NEVER LEFT THE VEHICLE. api/hardware.py has measured
    # all of them since the IMU was wired (read_gyro_z_dps / read_accel_fwd_ms2 /
    # read_pitch_roll) and api/nav/models.py carries them on SensorSample, where
    # the heading filter and the speed KF read them — but SensorSample never goes
    # topside. It is an internal ingest record: it reaches the dive journal
    # (nav/divelog.py) and the replay harness and stops there. So the console
    # could not have shown a turn rate or an attitude if it had wanted to; there
    # was no field to read. A measurement the vehicle takes, logs, and refuses to
    # transmit is a measurement the operator does not have.
    #
    # THEY ARE ON THIS FRAME AND NOT ON NavState, DELIBERATELY, for three reasons
    # that all point the same way:
    #
    #   1. They are READINGS, not estimates. /ws/nav carries what the estimator
    #      concluded; these are what it was fed. rov.py takes them off the same
    #      hardware handle it takes heading and mag_cal off, so they arrive with
    #      exactly the authority those two have and no more.
    #   2. NavState does not exist before a dive does. The estimator is built in
    #      NavService.start_dive, which needs an origin, so on every healthy boot
    #      there is no NavState at all until somebody sets a datum — and the nav
    #      freshness gate (NavService.fresh_state) would then blank an attitude
    #      the IMU is reporting perfectly well, for a reason that has nothing to
    #      do with the IMU. "No origin yet" and "the chip is dead" would arrive as
    #      one indistinguishable null, which is the failure this project keeps
    #      undoing. Nothing here goes through that gate; it does not apply.
    #   3. A fact carried on both sockets is a fact that can disagree with itself.
    #      tests/test_consumers.py TwoSocketsOneVehicleTest exists because
    #      gyro_only was null on one socket and False on the other in the same
    #      tick. Adding four more shared facts would be four more of those.
    #
    # ALL SIX BNO085 FIELDS GO NULL TOGETHER — heading, mag_cal, and these four —
    # because they are one chip and the hardware layer gates them on one liveness
    # answer (`self._answering("bno085")`). null here is NOT a neutral zero:
    #   * 0.0 deg/s is the measurement "the sub is not turning", which is the
    #     single most reassuring thing a turn-rate readout can say, and it is what
    #     a dead gyro used to say (nav/models.py records what that cost the
    #     heading filter: it coasted on a rate nothing produced).
    #   * 0.0 m/s² is "coasting".
    #   * (0.0, 0.0) is "level" — the attitude of a hull that is not rolling over.
    # Every one of those is the calm answer, so a default would make an IMU dying
    # look like a vehicle behaving itself. There is no default; there is None.
    #
    # pitch and roll are independently nullable because read_pitch_roll returns a
    # pair and the base class says either element may be absent on its own; in
    # practice they arrive and leave together with the chip.
    gyro_z_dps: Optional[float] = None  # yaw rate, deg/s, + = clockwise (compass convention)
    accel_fwd_ms2: Optional[float] = None  # forward linear acceleration, m/s², + = ahead
    pitch_deg: Optional[float] = None  # + = nose up
    roll_deg: Optional[float] = None  # + = starboard down
    # Pack current from the INA219 — free from the same chip as the voltage, and
    # the number the power budget is written against. None = no current sense.
    #
    # THE WIRE SHAPE IS ALREADY RIGHT FOR A READOUT OF ITS OWN and needs nothing
    # done to it: Optional[float], amps, rounded to 0.01 A, null exactly when the
    # INA219 is silent — which is exactly when battery_v is null, because it is
    # one chip and "ina219" rides in sensor_faults naming it. Any tile the console
    # builds on this inherits the pack's cannot-tell behaviour for free and cannot
    # drift from it. Its only defect was topside: client/js/render.js spends it
    # inside the pack tooltip ("drawing 3.1 A") and nowhere else, so the one number
    # that turns "the pack is sagging" into "the pack is sagging BECAUSE both
    # thrusters are at full" is invisible to an operator who is not hovering a
    # tooltip with wet hands. That is a console fix, not a contract fix.
    current_a: Optional[float] = None
    # Which leak probe reads open or shorted: "warn" | "flood" | "warn+flood".
    # None = both probes look sane. A dead probe reads dry forever, which is the
    # one failure the two-probe design would otherwise hide completely, so it is
    # reported rather than trusted.
    leak_probe_fault: Optional[str] = None
    # HOW MANY TIMES THE LEAK DETECTOR HAS BEEN RE-ARMED BY HAND this run. 0 for
    # the whole of a normal dive. Non-zero says the NORMAL on screen is a
    # reassurance an operator RESTORED after a latch, not one that was never in
    # doubt — the same reading arrived at two different ways, and the console is
    # entitled to say which. Never used to suppress or soften an alarm: a re-arm
    # is refused outright while a probe is wet, so this can only ever describe
    # history, never the present.
    leak_rearms: int = 0
    # WHICH parts are not answering, in the vehicle's own vocabulary — ["ms5837"],
    # ["bno085", "ina219"], the same names as docs/hardware.md and the wiring
    # diagram, so the console names the part a human will go and unplug. Mostly
    # I2C chip designations; the hardware layer may also name a thing that is not
    # a chip but can still stop (the leak probes are GPIO, the sensor thread is
    # software). This end does not interpret them — it renders whatever the
    # vehicle calls the part, because the vehicle is the only layer that knows.
    #
    # A blank gauge the operator cannot explain is only half the fix. Blanking
    # depth says "do not believe 4.3 m"; it does not say whether the sensor died,
    # the bus died, or the vehicle is doing something clever. This turns the null
    # into a sentence: DEPTH — MS5837 NOT ANSWERING. It is the same verdict the
    # nulls above are computed from, read a second time, so the two can never
    # contradict each other on screen.
    #
    # Empty = nothing is currently named as faulted. It is NOT a certificate of
    # health: a backend that cannot track liveness reports empty, and the nulls on
    # the individual readings remain the authoritative claim.
    sensor_faults: list[str] = Field(default_factory=list)
    # --- Pi system health (REAL readings; see api/sysinfo.py) ------------------
    # All Optional on purpose: None means "could not read this probe" and renders
    # as "--" topside. A real 0 (e.g. an idle CPU) stays a 0 and is never faked.
    cpu_c: Optional[float] = None
    cpu_pct: Optional[float] = None
    ram_pct: Optional[float] = None
    disk_gb: Optional[float] = None
    uptime_s: Optional[float] = None
    net_tether_up: Optional[bool] = None
    net_tether_mbps: Optional[int] = None
    net_cam_up: Optional[bool] = None
    net_cam_signal: Optional[float] = None
    # True only when the VEHICLE hardware is simulated. Pi metrics above are
    # always real regardless of this flag.
    mock: bool


class Alarm(BaseModel):
    type: Literal["alarm"] = "alarm"
    name: str


class Pong(BaseModel):
    type: Literal["pong"] = "pong"
    t1: Optional[float] = None  # echoed client send time
    t2: Optional[float] = None  # Pi monotonic ms at receive  (§2 SNTP)
    t3: Optional[float] = None  # Pi monotonic ms at send


class Ack(BaseModel):
    """Command acknowledgement (§3) — closes the correlation loop back to the client."""

    type: Literal["ack"] = "ack"
    c_id: Optional[str] = None
    name: str
    ok: bool
    reason: Optional[str] = None
