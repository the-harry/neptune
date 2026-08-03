"""Speed lookup table (spec §5.3): throttle setting -> m/s, from empirical calibration.

Built once per hull ('measure 20 m, run each throttle step, time the traverse').
This is the single largest accuracy win in the whole system, so it's a first-class
object stored per hull config — not a magic constant.
"""
from __future__ import annotations

import json
from pathlib import Path


class SpeedLUT:
    def __init__(self, points: list[tuple[float, float]], lut_id: str = "default"):
        # points: (throttle 0..1, speed m/s), sorted, must start at (0,0).
        self.id = lut_id
        self.points = sorted(points)
        if not self.points or self.points[0][0] > 0.0:
            self.points = [(0.0, 0.0)] + self.points

    def speed(self, throttle: float) -> float:
        """Signed speed for -1..1 throttle (reverse mirrors forward magnitude)."""
        s = 1.0 if throttle >= 0 else -1.0
        a = min(1.0, abs(throttle))
        pts = self.points
        for i in range(1, len(pts)):
            t0, v0 = pts[i - 1]
            t1, v1 = pts[i]
            if a <= t1:
                if t1 == t0:
                    return s * v1
                f = (a - t0) / (t1 - t0)
                return s * (v0 + f * (v1 - v0))
        return s * pts[-1][1]

    def to_dict(self) -> dict:
        return {"id": self.id, "points": self.points}

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        p = directory / f"{self.id}.json"
        p.write_text(json.dumps(self.to_dict(), indent=2))
        return p

    @classmethod
    def load(cls, path: Path) -> "SpeedLUT":
        d = json.loads(Path(path).read_text())
        return cls([tuple(x) for x in d["points"]], d.get("id", Path(path).stem))


# A reasonable default (small canal sub): full throttle ≈ 1 m/s.
DEFAULT_LUT = SpeedLUT([(0.0, 0.0), (0.25, 0.28), (0.5, 0.55), (0.75, 0.82), (1.0, 1.0)], "default")
