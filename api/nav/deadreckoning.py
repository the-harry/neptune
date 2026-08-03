"""Dead reckoning (spec §5.2) — heading + speed model, integrated ONCE.

NEVER double-integrates acceleration (§2.2). Depth is taken from the sensor,
never integrated (§2.4). Error is linear in distance travelled (~5-15%). Applies
current compensation (§5.4), the tether-payout bound (§5.5), and centreline
snapping (§5.7); confidence drops when clamped, mag is bad, or raw↔snapped diverge.
"""
from __future__ import annotations

import math

from .config import settings
from .geo import to_latlon, to_local
from .models import FlowVector, NavState, Origin, SensorSample
from .snap import nearest_on_polyline
from .speedlut import DEFAULT_LUT, SpeedLUT


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
        self.depth = 0.0
        self.heading = origin.heading_deg
        self.payout = 0.0
        self._prev_t: float | None = None

    def update(self, s: SensorSample) -> NavState:
        dt = 0.0 if self._prev_t is None else max(0.0, s.t - self._prev_t)
        self._prev_t = s.t
        self.heading = s.heading_deg
        self.payout = s.encoder_m

        hdg = math.radians(s.heading_deg)
        v = self.lut.speed(s.throttle)                 # single integration of a speed model
        cur = math.radians(self.current.bearing_deg)
        # compass heading (0=N, 90=E): east=sin, north=cos. + current compensation (§5.4)
        vx = v * math.sin(hdg) + self.current.speed_ms * math.sin(cur)   # east
        vy = v * math.cos(hdg) + self.current.speed_ms * math.cos(cur)   # north
        self.x += vx * dt
        self.y += vy * dt
        self.depth = s.depth_m                         # MEASURED (§2.4)

        confidence = 1.0
        rng = math.hypot(self.x, self.y)
        # tether payout is an UPPER bound on range (§5.5) — clamp, flag low confidence
        if s.encoder_m > 0 and rng > s.encoder_m:
            k = s.encoder_m / rng
            self.x *= k
            self.y *= k
            rng = s.encoder_m
            confidence = 0.5
        if s.mag_cal < 2:                              # heading quietly gone bad (§5.6)
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
            t=s.t, lat=round(lat, 7), lon=round(lon, 7), depth_m=round(self.depth, 2),
            heading_deg=round(s.heading_deg, 1), x_m=round(self.x, 2), y_m=round(self.y, 2),
            raw_lat=round(raw_lat, 7), raw_lon=round(raw_lon, 7),
            snapped=snapped, snap_offset_m=round(snap_off, 2),
            range_m=round(rng, 2), payout_m=round(s.encoder_m, 2),
            confidence=round(confidence, 2), mag_cal=s.mag_cal, speed_ms=round(v, 3),
            has_origin=True,
        )
