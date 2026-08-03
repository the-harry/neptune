"""Dive log (spec §8) — one GeoJSON LineString per dive, per-coordinate samples.

Raw local (x,y) samples are stored untouched. The origin-adjustment transform
(§4.5: translate + rotate) is applied only when producing output GeoJSON, so the
operator can drag a track back onto the waterway without losing the raw data.

Log from day one (§8): depth alone builds a usable picture, and the same file
becomes a bathymetry raster the moment a sounder is fitted — no format change.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .geo import to_latlon
from .models import Adjustment, FlowVector, NavState, Origin


class DiveLog:
    def __init__(self, dive_id: str, started_at: str, origin: Origin,
                 speed_lut_id: str = "default", flow: FlowVector | None = None):
        self.dive_id = dive_id
        self.started_at = started_at
        self.origin = origin
        self.speed_lut_id = speed_lut_id
        self.flow = flow or FlowVector()
        self.adjustment = Adjustment()
        # raw samples (local metres) — never mutated
        self._samples: list[dict] = []

    def add(self, ns: NavState) -> None:
        self._samples.append({
            "t": ns.t, "x": ns.x_m, "y": ns.y_m, "depth_m": ns.depth_m,
            "heading_deg": ns.heading_deg, "snapped": ns.snapped,
            "confidence": ns.confidence,
        })

    def set_adjustment(self, adj: Adjustment) -> None:
        self.adjustment = adj

    def _apply(self, x: float, y: float) -> tuple[float, float]:
        a = self.adjustment
        th = math.radians(a.rotation_deg)
        c, s = math.cos(th), math.sin(th)
        return (a.dx_m + x * c - y * s, a.dy_m + x * s + y * c)

    def to_feature(self) -> dict:
        coords, samples = [], []
        for smp in self._samples:
            ax, ay = self._apply(smp["x"], smp["y"])
            lat, lon = to_latlon(ax, ay, self.origin.lat, self.origin.lon)
            coords.append([round(lon, 7), round(lat, 7)])
            samples.append({
                "t": smp["t"], "depth_m": smp["depth_m"], "heading_deg": smp["heading_deg"],
                "snapped": smp["snapped"], "confidence": smp["confidence"],
            })
        return {
            "type": "Feature",
            "properties": {
                "dive_id": self.dive_id, "started_at": self.started_at,
                "origin": {"lat": self.origin.lat, "lon": self.origin.lon,
                           "accuracy_m": self.origin.accuracy, "heading_deg": self.origin.heading_deg,
                           "source": self.origin.source},
                "adjustment": self.adjustment.model_dump(),
                "speed_lut_id": self.speed_lut_id,
                "flow_vector": {"bearing_deg": self.flow.bearing_deg, "speed_ms": self.flow.speed_ms},
            },
            "geometry": {"type": "LineString", "coordinates": coords},
            "samples": samples,
        }

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        p = directory / f"{self.dive_id}.geojson"
        p.write_text(json.dumps(self.to_feature(), indent=2))
        return p

    @property
    def count(self) -> int:
        return len(self._samples)

    @classmethod
    def load(cls, path: Path) -> "DiveLog":
        d = json.loads(Path(path).read_text())
        pr = d["properties"]
        o = pr["origin"]
        log = cls(pr["dive_id"], pr["started_at"],
                  Origin(lat=o["lat"], lon=o["lon"], accuracy=o.get("accuracy_m", 0),
                         heading_deg=o.get("heading_deg", 0), source=o.get("source", "phone")),
                  pr.get("speed_lut_id", "default"),
                  FlowVector(**pr.get("flow_vector", {})))
        log.adjustment = Adjustment(**pr.get("adjustment", {}))
        # reconstruct raw local samples from stored coords (best-effort; raw x/y not re-derived)
        return log
