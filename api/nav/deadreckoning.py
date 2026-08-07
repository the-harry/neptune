"""Dead reckoning (spec §5.2) — heading + speed, integrated ONCE.

NEVER double-integrates acceleration (§2.2). Depth is taken from the sensor,
never integrated (§2.4). Error is linear in distance travelled (~5-15%). Applies
current compensation (§5.4), the tether-payout bound (§5.5), and centreline
snapping (§5.7); confidence drops when clamped, mag is bad, or raw↔snapped diverge.

SPEED SOURCE (§3): the paddlewheel wins over the LUT whenever it is reporting, and
the frame is labelled with which one was integrated. See update() for why that is
true here, in the DEFAULT backend, and not only in the filtered one.

WITH NO HEADING THERE IS NO TRACK. A speed is a scalar; a position needs a
direction to put it on. When the compass is not answering, integrating the speed
along ANY bearing — the last one, the origin's, or north — synthesises a position
nothing measured, which is the one thing this project refuses ("the map moves on
the SUB's output or it does not move at all"). So the position is HELD, confidence
drops to the floor, and NavState.no_heading says why. A held track is visibly
stale; a moving one is invisibly false, and the operator drives it.
"""
from __future__ import annotations

import math

from .config import settings
from .filters import _sign
from .geo import to_latlon, to_local
from .models import FlowVector, NavState, Origin, SensorSample
from .snap import nearest_on_polyline
from .speedlut import DEFAULT_LUT, SpeedLUT

# What a held track is worth. Lower than the snag floor (0.4) on purpose: a snagged
# sub is still being TRACKED — the estimator knows where it thinks the sub is and is
# telling you that number is running away from it. With no heading the estimator has
# stopped tracking altogether, so x/y are a timestamp of the last fix and their error
# grows with every second the sub keeps moving. Not zero, because the last fix is
# still the best place to start looking.
NO_HEADING_CONFIDENCE = 0.1


