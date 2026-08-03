"""Flat-earth local <-> lat/lon (spec §5.2). Exact enough at pond/canal scale."""
from __future__ import annotations

import math

from .config import EARTH_R


def to_latlon(x: float, y: float, lat0: float, lon0: float) -> tuple[float, float]:
    lat = lat0 + (y / EARTH_R) * 180.0 / math.pi
    lon = lon0 + (x / (EARTH_R * math.cos(math.radians(lat0)))) * 180.0 / math.pi
    return lat, lon


def to_local(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    y = math.radians(lat - lat0) * EARTH_R
    x = math.radians(lon - lon0) * EARTH_R * math.cos(math.radians(lat0))
    return x, y
