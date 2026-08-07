"""Dive log (spec §8) — one GeoJSON LineString per dive, per-coordinate samples.

Raw local (x,y) samples are stored untouched. The origin-adjustment transform
(§4.5: translate + rotate) is applied only when producing output GeoJSON, so the
operator can drag a track back onto the waterway without losing the raw data.

Log from day one (§8): depth alone builds a usable picture, and the same file
becomes a bathymetry raster the moment a sounder is fitted — no format change.

A NULL IS LOGGED AS NULL. Every measured channel here is nullable, and JSON null
is what "no instrument answered" is written as — never 0.0, never the last value,
never a key quietly dropped. This is the file whose entire purpose is replay and
calibration: a log that records 0.0 for a depth nobody measured produces a
bathymetry raster with a hole punched through it at the surface, and a depth model
(nav/calibrate.py) fitted against readings that were never taken. Worse, the one
thing a post-incident replay most needs to establish — WHEN the sensor stopped —
is exactly the thing a default erases. Absent data must stay visibly absent.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path

from .geo import to_latlon
from .models import Adjustment, FlowVector, NavState, Origin

log = logging.getLogger("neptune.nav.divelog")

# How often the append log is fsync'd. flush() alone survives a process crash;
# fsync() is what survives a power cut, and it is far too expensive to do per
# sample. Every few seconds bounds the worst-case loss to that window.
FSYNC_EVERY_S = 5.0


class DiveLog:
    def __init__(self, dive_id: str, started_at: str, origin: Origin,
                 speed_lut_id: str = "default", flow: FlowVector | None = None,
                 directory: Path | None = None, auto: bool = False):
        self.dive_id = dive_id
        self.started_at = started_at
        self.origin = origin
        self.speed_lut_id = speed_lut_id
        self.flow = flow or FlowVector()
        self.adjustment = Adjustment()
        self.auto = auto
        # raw samples (local metres) — never mutated
        self._samples: list[dict] = []

        # ---- append-only journal (safety) --------------------------------
        # The GeoJSON is written once, at stop. That is fine for reading a finished
        # dive and useless as a safety record: a crash, a power cut or a killed
        # process loses everything that was still in memory — which, before this,
        # was the ENTIRE track. So every sample is also appended to a .jsonl here,
        # as it happens. The GeoJSON becomes a derived artifact; this is the record.
        self._fh = None
        self._last_fsync = time.monotonic()
        if directory is not None:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                self._fh = open(directory / f"{dive_id}.jsonl", "a", buffering=1, encoding="utf-8")
                self._write({"type": "header", "dive_id": dive_id, "started_at": started_at,
                             "origin": origin.model_dump() if hasattr(origin, "model_dump") else None,
                             "speed_lut_id": speed_lut_id, "auto": auto})
            except Exception as exc:  # noqa: BLE001 — logging must never stop the dive
                log.warning("could not open the dive journal for %s: %s", dive_id, exc)
                self._fh = None

    def _write(self, obj: dict) -> None:
        """Append one line. Never raises — a full disk must not stop the vehicle."""
        if self._fh is None:
            return
        try:
            self._fh.write(json.dumps(obj, separators=(",", ":")) + "\n")
            now = time.monotonic()
            if now - self._last_fsync >= FSYNC_EVERY_S:
                self._fh.flush()
                os.fsync(self._fh.fileno())
                self._last_fsync = now
        except Exception as exc:  # noqa: BLE001
            log.warning("dive journal write failed (continuing): %s", exc)
            try:
                self._fh.close()
            except Exception:  # noqa: BLE001
                pass
            self._fh = None

    def add(self, ns: NavState, raw=None) -> None:
        """Record one sample. `raw` is the SensorSample behind it, when there is one.

        The nav STATE alone (where we think we are) cannot calibrate the model that
        produced it — that is circular. The raw control channels are what make a dive
        log analysable afterwards: throttle next to distance gives speed per unit
        throttle, steer next to heading gives turn rate, ballast next to depth gives
        the depth model. See nav/calibrate.py, which reads exactly these fields.

        The same argument, pushed one step further, is why the MEASURED channels are
        here now. `nav.cli replay` re-runs a finished dive through either estimator
        and scores them against each other, and it can only do that if the journal
        carries what the SENSORS said rather than only what the filter concluded:
        the compass's own heading (the filtered backend overwrites ns.heading_deg
        with its filtered one), the paddlewheel, the gyro rate, the forward
        acceleration and the tether payout. A dive logged as conclusions alone can
        be read back, but it can never be re-judged — and re-judging the filter with
        real data is the entire reason NAV_FILTER defaults to the old estimator.
        """
        # depth_m and heading_deg go in exactly as the estimator produced them,
        # INCLUDING None. They are the two the tempting `or 0.0` would ruin: a null
        # depth written as 0.0 says the sub surfaced, and a null heading written as
        # 0.0 says it turned due north — both are events, both are false, and both
        # are indistinguishable from real ones a year later when this file is all
        # that is left of the dive.
        smp = {
            "t": ns.t, "x": ns.x_m, "y": ns.y_m, "depth_m": ns.depth_m,
            "heading_deg": ns.heading_deg, "snapped": ns.snapped,
            "confidence": ns.confidence,
            # What the estimator concluded about ITSELF. speed_src is what separates
            # a measurement from an estimate after the fact, and snagged/gyro_only
            # mark the stretches where the track is least trustworthy — which are
            # exactly the stretches worth replaying. no_heading marks the stretches
            # where x/y are NOT a track at all but a held last fix; without it a
            # replay reads a straight run of identical coordinates as a sub sitting
            # still, which is the opposite of what was happening.
            "speed_ms": ns.speed_ms, "speed_src": ns.speed_src,
            "snagged": ns.snagged, "gyro_only": ns.gyro_only,
            "no_heading": ns.no_heading,
        }
        if raw is not None:
            smp.update({
                "throttle": getattr(raw, "throttle", 0.0),
                "steer": getattr(raw, "steer", 0.0),
                "left": getattr(raw, "left", 0.0),
                "right": getattr(raw, "right", 0.0),
                # None, not 0.0: an unhomed stepper has no position, and 0.0 here
                # would be logged as the specific claim "the syringe was empty".
                "ballast": getattr(raw, "ballast_level", None),
                "ballast_tgt": getattr(raw, "ballast_target", 0.0),
                # Pressure travels with depth and goes null with it — a depth in this
                # file with no pressure beside it is a number with no provenance, and
                # 0.0 psi absolute is not a low reading, it is an impossible one.
                "psi": getattr(raw, "pressure_psi", None),
                "armed": bool(getattr(raw, "armed", False)),
                # None = no IMU answered. NOT 3, which was the old fallback and is the
                # strongest trust mark in the system: every replay of a dive with a
                # dead compass would have been scored as though the magnetometer had
                # been perfectly calibrated throughout.
                "mag_cal": getattr(raw, "mag_cal", None),
                # ---- what the instruments measured, unfiltered ----
                "raw_heading_deg": getattr(raw, "heading_deg", None),
                "encoder_m": getattr(raw, "encoder_m", 0.0),
                # None survives into the journal as null on purpose: the wheel was
                # stale or not fitted, which is not the same reading as 0.0 m/s. The
                # same argument applies to every line below it — 0.0 °/s is "measured:
                # not turning", 0.0 m/s² is "measured: coasting", 0.0° of pitch is
                # "measured: level". Each is a reading a dead BNO085 cannot have taken,
                # and each was being written into the record as though it had.
                "speed_ms_measured": getattr(raw, "speed_ms_measured", None),
                "gyro_z_dps": getattr(raw, "gyro_z_dps", None),
                "accel_fwd_ms2": getattr(raw, "accel_fwd_ms2", None),
                "pitch_deg": getattr(raw, "pitch_deg", None),
                "roll_deg": getattr(raw, "roll_deg", None),
            })
            # Ground truth, only when the sample actually carries it — the simulator
            # knows where it really is; a canal does not. §4e's acceptance tests score
            # "filtered" against "dr" on track error, and an error needs something to
            # be an error FROM. A real dive simply has no such keys: absent truth is
            # left absent rather than filled in with the estimate that is on trial.
            tx = getattr(raw, "true_x_m", getattr(raw, "true_x", None))
            ty = getattr(raw, "true_y_m", getattr(raw, "true_y", None))
            if tx is not None and ty is not None:
                smp["true_x"], smp["true_y"] = tx, ty
        self._samples.append(smp)
        self._write({"type": "s", **smp})

    def close(self) -> None:
        if self._fh is None:
            return
        try:
            self._write({"type": "end", "t": time.time(), "n": len(self._samples)})
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._fh.close()
        except Exception:  # noqa: BLE001
            pass
        self._fh = None

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
                # Carried out to the GeoJSON as well, because this is the artifact
                # somebody opens in a map viewer: without it a stretch of repeated
                # identical coordinates reads as "the sub stopped here", when what
                # actually happened is that the compass stopped and the track was
                # held. .get() so a log rebuilt from an older journal still renders.
                "no_heading": smp.get("no_heading", False),
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
        self.close()
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
