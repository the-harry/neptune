"""Estimator selection (spec §4) — two backends behind one update() call.

`NAV_FILTER` picks between them and nothing else does:

  "dr"       (DEFAULT) the dead reckoner — same heading, position, clamp and snap
             logic it has always run — plus the snag detector, which is a safety
             signal and therefore not optional. Its speed is the paddlewheel when the
             wheel is reporting and the LUT when it is not (§3): "untouched in
             behaviour" was never a licence to ignore a sensor the hull now carries.
  "filtered" the same dead reckoner — same position integration, same tether clamp,
             same snapping, same confidence rules — fed a complementary-filtered
             heading and a Kalman-filtered speed instead of the raw compass and the
             raw speed model.

Both expose `update(SensorSample) -> NavState`, so the nav service cannot tell them
apart and no caller has to branch. The default stays "dr" on purpose: promoting the
filter is a decision to be made against real dive data with
`python -m nav.cli replay --filter both`, not a decision to be made by taste. A
filter that has never been scored against a track it did not produce is a guess with
better manners.

The maths lives in filters.py, deliberately away from pydantic and config so it can be
tested with plain numbers. This file is only plumbing: it turns a SensorSample into
scalars, runs the filters, feeds the results into the dead reckoner and carries the
filter's own state (speed_src, gyro_only, snagged) out on the NavState.
"""
from __future__ import annotations

import logging
from typing import Protocol

from .config import settings
from .deadreckoning import DeadReckoner
from .filters import HeadingFilter, SnagDetector, SpeedKF
from .models import FlowVector, NavState, Origin, SensorSample
from .speedlut import DEFAULT_LUT, SpeedLUT

log = logging.getLogger("neptune.nav.estimator")

# What a snag does to confidence (§4c). The position is still being integrated from a
# speed model that believes the sub is moving, so the track is actively wrong while
# this is set — it must not read as a healthy fix.
SNAGGED_CONFIDENCE = 0.4


class Estimator(Protocol):
    """Everything the nav service is allowed to assume about an estimator.

    Kept to the minimum on purpose: an interface the service can rely on is also an
    interface a future backend has to honour, and the shorter it is the cheaper the
    third backend is to write.
    """

    current: FlowVector

    def update(self, s: SensorSample) -> NavState: ...


def _finish(ns: NavState, *, snagged: bool, gyro_only: bool = False,
            speed_src: str | None = None) -> NavState:
    """Stamp the estimator's own state onto the NavState the dead reckoner produced.

    Only things that CANNOT change the track live here — the flags, and the one
    confidence floor a snag forces. Anything that feeds the integration (the filtered
    heading, the filtered speed) goes in through DeadReckoner.update() instead, because
    a value applied to a finished NavState cannot un-integrate the position under it.

    `no_heading` is deliberately NOT set here. It is a fact about whether the position
    was integrated this tick, so it belongs to the code that did or did not integrate
    it; a flag stamped on afterwards could disagree with the x/y underneath it, and
    "the map has stopped following the sub" is not a claim worth letting drift.
    """
    return ns.model_copy(update={
        "snagged": snagged,
        "gyro_only": gyro_only,
        "speed_src": speed_src or ns.speed_src,
        "confidence": round(min(ns.confidence, SNAGGED_CONFIDENCE), 2) if snagged else ns.confidence,
    })


