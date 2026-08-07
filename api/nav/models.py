"""Pydantic models for the navigation subsystem (spec §4, §5, §8, §9)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class SensorSample(BaseModel):
    """One tick of raw sensor input (from the sim or real hardware).

    EVERY FIELD THAT COMES OFF A CHIP IS NULLABLE, and None is the loudest answer
    any of them can give: the instrument behind it is not answering — never wired,
    or wired and stopped. The alternative was tried and it is what this round exists
    to undo: nav/sensors.py handed each readback a "cannot-tell default" that was
    itself a measurement, so a dead BNO085 arrived here as heading 0.0 (due north),
    mag_cal 0 ("a compass answered, badly") and gyro 0.0 ("measured: not turning").
    Everything downstream then did exactly what it should with numbers that were
    never measured — the radar is heading-up, so the map swung north and the dead
    reckoner ran the track north, all of it beside a NO COMPASS badge on the same
    screen. A cannot-tell that is also a valid reading is not a cannot-tell.
    """
    t: float                          # seconds since dive start
    # BNO085 fused yaw (0=N, 90=E), or None = NO COMPASS ANSWERING. Not a bearing,
    # and not the last bearing: readers must branch on it. With no heading there is
    # no track (see deadreckoning.py), which is the whole point of carrying the null
    # this far instead of substituting a number here.
    heading_deg: Optional[float]
    # MS5837 — MEASURED, never integrated (§2.4). None = nothing measured it. 0.0 is
    # "at the surface", which is the single depth a descending sub is not at, and it
    # was being written into the permanent dive log as if a sensor had said it.
    depth_m: Optional[float]
    throttle: float                   # -1..1 commanded
    encoder_m: float = 0.0            # tether payout (cumulative) — an UPPER bound (§5.5)
    # IMU mag calibration status 0..3 (§5.6); <2 = suspect. None = NO IMU ANSWERED,
    # which is a different claim from 0 — 0 is "a compass answered, and it says it
    # is uncalibrated", and the two send an operator to do different things
    # (recalibrate, versus go and find out why the IMU is dead). protocol.py was
    # rewritten to preserve exactly that distinction; it must not be flattened here.
    # The default is None and no longer 3: a sample nobody supplied a calibration
    # for has certified nothing, and 3 certified the strongest trust mark there is.
    mag_cal: Optional[int] = None
    # + = nose up / + = starboard down. None = no attitude source answering.
    # (0.0, 0.0) is the measurement "level" — advisory here, but advisory is not a
    # licence to invent, and the hardware layer already answers (None, None).
    pitch_deg: Optional[float] = None
    roll_deg: Optional[float] = None
    # ---- measured motion (paddlewheel + the IMU's own rates) ----------------
    # The paddlewheel is the only instrument aboard that can contradict the speed
    # model, which is the entire reason it was bought: without it a snagged sub
    # keeps "moving" on the map at whatever the LUT says the throttle is worth.
    # It cannot sense direction (the sign comes from the throttle) and it stalls
    # below ~0.1 m/s, so a stopped wheel is genuinely ambiguous — None says so
    # rather than handing the estimator a 0.0 it would treat as a measurement.
    speed_ms_measured: Optional[float] = None   # m/s water-relative; None = stale/not fitted
    # Gyro yaw rate — immune to the thruster magnetic fields that swing the fused
    # compass by tens of degrees at full throttle, which is why the heading filter
    # coasts on this when the magnetometer is not trusted. 0.0 is a REAL reading
    # here ("not turning"); None means NO GYRO IS ANSWERING and the filter has
    # nothing to coast on at all.
    #
    # THIS FIELD USED TO SAY THAT A MISSING IMU "announces itself through mag_cal,
    # not through this field", and defaulted to 0.0. It is the same chip: when the
    # BNO085 stops, the yaw rate stops with it, and 0.0 then reads as "measured, and
    # the sub is running dead straight" — so the heading filter coasted on a rate
    # nothing produced and held a frozen bearing while the track kept advancing
    # along it. The two must go null together.
    gyro_z_dps: Optional[float] = None   # deg/s, + = clockwise (compass convention)
    # Forward linear acceleration. LOGGED AND FILTERED ONLY (§2.2): it feeds the
    # snag detector and the speed KF's predict step and is NEVER integrated twice
    # into position. If you are reading this while adding a position term — don't.
    # None = no accelerometer answering; 0.0 is the measurement "coasting", and the
    # speed KF's predict step treats the two very differently.
    accel_fwd_ms2: Optional[float] = None   # m/s², + = forward
    # ---- what was COMMANDED, alongside what happened ------------------------
    # Without these a dive log cannot calibrate anything. Position over time tells
    # you the sub moved; only throttle-next-to-distance tells you how fast it moves
    # PER UNIT of throttle, which is the number the whole dead-reckoning rests on.
    # Logged from day one so the first dive with real sensors is already usable.
    steer: float = 0.0                # -1..1 commanded (turn rate calibration)
    left: float = 0.0                 # actual thruster output -1..1
    right: float = 0.0
    # 0..1 of the calibrated stroke, or None. The syringe is driven open-loop by a
    # stepper with no position sensor, so from power-on until homing there is no
    # position to report. "Unknown" is a real state and it travels as None; 0.0
    # would read as "empty", which is a specific and possibly dangerous claim.
    ballast_level: Optional[float] = None
    ballast_target: float = 0.0       # 0..1 commanded
    # Raw, before the depth conversion — and None whenever depth_m is None, because
    # depth is arithmetic on this and nothing else. They travel together or the log
    # carries a depth with no provenance. 0.0 psi absolute is not a plausible
    # reading, it is an impossible one, and it was still being logged as data.
    pressure_psi: Optional[float] = None
    armed: bool = False               # a disarmed sample proves nothing about speed


class Origin(BaseModel):
    """Captured atomically (§4.4): lat/lon/accuracy/heading0 in one 'set origin'."""
    lat: float
    lon: float
    accuracy: float = Field(ge=0)     # metres — floor on the whole track's accuracy (§4.2)
    # heading0, from the IMU on the surface (§4.4). None = nothing measured a bearing
    # when the datum was captured, and that is a state worth recording rather than
    # papering over: heading0 is what EVERY track logged from this origin is expressed
    # against, so a fabricated 0.0 (due north) tilts the whole dive permanently, in a
    # file that outlives the dive by years. A dive with no heading0 can still be read;
    # a dive silently rotated to north cannot be un-rotated.
    heading_deg: Optional[float] = None
    source: Literal["phone", "map_tap", "device", "manual"] = "phone"
    t: Optional[float] = None          # capture timestamp (epoch ms), from the client (§2)


class Adjustment(BaseModel):
    """Post-hoc translate+rotate of a track (§4.5). Applied to output, raw log untouched."""
    dx_m: float = 0.0
    dy_m: float = 0.0
    rotation_deg: float = 0.0


class FlowVector(BaseModel):
    """Constant current, entered at launch (§5.4)."""
    bearing_deg: float = 0.0
    speed_ms: float = 0.0


class NavState(BaseModel):
    """Broadcast to the SPA map at broadcast_hz."""
    t: float
    lat: float
    lon: float
    # None = NOTHING MEASURED THE DEPTH this tick. Not the last depth and not 0.0:
    # §2.4 says depth is measured and never integrated, so there is no legitimate
    # way for this to be a number when the pressure sensor has stopped. The map
    # cannot show a depth nothing measured.
    depth_m: Optional[float]
    # None = no heading. The track is then NOT advancing (see no_heading below), so
    # this being null and x_m/y_m standing still are the same fact reported twice.
    heading_deg: Optional[float]
    x_m: float                        # metres east of origin
    y_m: float                        # metres north of origin
    raw_lat: float                    # un-snapped estimate (rendered faint, §5.7)
    raw_lon: float
    snapped: bool
    snap_offset_m: float = 0.0        # raw↔snapped divergence = the drift indicator (§5.7)
    range_m: float                    # straight-line distance from origin
    payout_m: float                   # tether payout bound
    confidence: float = 1.0           # drops when clamped by tether / mag bad / snap far
    mag_cal: Optional[int] = None     # None = no IMU answered; 0 = it did, and says don't trust it
    speed_ms: float = 0.0
    # Where speed_ms came from. An estimate must never dress as a measurement, so
    # the dashboard styles these differently and the replay harness scores them:
    #   "lut"       — speed model from throttle alone; nothing measured the water
    #   "paddle"    — the paddlewheel, direct
    #   "kf-lut"    — speed KF running with the LUT as a weak prior (wheel stale)
    #   "kf-paddle" — speed KF corrected by a fresh paddlewheel measurement
    speed_src: str = "lut"
    # High thrust, sustained, with no measured speed = the sub is pinned on
    # something and the map is marching forward without it. A safety signal in
    # its own right, not an estimator detail — it runs in both filter modes.
    snagged: bool = False
    # The heading filter is deliberately ignoring the compass (bad mag_cal or
    # thrusters running) and coasting on the gyro. The operator must be able to
    # tell "on purpose" from "broken", so this is surfaced rather than inferred —
    # and it can only be true while a gyro is actually answering, because a coast
    # is an integration and there is nothing to integrate otherwise.
    gyro_only: bool = False
    # WITH NO HEADING THERE IS NO TRACK. The position was HELD this tick: x_m/y_m
    # are the last place the sub was tracked to, not where it is now, and the
    # longer this stands the further apart those two are. Its own flag rather than
    # something to infer from heading_deg being null, because "the map has stopped
    # following the sub" is the sentence the operator needs and no amount of
    # missing-value styling on a bearing readout says it. gyro_only says the
    # estimate is degraded on purpose; this says it has stopped.
    no_heading: bool = False
    has_origin: bool = True


class ReadinessItem(BaseModel):
    step: str
    ok: bool
    detail: str = ""


class ReadinessResult(BaseModel):
    passed: bool
    items: list[ReadinessItem]
