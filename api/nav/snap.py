"""Waterway centreline snapping (spec §5.7).

Pure-Python point-to-polyline projection — the equivalent of Shapely's
`line.interpolate(line.project(point))` with no dependency, which suits the
isolated Pi. Collapses a 1-D canal to 'distance along the waterway' and kills
cross-track error for free. Works in local metres (convert the centreline first).
"""

from __future__ import annotations

import math


def _project_point_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0.0:
        return ax, ay, 0.0
    t = ((px - ax) * dx + (py - ay) * dy) / l2
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    sx, sy = ax + t * dx, ay + t * dy
    return sx, sy, t * math.sqrt(l2)


def nearest_on_polyline(px, py, line):
    """line: list[(x,y)] in metres. Returns (sx, sy, dist_to_line, dist_along) or None."""
    if not line or len(line) < 2:
        return None
    best = None
    along = 0.0
    for i in range(1, len(line)):
        ax, ay = line[i - 1]
        bx, by = line[i]
        sx, sy, seg_along = _project_point_segment(px, py, ax, ay, bx, by)
        d = math.hypot(px - sx, py - sy)
        if best is None or d < best[2]:
            best = (sx, sy, d, along + seg_along)
        along += math.hypot(bx - ax, by - ay)
    return best