class _EstimatorBase:
    """Shared plumbing for the two wrappers.

    Both of them hold a DeadReckoner rather than subclassing it, which means anything
    that used to reach through `NavService.dr` for a dead-reckoner attribute would now
    silently find nothing. The one that bites is `/api/nav/flow`: it does
    `svc.dr.current = f`, and on a plain wrapper that quietly creates a new attribute
    on the wrapper while the dead reckoner keeps using the old current — an entered
    current that does nothing, with no error to notice. Hence the explicit property,
    and the read-through for everything else (x, y, heading, depth, payout, origin).
    """

    dr: DeadReckoner
    backend: str

    @property
    def current(self) -> FlowVector:
        return self.dr.current

    @current.setter
    def current(self, flow: FlowVector) -> None:
        self.dr.current = flow

    def __getattr__(self, name: str):
        # Only reached when the attribute is not on the wrapper itself, so the wrapper's
        # own state always wins and there is no recursion risk once `dr` is in __dict__.
        try:
            inner = self.__dict__["dr"]
        except KeyError:                      # asked before __init__ finished
            raise AttributeError(name) from None
        return getattr(inner, name)


class DeadReckonEstimator(_EstimatorBase):
    """NAV_FILTER=dr — the default, and behaviourally the estimator that shipped before
    this pass, with exactly one addition: the snag detector.

    The addition is deliberate and is not a filter feature. §4c is a safety signal:
    which estimator backend happens to be configured must not decide whether the
    operator is told the sub is pinned on something. It cannot affect the track — it
    only sets NavState.snagged and pulls confidence down to SNAGGED_CONFIDENCE.

    The speed source is the dead reckoner's own decision (§3): paddlewheel when the
    wheel is reporting, LUT when it is not, labelled either way. This file used to
    force speed_src="lut" here on the grounds that "dr" integrates the MODEL — that
    reasoning was sound and its premise is no longer true, because the dead reckoner
    now integrates the measurement when it has one. Nothing is dressed up: "paddle"
    appears only on the frames a wheel actually measured.
    """

    def __init__(
        self,
        origin: Origin,
        speed_lut: SpeedLUT | None = None,
        current: FlowVector | None = None,
        centreline_lonlat: list[tuple[float, float]] | None = None,
        snapping: bool | None = None,
    ) -> None:
        self.dr = DeadReckoner(origin, speed_lut, current, centreline_lonlat, snapping)
        self.snag = SnagDetector()
        self.backend = "dr"

    def update(self, s: SensorSample) -> NavState:
        ns = self.dr.update(s)
        # Raw paddlewheel only: in this backend there is no filtered speed to consult,
        # and the LUT speed the dead reckoner just used is precisely the number that
        # cannot notice a snag (§4c).
        snagged = self.snag.update(s.t, s.left, s.right, s.speed_ms_measured)
        return _finish(ns, snagged=snagged)


