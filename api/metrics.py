"""System metrics (CPU temp / RAM / disk) via psutil.

These are the Pi's own health, not vehicle telemetry — read at a slow cadence
(settings.metrics_period_s) and cached, since some probes are mildly expensive.
Degrades gracefully: any probe that isn't available returns a benign default
rather than raising.
"""
from __future__ import annotations

import logging

try:
    import psutil
except Exception:  # noqa: BLE001 — psutil optional off-Pi; metrics just read 0
    psutil = None  # type: ignore

log = logging.getLogger("neptune.metrics")


def cpu_temp_c() -> float:
    if not psutil or not hasattr(psutil, "sensors_temperatures"):
        return 0.0
    try:
        sensors = psutil.sensors_temperatures()
    except Exception:  # noqa: BLE001
        return 0.0
    probe = sensors.get("cpu_thermal") or sensors.get("coretemp")
    if probe:
        return round(probe[0].current, 1)
    # first available sensor as a last resort
    for entries in sensors.values():
        if entries:
            return round(entries[0].current, 1)
    return 0.0


def ram_pct() -> int:
    if not psutil:
        return 0
    try:
        return round(psutil.virtual_memory().percent)
    except Exception:  # noqa: BLE001
        return 0


def disk_free_gb(path: str = "/") -> float:
    if not psutil:
        return 0.0
    try:
        return round(psutil.disk_usage(path).free / 1e9, 1)
    except Exception:  # noqa: BLE001
        return 0.0


def snapshot() -> dict[str, float | int]:
    return {"cpu_c": cpu_temp_c(), "ram_pct": ram_pct(), "disk_gb": disk_free_gb()}