class DeadReckoner:
    def __init__(
        self,
        origin: Origin,
        speed_lut: SpeedLUT | None = None,
        current: FlowVector | None = None,
        centreline_lonlat: list[tuple[float, float]] | None = None,  # GeoJSON [lon,lat]
        snapping: bool | None = None,
    ):
        self.origin = origin
        self.lut = speed_lut or DEFAULT_LUT
        self.current = current or FlowVector()
        self.snapping = (settings.snapping_enabled if snapping is None else snapping) and bool(centreline_lonlat)
        # centreline → local metres, once
        self.line_local = None
        if centreline_lonlat:
            self.line_local = [to_local(lat, lon, origin.lat, origin.lon) for (lon, lat) in centreline_lonlat]
        self.x = 0.0
        self.y = 0.0
        # None until something measures it. 0.0 would say "at the surface" about a
        # sub nobody has taken a pressure reading from yet — and §2.4 forbids ever
        # deriving this any other way, so there is no honest number to start at.
        self.depth: float | None = None
        self.heading: float | None = origin.heading_deg
        self.payout = 0.0
        self._prev_t: float | None = None

    def update(self, s: SensorSample, speed_ms: float | None = None,
               speed_src: str | None = None) -> NavState:
        """One tick.

        `speed_ms` is the hook for an estimator that has already decided what the speed
        is — the speed KF — with `speed_src` the label that number travels under. Both
        None (every call on the "dr" backend) means this method chooses its own source
        below. The speed has to arrive HERE rather than be stamped on the result,
        because a speed applied after the fact cannot un-integrate a position.
        """
        dt = 0.0 if self._prev_t is None else max(0.0, s.t - self._prev_t)
        self._prev_t = s.t
        self.heading = s.heading_deg
        self.payout = s.encoder_m

        # ---- what speed are we integrating, and can we honestly call it measured? ----
        # §3, and it applies to THIS backend, which is the one that ships by default.
        # Read before "restoring" the old unconditional self.lut.speed(): §4 calls "dr"
        # "the existing DeadReckoner untouched in behaviour", and that means its heading,
        # position, clamp and snap logic — not that it must keep ignoring a sensor the
        # hull now carries. §4b hands the FILTERED estimator its own labels ("kf-paddle"
        # /"kf-lut"), so the plain "paddle"/"lut" of §3 have nowhere else to live, and
        # with the wheel confined to the filtered path the calibration procedure in
        # docs/hardware.md §8.1 — "take the samples where speed_src is paddle-backed" —
        # could not be carried out on a stock install at all.
        if speed_ms is not None:
            v, src = speed_ms, (speed_src or "lut")
        elif s.speed_ms_measured is not None:
            # The wheel turned: something actually measured the water this tick, and the
            # LUT is the one instrument that cannot notice a headwind, a fouled prop or a
            # shopping trolley. abs() because the wheel is mechanically blind to
            # direction — the throttle casts the only vote there is — and _sign() is the
            # LUT's and the speed KF's own tie-break, so a zero-throttle sample resolves
            # the same way in all three places.
            v, src = _sign(s.throttle) * abs(s.speed_ms_measured), "paddle"
        else:
            # Stale or unfitted wheel: back to the open-loop model, and SAY so. Calling a
            # modelled number "paddle" would dress an estimate as a measurement in a
            # dashboard that deliberately styles the two differently (§5).
            v, src = self.lut.speed(s.throttle), "lut"

        # ---- and along WHICH bearing? (see the module docstring) ----
        # No heading, no track. The speed above is still reported — the paddlewheel
        # measured it and it is true — but a speed with no direction cannot become a
        # position, and every candidate substitute is a lie the operator would act
        # on: the last heading claims the sub held its course, the origin's claims it
        # turned back to the launch bearing, 0.0 claims due north. Holding is the only
        # statement supported by the evidence, and it is the honest one: the sub is
        # somewhere within (speed x time held) of here, which is exactly what an
        # operator needs to be told to go and look.
        no_heading = s.heading_deg is None
        confidence = 1.0
        if not no_heading:
            hdg = math.radians(s.heading_deg)
            cur = math.radians(self.current.bearing_deg)
            # compass heading (0=N, 90=E): east=sin, north=cos. + current compensation (§5.4)
            vx = v * math.sin(hdg) + self.current.speed_ms * math.sin(cur)   # east
            vy = v * math.cos(hdg) + self.current.speed_ms * math.cos(cur)   # north
            self.x += vx * dt
            self.y += vy * dt
        else:
            # The current-compensation term is held too, deliberately. It is a
            # constant an operator TYPED IN at launch, not something the sub
            # measured, so letting it creep the track on while every instrument is
            # silent would be the same synthesised position wearing a smaller number.
            confidence = NO_HEADING_CONFIDENCE
        # MEASURED (§2.4) — including when the measurement is "nothing answered".
        # Not `or self.depth`: holding the last depth is exactly the failure the
        # hardware layer was rebuilt to stop, a sensor that dies at 4.33 m and keeps
        # shipping 4.33 while the sub descends to 8.
        self.depth = s.depth_m

        rng = math.hypot(self.x, self.y)
        # tether payout is an UPPER bound on range (§5.5) — clamp, flag low confidence
        if s.encoder_m > 0 and rng > s.encoder_m:
            k = s.encoder_m / rng
            self.x *= k
            self.y *= k
            rng = s.encoder_m
            confidence = min(confidence, 0.5)
        # None is not "0..1 and below the threshold", it is NO IMU ANSWERING — the
        # worse of the two, and it must not be compared with < or it raises. The
        # no-heading floor above already dominates when they arrive together (one
        # chip), but they are separable in principle and each is checked on its own.
        if s.mag_cal is None or s.mag_cal < 2:         # heading quietly gone bad (§5.6)
            confidence = min(confidence, 0.6)

        raw_lat, raw_lon = to_latlon(self.x, self.y, self.origin.lat, self.origin.lon)
        lat, lon, snapped, snap_off = raw_lat, raw_lon, False, 0.0
        if self.snapping and self.line_local:
            near = nearest_on_polyline(self.x, self.y, self.line_local)
            if near and near[2] <= settings.snap_max_dist_m:
                lat, lon = to_latlon(near[0], near[1], self.origin.lat, self.origin.lon)
                snapped, snap_off = True, near[2]
                if snap_off > 8.0:                     # raw↔snapped divergence = drift → surface & re-fix
                    confidence = min(confidence, 0.7)

        return NavState(
            t=s.t, lat=round(lat, 7), lon=round(lon, 7),
            depth_m=None if self.depth is None else round(self.depth, 2),
            heading_deg=None if s.heading_deg is None else round(s.heading_deg, 1),
            x_m=round(self.x, 2), y_m=round(self.y, 2),
            raw_lat=round(raw_lat, 7), raw_lon=round(raw_lon, 7),
            snapped=snapped, snap_offset_m=round(snap_off, 2),
            range_m=round(rng, 2), payout_m=round(s.encoder_m, 2),
            confidence=round(confidence, 2), mag_cal=s.mag_cal, speed_ms=round(v, 3),
            speed_src=src,
            no_heading=no_heading,
            has_origin=True,
        )