class FilteredEstimator(_EstimatorBase):
    """NAV_FILTER=filtered — the same dead reckoning, with filtered INPUTS (§4a, §4b).

    What changes: heading comes from the complementary filter (gyro-led whenever the
    thrusters are poisoning the magnetometer) and speed comes from the 1-D Kalman
    filter (paddlewheel-corrected when the wheel is turning, weak LUT prior when it is
    not). What does NOT change: the position integration itself, the tether-payout
    clamp (§5.5), centreline snapping (§5.7) and the confidence rules — all of that is
    still the dead reckoner's, untouched, because those rules are about geometry and
    the estimator argument is about sensors.
    """

    def __init__(
        self,
        origin: Origin,
        speed_lut: SpeedLUT | None = None,
        current: FlowVector | None = None,
        centreline_lonlat: list[tuple[float, float]] | None = None,
        snapping: bool | None = None,
    ) -> None:
        self.lut = speed_lut or DEFAULT_LUT          # the real one; the KF falls back to it
        self.dr = DeadReckoner(origin, self.lut, current, centreline_lonlat, snapping)
        self.heading_filter = HeadingFilter()
        self.speed_kf = SpeedKF(settings.m_per_pulse, settings.paddle_window_s)
        self.snag = SnagDetector()
        self.backend = "filtered"
        self._prev_t: float | None = None

    def update(self, s: SensorSample) -> NavState:
        dt = 0.0 if self._prev_t is None else s.t - self._prev_t
        self._prev_t = s.t

        # h is None when the IMU has stopped answering entirely — no fused yaw AND no
        # yaw rate, which is one dead BNO085 and the common case rather than an exotic
        # one. It is passed STRAIGHT THROUGH to the dead reckoner, which holds the
        # position on it. Substituting anything here (the filter's last h, the raw
        # compass, the origin's heading0) would put the invented bearing back one layer
        # down from where it was just removed, and the filtered backend would go on
        # drawing a confident track for a sub nothing can point at.
        h = self.heading_filter.update(s.t, s.heading_deg, s.gyro_z_dps, s.mag_cal, s.left, s.right)
        # The accelerometer is on that same chip, so it goes quiet at the same instant;
        # the KF widens its own uncertainty rather than reading the silence as 0 m/s².
        v = self.speed_kf.update(dt, s.accel_fwd_ms2, s.throttle, s.speed_ms_measured,
                                 self.lut.speed(s.throttle))
        src = self.speed_kf.source

        # Snag evidence: the filtered speed counts ONLY while a fresh paddlewheel
        # measurement is holding it up. When the wheel goes stale the KF is being pulled
        # towards the LUT, and the LUT is a function of throttle — it would report a
        # healthy speed for a sub bolted to a trolley, which is the exact case this is
        # meant to catch. So a stale wheel hands the detector None, not the KF's number.
        evidence = v if src == "kf-paddle" else None
        snagged = self.snag.update(s.t, s.left, s.right, evidence)

        # The filtered speed goes IN to the integration, via the dead reckoner's speed
        # hook. It cannot be stamped on afterwards — a speed applied to a finished
        # NavState cannot un-integrate the position that was built from another one.
        # This used to be done by swapping dr.lut for a fake one-number "LUT"; that
        # became silently wrong the moment the dead reckoner learned to prefer the
        # paddlewheel over its LUT (§3), because the filter's own answer would then have
        # been bypassed on every tick the wheel was turning — which is most of them —
        # while the frame still claimed "kf-paddle".
        ns = self.dr.update(s.model_copy(update={"heading_deg": h}),
                            speed_ms=v, speed_src=src)
        return _finish(ns, snagged=snagged, gyro_only=self.heading_filter.gyro_only, speed_src=src)


def make_estimator(
    origin: Origin,
    speed_lut: SpeedLUT | None = None,
    current: FlowVector | None = None,
    centreline_lonlat: list[tuple[float, float]] | None = None,
    snapping: bool | None = None,
    backend: str | None = None,
) -> Estimator:
    """Build the estimator `NAV_FILTER` asks for. `backend` overrides the setting, which
    is what the replay harness needs to run both over one log.

    Argument order mirrors DeadReckoner's exactly, so this is a drop-in at every call
    site that used to construct one. A purely positional backend-first call —
    make_estimator("filtered", origin, ...) — is also accepted, because that is how the
    brief sketched it and a TypeError there would only surface when a dive is started.
    """
    if isinstance(origin, str):
        backend, origin, speed_lut, current, centreline_lonlat, snapping = (
            origin, speed_lut, current, centreline_lonlat, snapping, backend)
    if not isinstance(origin, Origin):
        # Loud and immediate: the alternative is a wrapper built around junk that only
        # explodes ten minutes later, mid-dive, inside the integration loop.
        raise TypeError(f"make_estimator() needs an Origin, got {type(origin).__name__}")

    name = (backend or settings.filter_backend or "dr").strip().lower()
    if name not in ("dr", "filtered"):
        # A typo in NAV_FILTER must not silently choose an estimator. Fall back to the
        # default and say so — the dive still runs, on the backend that has flown before.
        log.warning("unknown NAV_FILTER=%r — falling back to 'dr'", name)
        name = "dr"

    cls = FilteredEstimator if name == "filtered" else DeadReckonEstimator
    log.info("estimator backend: %s", name)
    return cls(origin, speed_lut, current, centreline_lonlat, snapping)
