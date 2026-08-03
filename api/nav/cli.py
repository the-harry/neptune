"""Navigation CLI (spec §10.5) — bench-usable, no browser.

  python -m nav.cli sim                       # run the simulator through DR, print+log a track
  python -m nav.cli speed-cal --distance 20 --pairs 0.25:36,0.5:19,0.75:13,1.0:10 --id hullA
  python -m nav.cli mag-cal   [--base http://127.0.0.1:8000]   # guide IMU calibration
  python -m nav.cli state     [--base ...]
  python -m nav.cli readiness [--base ...]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time

from .config import settings
from .deadreckoning import DeadReckoner
from .divelog import DiveLog
from .models import Origin
from .sim import Simulator
from .speedlut import SpeedLUT


def _sim():
    origin = Origin(lat=52.48, lon=-1.90, accuracy=6, heading_deg=90, source="map_tap")
    sim = Simulator()
    dr = DeadReckoner(origin)
    log = DiveLog("sim-" + time.strftime("%Y%m%d-%H%M%S"),
                  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), origin)
    ns = None
    for s in sim.run(0.1):
        ns = dr.update(s)
        log.add(ns)
    tx, ty, _ = sim.truth()
    err = math.hypot(dr.x - tx, dr.y - ty)
    path = log.save(settings.dives_dir)
    print(f"samples={log.count}  path={sim.path_len:.0f}m  final depth={dr.depth:.1f}m")
    print(f"truth=({tx:.1f},{ty:.1f})  DR=({dr.x:.1f},{dr.y:.1f})  err={err:.1f}m ({100*err/max(1,sim.path_len):.1f}%)")
    print(f"dive written: {path}")
    return 0


def _speed_cal(args) -> int:
    # pairs "throttle:seconds" over a measured distance → speed = distance/seconds
    pts = [(0.0, 0.0)]
    for pair in args.pairs.split(","):
        thr_s, t_s = pair.split(":")
        thr, secs = float(thr_s), float(t_s)
        if secs <= 0:
            print(f"bad time in {pair!r}", file=sys.stderr); return 2
        pts.append((thr, round(args.distance / secs, 3)))
    lut = SpeedLUT(pts, args.id)
    p = lut.save(settings.speed_lut_dir)
    print(f"speed LUT '{args.id}' ({args.distance} m runs):")
    for thr, v in lut.points:
        print(f"  throttle {thr:>4}  ->  {v:.3f} m/s")
    print(f"saved: {p}")
    return 0


def _mag_cal(args) -> int:
    import httpx
    print("Magnetometer calibration (§5.6) — do this IN THE WATER, away from the dock.")
    print("Move the sub through slow figure-8s and full rotations until cal = 3.\n")
    good = 0
    try:
        for _ in range(600):
            try:
                st = httpx.get(args.base.rstrip("/") + "/api/nav/state", timeout=2).json()
            except Exception:  # noqa: BLE001
                print("  (waiting for nav state…)"); time.sleep(1); continue
            cal = st.get("mag_cal", "?")
            bar = {0: "UNRELIABLE", 1: "LOW", 2: "OK", 3: "GOOD"}.get(cal, "?")
            print(f"  mag_cal = {cal} ({bar})   heading={st.get('heading_deg','?')}")
            good = good + 1 if cal == 3 else 0
            if good >= 5:
                print("\nCalibration GOOD and stable. Done."); return 0
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    print("stopped."); return 1


def _get(args, path) -> int:
    import httpx
    try:
        r = httpx.get(args.base.rstrip("/") + path, timeout=10)
        print(json.dumps(r.json(), indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr); return 2


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="nav.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sim")
    sc = sub.add_parser("speed-cal")
    sc.add_argument("--distance", type=float, required=True, help="measured run length in metres")
    sc.add_argument("--pairs", required=True, help="throttle:seconds,throttle:seconds,…")
    sc.add_argument("--id", default="default")
    for name in ("mag-cal", "state", "readiness"):
        sp = sub.add_parser(name)
        sp.add_argument("--base", default="http://127.0.0.1:8000")
    args = p.parse_args(argv)

    if args.cmd == "sim":        return _sim()
    if args.cmd == "speed-cal":  return _speed_cal(args)
    if args.cmd == "mag-cal":    return _mag_cal(args)
    if args.cmd == "state":      return _get(args, "/api/nav/state")
    if args.cmd == "readiness":  return _get(args, "/api/readiness")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
