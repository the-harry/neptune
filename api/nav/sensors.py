"""Navigation sensor source — sim (default) or real hardware (BNO085 / MS5837 /
paddlewheel / tether encoder, spec §5.1). The service reads SensorSamples from
here at dr_hz.
"""
from __future__ import annotations

import logging
import math

from .config import settings
from .models import SensorSample
from .sim import Simulator

log = logging.getLogger("neptune.nav.sensors")


def _readback(hw, name: str):
    """Call one hardware readback, or report CANNOT-TELL — which is None, always.

    Two things are being defended against and they deserve the same answer. A
    backend that has never heard of this readback is saying "no such instrument",
    which is exactly what HardwareBase's own concrete default says. And a sensor
    that raises mid-dive has stopped being able to answer — substituting the last
    good value would hand the estimator a stale reading dressed as a current one,
    which is the specific failure this subsystem exists to prevent. Neither case
    invents a number, and neither takes the navigation loop down with it.

    THIS FUNCTION USED TO TAKE A `default` AND THAT DEFAULT WAS THE BUG. The
    docstring above already claimed "neither case invents a number" while every
    call site handed it one to invent: 0.0 for heading (due north), the surface
    pressure for depth (the surface), 0 for mag_cal ("a compass answered, badly").
    The guarding was right and is kept; the substitution was not. There is now
    exactly one value for cannot-tell here and it is not a value any instrument
    can return, so nothing downstream can mistake it for a reading. A caller that
    genuinely needs a number in place of the null has to write that conversion out
    where a reader can see it and argue with it.
    """
    fn = getattr(hw, name, None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception:  # noqa: BLE001
        log.debug("hardware readback %s() failed; reporting cannot-tell", name, exc_info=True)
        return None


def _num(value) -> float | None:
    """Read a value as a number, or report CANNOT-TELL.

    A backend handing back something that is not a number is a bug in that
    backend, but the navigation loop is not the place to discover it by dying —
    the sub is in the water while this runs. So the guard stays; what has gone is
    the `default` it used to fall back to, because that parameter is how a None
    from a dead chip got laundered into a plausible reading one line after the
    hardware layer had carefully said it could not measure.

    NaN and infinity are cannot-tell too. They are floats, so the old coercion let
    them through, and a NaN reaching the integration poisons x/y permanently — a
    track that can never be recovered by the sensor coming back, which is worse
    than the blank it should have been.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _round_or_none(value: float | None, nd: int) -> float | None:
    """round(), for a value that is allowed to be cannot-tell. round(None) raises,
    and the one-liner that avoids it (`round(value or 0.0)`) is the laundering this
    module is being fixed for."""
    return None if value is None else round(value, nd)


def _commanded(value) -> float:
    """A COMMANDED channel as a number — never None.

    The one honest exception to the rule above, and it is narrow: throttle, steer
    and the thruster outputs are things this software ASKED FOR, not things it
    measured. "Nothing has been commanded" is 0.0 and always was; there is no
    unknown state to represent, so there is no null to carry. Anything that came
    off a chip goes through _num() instead and keeps its None.
    """
    v = _num(value)
    return 0.0 if v is None else v


def _hw_sample_fields(rov, hw) -> dict:
    """Every SensorSample field that comes off the vehicle, in one place.

    Both live sources build their samples from this, so the data SHAPE cannot
    drift between "vehicle" and "real" — or between a mock backend and a wired
    one. That matters more than the small duplication it saves: an estimator
    tuned against a stream that quietly loses a field on the real hardware is a
    bug that only shows up in the water.
    """
    # A compass that is not answering has NO BEARING, and 0.0 is a bearing: it is
    # due north. That substitution is what defeated the whole liveness change — the
    # hardware layer said None, this line turned it into north, and because the
    # radar is heading-up the map swung north and the dead reckoner ran the track
    # north, beside a NO COMPASS badge on the same screen. The modulo only applies
    # to a number that exists.
    heading = _num(_readback(hw, "read_heading"))
    if heading is not None:
        heading = heading % 360.0
    # Depth is arithmetic on pressure and inherits its silence. Defaulting the
    # pressure to the surface reading put the sub at exactly 0.00 m — the one depth
    # a descending sub is definitely not at — and that 0.0 went into the permanent
    # dive log as a measurement.
    surface_psi = settings_surface_psi()
    pressure = _num(_readback(hw, "read_pressure"))
    depth = (None if pressure is None
             else max(0.0, (pressure - surface_psi) / settings_psi_per_m()))

    # Use the ACTUAL thruster output, not the commanded throttle.
    #
    # Heading comes from the hardware, which only turns when the thrusters really
    # run - so it is zero while disarmed or in failsafe. Taking forward speed from
    # the *commanded* throttle instead produced the exact reported symptom: the sub
    # advanced but never turned, i.e. "I can only go straight". Averaging left/right
    # keeps both quantities on the same footing: disarmed means neither.
    #
    # _commanded, not _num: these are outputs this software asked for, so 0.0 is a
    # real value ("nothing commanded") and not a stand-in for an unknown.
    left = _commanded(getattr(rov, "left", 0.0))
    right = _commanded(getattr(rov, "right", 0.0))
    throttle = (left + right) / 2.0

    # Magnetometer calibration, straight from the IMU. This used to be hard-coded
    # to 3, which had a hardware layer with no compass at all certifying its
    # heading as perfectly calibrated — and mag_cal is what the pre-dive check and
    # the heading filter's trust gate both read. Then it was defaulted to 0, which
    # is better and still wrong: 0 is a READING, "a compass answered and says it is
    # uncalibrated", so a dead IMU still got to make a confident statement about a
    # chip that was not talking. None is the only answer that is not a claim.
    mag_cal = _num(_readback(hw, "read_mag_cal"))
    mag_cal = None if mag_cal is None else int(mag_cal)

    # (None, None) from the base class, and either element may be null on its own.
    # (0.0, 0.0) is the measurement "level" — attitude is advisory, but advisory is
    # not a licence to invent an attitude for a hull nothing is sensing.
    pr = _readback(hw, "read_pitch_roll")
    try:
        pitch, roll = _num(pr[0]), _num(pr[1])
    except Exception:  # noqa: BLE001 — not a pair; the backend is broken, not the sub
        pitch, roll = None, None

    # The paddlewheel is directionless: only its MAGNITUDE is a measurement and the
    # sign belongs to the throttle, which the estimator applies itself (§4b). Pass
    # a signed value through here and the sign gets applied twice. fresh=False means
    # stale, stalled or not fitted — the magnitude is then meaningless, so it
    # travels as None rather than as a 0.0 that reads like "measured: stopped".
    # A readback that is missing or raised is the same claim: nothing measured the
    # water this tick.
    ws = _readback(hw, "read_water_speed")
    try:
        speed_ms, fresh = _num(ws[0]), bool(ws[1])
    except Exception:  # noqa: BLE001
        speed_ms, fresh = None, False

    # 0..1 of the calibrated stroke, or None for "the stepper has never been homed".
    # Carried through untouched: rounding None to 0.0 would turn "I do not know
    # where the syringe is" into "the syringe is empty", which is a specific claim
    # about buoyancy that an operator would dive on.
    level = _num(_readback(hw, "get_ballast_level"))

    return {
        "heading_deg": None if heading is None else round(heading, 2),
        "depth_m": None if depth is None else round(depth, 3),
        "throttle": round(throttle, 3),
        "mag_cal": mag_cal,
        "pitch_deg": None if pitch is None else round(pitch, 2),
        "roll_deg": None if roll is None else round(roll, 2),
        "speed_ms_measured": round(abs(speed_ms), 3) if (fresh and speed_ms is not None) else None,
        # 0.0 deg/s is "measured: not turning" and None is "no gyro is answering".
        # They used to be the same value here, and the heading filter coasts on this
        # — so a dead IMU handed it a rate of zero, which it integrated into a
        # perfectly steady bearing while the sub turned underneath it.
        "gyro_z_dps": _round_or_none(_num(_readback(hw, "read_gyro_z_dps")), 3),
        "accel_fwd_ms2": _round_or_none(_num(_readback(hw, "read_accel_fwd_ms2")), 3),
        # The control channels ride along so the dive log can be calibrated
        # against later (see nav.calibrate). They cost nothing to carry.
        "steer": round(_commanded(getattr(rov, "steer", 0.0)), 3),
        "left": round(left, 3),
        "right": round(right, 3),
        "ballast_level": None if level is None else round(level, 3),
        # rov.ballast_target is seeded from get_ballast_level(), so it is None until
        # the stepper has been homed — carried as 0.0 only because "commanded" has
        # no unknown state: nothing has been commanded yet.
        "ballast_target": round(_commanded(getattr(rov, "ballast_target", 0.0)), 3),
        "pressure_psi": None if pressure is None else round(pressure, 2),
        "armed": bool(getattr(rov, "armed", False)),
    }


class SensorSource:
    is_sim = False
    def read(self, dt: float) -> SensorSample | None: ...
    def reset(self) -> None: ...


class SimSensorSource(SensorSource):
    is_sim = True

    def __init__(self) -> None:
        self._clock = 0.0
        self._sim = Simulator(hold_at_end=True)   # never stops; flies the last leg
        log.info("nav sensors: SIMULATOR (scripted path, drift + mag + current)")

    def read(self, dt: float) -> SensorSample | None:
        self._clock += dt
        s = self._sim.step(dt)
        if s is not None:
            s.t = round(self._clock, 3)           # continuous clock across the run
        return s

    def truth_row(self) -> dict:
        """Ground truth behind the sample just read (§4e).

        Only the simulator can offer this, and only it should: a harness that
        scores an estimator needs a truth the estimator never saw. Kept as an
        accessor rather than smuggled into SensorSample, because a truth field on
        the sample would eventually be read by something that is not a test.
        """
        return self._sim.truth_row()

    def reset(self) -> None:
        self._clock = 0.0
        self._sim = Simulator(hold_at_end=True)


class RealSensorSource(SensorSource):
    """Sensors read straight off the hardware layer's readbacks (§5.1).

    THE HANDLE PROBLEM: there is exactly one hardware object in the process —
    main.py builds it and hands it to RovState — and calling get_hardware() again
    here would produce a second one. On the Pi that is two owners of the same GPIO
    pins and I2C bus; on the bench it is two independent mock vehicles quietly
    disagreeing about where the sub is and what it is doing. So this source
    reaches the hardware exactly the way VehicleSensorSource does, through the
    get_rov callable, and refuses to construct without one rather than reporting a
    stream of plausible zeros nobody measured.

    Resolution is LAZY on purpose: main.py builds the nav service at import time,
    before app.state.rov exists, so get_rov() returns None for the first ticks.
    No vehicle means no sample — silence, not a fabricated one.
    """

    def __init__(self, get_rov=None) -> None:
        if get_rov is None:
            raise RuntimeError(
                "RealSensorSource: no vehicle to read the hardware through "
                "(nav is running standalone). Bind a get_rov callable, or use "
                "NAV_SENSORS=sim for a source that admits it is simulated."
            )
        self._get_rov = get_rov
        self._t = 0.0
        log.info("nav sensors: REAL (hardware readbacks — IMU, depth, paddlewheel, spool)")

    @property
    def is_sim(self) -> bool:
        # "real" names the SOURCE, not the truth of it. Point this at MockHardware
        # and every reading is still invented, so the dashboard has to keep saying
        # so — dropping the SIM badge is how a bench number becomes a dive number.
        rov = self._get_rov()
        hw = getattr(rov, "hw", None)
        return bool(getattr(hw, "is_mock", True))

    def read(self, dt: float) -> SensorSample | None:
        rov = self._get_rov()
        hw = getattr(rov, "hw", None)
        if rov is None or hw is None:
            return None
        self._t += dt
        # Tether payout comes from the spool encoder and nowhere else. THE ONE
        # READBACK WHOSE CANNOT-TELL IS ALREADY 0.0 AND MAY STAY THERE: this is an
        # UPPER BOUND on range (§5.5), not a position, and the clamp explicitly
        # treats a zero bound as absent (`if s.encoder_m > 0`). So a silent spool
        # LOOSENS the clamp rather than dragging the track home — the null and the
        # zero produce the same behaviour, and no reading downstream is a claim
        # about where the sub is. The conversion is written out here rather than
        # hidden in a helper default, so the exception is visible and arguable.
        payout = _num(_readback(hw, "read_payout_m"))
        payout = 0.0 if payout is None else max(0.0, payout)
        return SensorSample(t=round(self._t, 3),
                            encoder_m=round(payout, 3),
                            **_hw_sample_fields(rov, hw))

    def reset(self) -> None:
        self._t = 0.0


class VehicleSensorSource(SensorSource):
    """Sensors read from the LIVE vehicle instead of a script.

    Whatever the hardware layer can actually measure, this reports; whatever it
    cannot, it reports as cannot-tell. The best available truth about where the
    sub is pointing and how hard it is being driven is the ROV control plane
    itself - heading from the hardware layer, depth from the pressure reading,
    throttle from what the operator is actually commanding.

    This exists because the alternative was worse than useless: NAV_SENSORS
    defaulted to the scripted simulator, so the map traced a canned route with
    preset heading legs and IGNORED the operator entirely. Steering the vehicle
    changed nothing on the map, which reads exactly as "I can only go straight".

    is_sim stays True while the underlying hardware is mocked, so the dashboard
    keeps flagging it honestly rather than presenting a simulation as ground truth.
    """

    def __init__(self, get_rov):
        self._get_rov = get_rov
        self._t = 0.0
        self._payout = 0.0
        log.info("nav sensors: VEHICLE (heading/depth/throttle from the live ROV state)")

    @property
    def is_sim(self) -> bool:
        rov = self._get_rov()
        hw = getattr(rov, "hw", None)
        return bool(getattr(hw, "is_mock", True))

    def read(self, dt: float):
        rov = self._get_rov()
        hw = getattr(rov, "hw", None)
        if rov is None or hw is None:
            return None
        self._t += dt
        fields = _hw_sample_fields(rov, hw)
        # Tether payout: the spool encoder when the vehicle has one. It is the only
        # thing here that actually measures the bound, so it wins whenever it
        # answers with a length; 0.0 (or a silent encoder — see RealSensorSource for
        # why the two are the same claim here) means "no encoder / nothing paid
        # out", never "range zero".
        spool_m = _num(_readback(hw, "read_payout_m")) or 0.0
        # No spool encoder yet. Payout is used only as an UPPER BOUND on range, so
        # integrating commanded speed over time is a safe over-estimate: it can only
        # loosen the clamp, never fake precision the vehicle does not have.
        #
        # The over-estimate keeps running even while an encoder is answering, so an
        # encoder that dies mid-dive falls back to a loose bound instead of a bound
        # that is suddenly, wrongly tight.
        self._payout += abs(fields["throttle"]) * dt * 1.2
        payout = spool_m if spool_m > 0.0 else self._payout
        return SensorSample(t=round(self._t, 3), encoder_m=round(payout, 3), **fields)

    def reset(self) -> None:
        self._t = 0.0
        self._payout = 0.0


def settings_surface_psi() -> float:
    try:
        from config import settings as rov_settings
        return float(rov_settings.surface_pressure_psi)
    except Exception:  # noqa: BLE001
        return 14.7


def settings_psi_per_m() -> float:
    try:
        from config import settings as rov_settings
        return float(rov_settings.psi_per_meter) or 1.42
    except Exception:  # noqa: BLE001
        return 1.42


def get_sensor_source(get_rov=None) -> SensorSource:
    choice = settings.sensor_backend.lower()
    if choice == "sim":
        return SimSensorSource()                 # scripted demo path, ignores the operator
    if choice == "real":
        try:
            return RealSensorSource(get_rov)
        except Exception as exc:  # noqa: BLE001
            log.warning("RealSensorSource init failed (%s); using SIM", exc)
            return SimSensorSource()
    # default ("vehicle"/"auto"): follow the actual vehicle when one is bound,
    # falling back to the scripted sim only when nav runs standalone.
    if get_rov is not None:
        return VehicleSensorSource(get_rov)
    return SimSensorSource()
