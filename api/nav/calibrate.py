"""Turn a real dive log into the constants the models actually run on.

    python -m nav.cli calibrate data/dives/dive-20260806-141233.jsonl
    python -m nav.cli calibrate <file> --ground-truth 20      # a measured straight run
    python -m nav.cli calibrate --selftest                    # prove the maths

WHY THIS EXISTS
    Every number in the motion model is currently a guess: the client's
    subMaxSpeedMs (1.0 m/s), headingRatePerS (40 deg/s), the ballast->depth curve,
    and the server's SpeedLUT. A guessed speed model is the single biggest error
    term in dead reckoning, because the error is not random — it is a constant
    multiplier on every metre of the track.

WHAT IT CAN AND CANNOT TELL YOU — read this before trusting a number
    Speed cannot be measured from the log's own x/y. Those coordinates were
    PRODUCED by the speed model, so checking them against it is circular and will
    cheerfully confirm whatever is already configured. Speed needs an outside
    reference, one of:

      --ground-truth D   you ran a measured stretch (the classic: mark 20 m of
                         bank, run one throttle step, time the traverse). Honest,
                         needs no hardware, and is the method the SpeedLUT
                         docstring has always described.
      an encoder         tether payout is a real distance measurement (encoder_m)
      a GNSS surface run position fixes while on the surface

    Heading rate and the depth model do NOT have this problem: heading comes from
    the IMU and depth from the pressure sensor, both measured independently of the
    model. They are calibratable from any ordinary dive — once those sensors exist.

    Where the data cannot support a number, this says so and returns nothing for
    it. A calibration tool that always produces an answer is worse than none.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

# A steady segment: control input held still enough, for long enough, to mean anything.
MIN_RUN_S = 3.0  # shorter than this and startup transients dominate
CTRL_TOLERANCE = 0.05  # how much the input may wander and still count as "held"
MIN_SAMPLES = 8


def read_dive(path: Path):
    """Header + samples from a dive .jsonl. Tolerates a truncated final line."""
    header, samples = None, []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001 — a crashed process ends mid-write
                continue
            if rec.get("type") == "header":
                header = rec
            elif rec.get("type") == "s":
                samples.append(rec)
    return header, samples


def _null_free(run, key):
    """True when every sample in `run` actually carries a reading for `key`.

    WHY A WHOLE SEGMENT GOES, AND NOT JUST THE NULL SAMPLES IN IT. The journal now
    writes a measured channel as JSON null the moment its chip stops answering — the
    MS5837 that drops off the bus at 4.23 m, the BNO085 that browns out under the
    thrusters — so a run can have a hole punched through the middle of it. Dropping
    only the null samples and closing the gap would leave a segment that LOOKS
    contiguous and is not, and both measurements below are computed across a segment's
    span rather than sample by sample:

      * turn rate is (last heading - first heading) / (last t - first t), and those
        headings are only comparable because _unwrap walked every step between them.
        A hole can hide a full rotation, so the rate is not "a bit less certain" across
        one — it can be wrong by 360/dt deg/s, in either direction.
      * the depth model asserts the sub SETTLED at a ballast setting. That is a claim
        about the whole hold, and a hold the sensor was absent for is not one anybody
        watched settle.

    So the segment is dropped and counted, and report() prints the count. A calibration
    fitted to the stretches where the instrument happened to be alive, with no mention
    that the others existed, is the same lie as a substituted zero wearing a better suit.
    """
    return all(s.get(key) is not None for s in run)


def _column_state(samples, key) -> str:
    """'absent' | 'silent' | 'partial' | 'complete' for one measured column."""
    present = [s for s in samples if key in s]
    if not present:
        return "absent"  # no such column in this log
    if all(s[key] is None for s in present):
        return "silent"  # there, and never once answered
    if any(s[key] is None for s in present):
        return "partial"  # answered, then stopped
    return "complete"


def _why_null(samples, key, part, dropped, segment, quantity) -> str:
    """The refusal sentence for a channel that took every usable segment with it.

    THREE DIFFERENT FAILURES ARRIVE HERE AS THE SAME None AND THEY ARE NOT THE SAME
    FINDING. A column that was never logged means fly it again on a newer build. A
    column that is present and null throughout means go and fit the part, or find the
    connector that was never on. A column that answered and then stopped means the part
    is fitted, it worked, and something killed it mid-dive — the only one of the three
    that is an incident. Collapsing them into one sentence sends an operator to do the
    wrong job, which is the same class of harm as collapsing them into one number.
    """
    state = _column_state(samples, key)
    if state == "absent":
        return (
            f"this log has no {key} column at all — it predates that channel, so "
            f"{quantity} is not derivable from it (the {part} may well have been "
            f"fitted and fine; nothing here recorded it)"
        )
    if state == "silent":
        return (
            f"the {key} column is present and null on every sample — the {part} never "
            f"answered once in this dive, so {quantity} is not measurable from it"
        )
    return (
        f"the {part} was null inside every {segment} ({dropped} of them) — it answered "
        f"for part of this dive and then stopped, so {quantity} is not measurable from "
        f"what is left"
    )


def _runs(samples, key, tol=CTRL_TOLERANCE):
    """Split into segments where `key` is held roughly constant and the sub is armed.

    `v is None` already ends a run, and that now covers more than a missing column: the
    ballast level is null from power-on until the stepper is homed (there is no
    position sensor, so an unhomed syringe has no position to report), and a run that
    straddled the homing would otherwise be measured against a level nobody knew.
    """
    out, cur = [], []
    for s in samples:
        v = s.get(key)
        if v is None or not s.get("armed", False):
            if len(cur) >= MIN_SAMPLES:
                out.append(cur)
            cur = []
            continue
        # cur[0][key], not .get(key, 0.0): a sample only enters `cur` after the check
        # above found its value non-None, so the default could never fire — and a 0.0
        # standing in for a control input is exactly the kind of silent, plausible
        # substitution this round is removing everywhere else. Indexing says the
        # invariant out loud and fails loudly if someone ever breaks it.
        if cur and abs(v - cur[0][key]) > tol:
            if len(cur) >= MIN_SAMPLES:
                out.append(cur)
            cur = []
        cur.append(s)
    if len(cur) >= MIN_SAMPLES:
        out.append(cur)
    return [r for r in out if (r[-1]["t"] - r[0]["t"]) >= MIN_RUN_S]


def _unwrap(deg_series):
    """Heading crossing 360 must not read as a 359 deg/s turn.

    EVERY ELEMENT MUST BE A REAL BEARING — callers filter with _null_free() first. A
    None reaching the subtraction below is what crashed `nav.cli calibrate` on any dive
    whose compass stopped, and the fix is upstream of here on purpose: this function
    cannot do anything useful with a gap (it is a running accumulator, so a hole can
    conceal a wrap), and the only thing it could do locally is substitute a bearing
    nobody measured.
    """
    out, prev, acc = [], None, 0.0
    for d in deg_series:
        if prev is not None:
            delta = d - prev
            if delta > 180:
                acc -= 360
            elif delta < -180:
                acc += 360
        out.append(d + acc)
        prev = d
    return out


def turn_rate(samples):
    """deg/s per unit steer, from MEASURED heading. Independent of the speed model.

    Segments the compass went silent inside are skipped, not zero-filled: `.get(key,
    0.0)` defends against a MISSING key and this log carries the key PRESENT AND NULL,
    so the default never fired and a None reached the subtraction in _unwrap. Filling
    it would have been worse than the crash — 0.0 is due north, so a dead compass would
    read as the sub snapping to north and back, i.e. as an enormous measured turn rate,
    and this tool's whole output is a turn rate.
    """
    pts, dropped = [], 0
    for run in _runs(samples, "steer"):
        steer = run[0]["steer"]
        if abs(steer) < 0.1:
            continue
        if not _null_free(run, "heading_deg"):
            dropped += 1
            continue
        hd = _unwrap([s["heading_deg"] for s in run])
        dt = run[-1]["t"] - run[0]["t"]
        if dt <= 0:
            continue
        rate = (hd[-1] - hd[0]) / dt
        pts.append((abs(steer), abs(rate), len(run), dt))
    if not pts:
        if dropped:
            # Naming the count matters: "no steady steer segments" would send someone
            # to fly the dive again, when the dive was flown correctly and the compass
            # died in the middle of it. Those are different jobs.
            return None, _why_null(samples, "heading_deg", "compass", dropped, "steady steer segment", "turn rate")
        return None, "no steady steer segments with a moving heading"
    # Rate should be proportional to steer, so per-unit is the quantity to average.
    per_unit = [r / s for s, r, _n, _d in pts]
    if max(per_unit) <= 0.01:
        return None, (
            "heading never changed under steer — no IMU fitted, or the sub "
            "was never armed. Turn rate is not measurable from this log."
        )
    mean = sum(per_unit) / len(per_unit)
    spread = max(per_unit) - min(per_unit)
    return {
        "deg_per_s_per_unit_steer": round(mean, 1),
        "segments": len(pts),
        "spread": round(spread, 1),
        "dropped_null": dropped,
        "detail": [(round(s, 2), round(r, 1)) for s, r, _n, _d in pts],
    }, None


def depth_model(samples):
    """Ballast -> depth, from the MEASURED pressure. Also independent of the model.

    Holds the depth sensor went silent inside are skipped, not zero-filled, for the
    same reason as turn_rate: the key is present and null, so `.get(key, 0.0)` never
    fired and the None went straight into max()/min(). And 0.0 m is "at the surface" —
    the single depth a sub holding ballast at 9 m is not at — so a filled null would
    have fitted the ballast->depth curve against a descent that never happened.
    """
    pts, dropped = [], 0
    for run in _runs(samples, "ballast", tol=0.03):
        if not _null_free(run, "depth_m"):
            dropped += 1
            continue
        # Settled = depth no longer moving; anything else is the transient, not the target.
        d = [s["depth_m"] for s in run]
        tail = d[max(0, len(d) - 5) :]
        if len(tail) < 3 or (max(tail) - min(tail)) > 0.05:
            continue
        pts.append((run[0]["ballast"], sum(tail) / len(tail), len(run)))
    if not pts:
        if dropped:
            return None, _why_null(
                samples, "depth_m", "pressure sensor", dropped, "settled ballast segment", "the depth model"
            )
        return None, "no settled ballast segments (needs the depth to stop changing)"
    if len(pts) < 2:
        # ONE POINT IS NOT A CURVE — AND IT IS NOT EVIDENCE OF A DEAD SENSOR EITHER.
        # This used to fall through to the sentence below, so a dive that settled at
        # exactly one ballast setting (the ordinary shape of a descend-and-work dive:
        # home, fill, stay down) was told "no pressure sensor fitted" while the MS5837
        # sat there reading 9.00 m on every one of 381 samples. The refusal is right;
        # blaming it on absent hardware was a diagnosis the data never supported, and
        # it sends someone to fit a part that is already fitted and working.
        b, d, _n = pts[0]
        if dropped:
            # The nulls are the reason there is only one, so they lead. "The dive did
            # not hold enough settings" would be false here — it held them, and the
            # sensor was not there for them.
            return None, (
                f"only one settled ballast setting SURVIVES (ballast {b:.2f} at "
                f"{d:.2f} m) — the other {dropped} were skipped because the depth "
                f"was null inside them. The sensor answered for part of this dive "
                f"and then stopped, and a ballast->depth curve needs at least two."
            )
        return None, (
            f"only one settled ballast setting in this log (ballast {b:.2f} at "
            f"{d:.2f} m) — a ballast->depth curve needs at least two. The sensor "
            f"answered; the dive did not hold enough settings to fit against."
        )
    if max(p[1] for p in pts) - min(p[1] for p in pts) < 0.05:
        # "NO PRESSURE SENSOR FITTED" IS A DIAGNOSIS, AND IT MUST NOT BE HANDED TO A
        # DIVE WHOSE SENSOR WAS FITTED AND DIED. Once the null segments are skipped, a
        # dive that descended can be left holding only its surface segments — every
        # remaining point at the same depth, which is the exact shape of a hull with no
        # depth sensor at all. The two readings of that shape send an operator to do
        # completely different things (go and fit the part, versus go and find out why
        # the part stopped), so the count decides which sentence is printed.
        if dropped:
            return None, (
                f"the settled ballast segments that survived are all at the same "
                f"depth — the {dropped} that would have shown the sub descending "
                f"were skipped because the pressure sensor was null inside them. "
                f"It answered for part of this dive and then stopped; the depth "
                f"model is not measurable from what is left."
            )
        return None, (
            "depth never changed with ballast — no pressure sensor fitted. "
            "The depth model is not measurable from this log."
        )
    # depth = k * ballast, through the origin: k is maxDepthM at full ballast.
    num = sum(b * dd for b, dd, _ in pts)
    den = sum(b * b for b, _dd, _ in pts)
    k = (num / den) if den > 0 else 0.0
    return {
        "max_depth_m_at_full_ballast": round(k, 2),
        "points": len(pts),
        "dropped_null": dropped,
        "detail": [(round(b, 2), round(dd, 2)) for b, dd, _ in pts],
    }, None


def speed_from_ground_truth(samples, distance_m):
    """The only honest speed measurement without an encoder or GNSS.

    One armed, steady-throttle run over a KNOWN distance. The log supplies the
    throttle and the duration; the operator supplies the tape measure.
    """
    runs = _runs(samples, "throttle")
    runs = [r for r in runs if abs(r[0]["throttle"]) > 0.05]
    if not runs:
        return None, "no armed, steady-throttle segment in this log"
    best = max(runs, key=lambda r: r[-1]["t"] - r[0]["t"])
    dt = best[-1]["t"] - best[0]["t"]
    thr = abs(best[0]["throttle"])
    if dt <= 0:
        return None, "the steady segment has no duration"
    v = distance_m / dt
    return {
        "throttle": round(thr, 2),
        "seconds": round(dt, 1),
        "distance_m": distance_m,
        "speed_ms": round(v, 3),
        "speed_at_full_throttle_ms": round(v / thr, 3) if thr > 0 else None,
        "other_runs": len(runs) - 1,
    }, None


def encoder_speed(samples):
    """Speed from tether payout — a real distance, when a spool encoder exists."""
    pts = []
    for run in _runs(samples, "throttle"):
        thr = abs(run[0]["throttle"])
        if thr < 0.05:
            continue
        e0, e1 = run[0].get("encoder_m"), run[-1].get("encoder_m")
        if e0 is None or e1 is None:
            continue
        dt = run[-1]["t"] - run[0]["t"]
        if dt <= 0 or (e1 - e0) <= 0:
            continue
        pts.append((thr, (e1 - e0) / dt))
    if not pts:
        return None, "no tether-payout distance in this log (no encoder fitted)"
    by_thr = {}
    for thr, v in pts:
        by_thr.setdefault(round(thr, 1), []).append(v)
    lut = sorted((t, round(sum(vs) / len(vs), 3)) for t, vs in by_thr.items())
    return {"lut_points": lut, "segments": len(pts)}, None


# The MEASURED columns this tool fits against, the name an operator calls the part,
# and what stops being derivable when it goes quiet. The COMMANDED channels (throttle,
# steer, ballast_tgt) are deliberately not here: they are what the operator asked for
# rather than what an instrument answered, they cannot go null, and listing them as
# gaps would put a log's control record on the same footing as its sensors.
MEASURED_COLUMNS = (
    ("heading_deg", "heading", "turn rate"),
    ("depth_m", "depth", "the ballast->depth model"),
    ("ballast", "ballast level", "the segments the depth model is measured over"),
    ("encoder_m", "tether payout", "speed from the encoder"),
)


def _gaps(samples) -> list[str]:
    """Which measured columns this log could not supply, and from when.

    WHEN a channel stopped is the thing a calibration run most needs and the thing a
    substituted default most thoroughly erases. It also separates the two questions an
    operator is actually asking — "is this sensor fitted" (the column is absent) from
    "did it survive the dive" (the column is there and goes null partway) — which look
    identical once every gap has been filled with a plausible number.
    """
    out: list[str] = []
    for key, label, cost in MEASURED_COLUMNS:
        present = [s for s in samples if key in s]
        if not present:
            out.append(f"{label}: no {key} column in this log at all — {cost} is not " f"derivable from it")
            continue
        nulls = [s["t"] for s in present if s[key] is None]
        if not nulls:
            continue
        where = "never answered once" if len(nulls) == len(present) else f"first at t={nulls[0]:.1f}s"
        out.append(
            f"{label}: null in {len(nulls)} of {len(present)} samples "
            f"({100.0 * len(nulls) / len(present):.0f}%), {where}"
        )
    return out


def report(path: Path, ground_truth: float | None = None) -> int:
    header, samples = read_dive(path)
    print(f"dive     : {path.name}")
    print(f"samples  : {len(samples)}")
    if not samples:
        print("nothing to analyse")
        return 1
    span = samples[-1]["t"] - samples[0]["t"]
    armed = sum(1 for s in samples if s.get("armed"))
    has_ctrl = any("throttle" in s for s in samples)
    print(f"duration : {span:.0f} s   armed for {armed} of {len(samples)} samples")
    if not has_ctrl:
        print("\nThis log predates control-channel logging: it has position and depth but")
        print("not what was commanded, so nothing in it can be calibrated. Newer dives will.")
        return 1
    if not armed:
        print("\nThe sub was never armed in this log. Thrusters were never driven, so")
        print("speed and turn rate cannot be measured from it.")

    # WHAT THE LOG COULD NOT SUPPLY, before any constant is derived from it. Above the
    # numbers on purpose: everything below is fitted to whatever stretch the
    # instruments were alive for, and a constant fitted to a third of a dive is a
    # different quantity from one fitted to all of it.
    gaps = _gaps(samples)
    if gaps:
        print("\n--- SENSOR GAPS (channels that stopped answering) ---")
        for g in gaps:
            print(f"  {g}")
        print("  Segments containing a null are SKIPPED below, never zero-filled: 0.0 heading")
        print("  is due north and 0.0 depth is the surface, so a filled null would not merely")
        print("  weaken a fit, it would move it — and quietly.")

    print("\n--- TURN RATE (from measured heading) ---")
    tr, why = turn_rate(samples)
    if tr:
        print(
            f"  {tr['deg_per_s_per_unit_steer']} deg/s per unit steer "
            f"({tr['segments']} segments, spread {tr['spread']} deg/s)"
        )
        print(f"  -> client CONFIG.sim.headingRatePerS = {tr['deg_per_s_per_unit_steer']}")
        if tr["spread"] > tr["deg_per_s_per_unit_steer"] * 0.5:
            print("  NOTE: the spread is wide — treat this as a first estimate, not a constant.")
        if tr.get("dropped_null"):
            print(f"  NOTE: {tr['dropped_null']} further steer segment(s) SKIPPED — the compass")
            print("        was null inside them. This constant is fitted to the stretch where")
            print("        the IMU was answering, not to the whole dive.")
    else:
        print(f"  not measurable: {why}")

    print("\n--- DEPTH (from measured pressure) ---")
    dm, why = depth_model(samples)
    if dm:
        print(f"  {dm['max_depth_m_at_full_ballast']} m at full ballast ({dm['points']} settled points)")
        print(f"  -> client CONFIG.sim.maxDepthM = {dm['max_depth_m_at_full_ballast']}")
        if dm.get("dropped_null"):
            print(f"  NOTE: {dm['dropped_null']} further ballast segment(s) SKIPPED — the depth")
            print("        was null inside them, so nobody watched the sub settle there.")
    else:
        print(f"  not measurable: {why}")

    print("\n--- SPEED ---")
    enc, why_enc = encoder_speed(samples)
    if enc:
        print(f"  from tether payout: {enc['lut_points']}")
        print(f"  -> nav SpeedLUT points (python -m nav.cli speed-cal ...)")
    elif ground_truth:
        gt, why = speed_from_ground_truth(samples, ground_truth)
        if gt:
            print(
                f"  {gt['distance_m']} m in {gt['seconds']} s at throttle {gt['throttle']}" f"  =  {gt['speed_ms']} m/s"
            )
            print(
                f"  -> client CONFIG.map.subMaxSpeedMs = {gt['speed_at_full_throttle_ms']}"
                f"   (extrapolated to full throttle)"
            )
            if gt["other_runs"]:
                print(f"  NOTE: {gt['other_runs']} other steady runs in this log — re-run with the")
                print("        distance for each throttle step to build a proper LUT.")
        else:
            print(f"  not measurable: {why}")
    else:
        print(f"  {why_enc}.")
        print("  Speed CANNOT be taken from this log's x/y: those coordinates were produced")
        print("  BY the speed model, so measuring them against it is circular. Run a measured")
        print("  stretch and pass --ground-truth <metres>, or fit a spool encoder.")
    return 0


# --------------------------------------------------------------------------
def _synthetic(turn=40.0, max_depth=9.0, speed=0.8, dt=0.1):
    """A dive with KNOWN constants, so the analysis can be checked against truth."""
    out, t, hdg, payout = [], 0.0, 0.0, 0.0

    def emit(n, thr, steer, ballast, depth):
        nonlocal t, hdg, payout
        for _ in range(n):
            t += dt
            hdg = (hdg + steer * turn * dt) % 360
            payout += abs(thr) * speed * dt
            out.append(
                {
                    "type": "s",
                    "t": round(t, 3),
                    "x": 0.0,
                    "y": 0.0,
                    "depth_m": round(depth, 3),
                    "heading_deg": round(hdg, 3),
                    "snapped": False,
                    "confidence": 1.0,
                    "throttle": thr,
                    "steer": steer,
                    "left": thr,
                    "right": thr,
                    "ballast": ballast,
                    "ballast_tgt": ballast,
                    "psi": 14.7 + depth * 1.42,
                    "armed": True,
                    "mag_cal": 3,
                    "encoder_m": round(payout, 3),
                }
            )

    emit(100, 0.5, 0.0, 0.0, 0.0)  # straight, half throttle
    emit(100, 0.0, 1.0, 0.0, 0.0)  # full right, stationary
    emit(100, 0.0, 0.5, 0.0, 0.0)  # half right
    emit(100, 0.0, 0.0, 0.5, max_depth * 0.5)  # settled at half ballast
    emit(100, 0.0, 0.0, 1.0, max_depth)  # settled at full ballast
    emit(100, 1.0, 0.0, 1.0, max_depth)  # straight, full throttle
    return out


def selftest() -> int:
    """Prove the maths recovers constants it was not told, from a known dive."""
    truth = {"turn": 40.0, "max_depth": 9.0, "speed": 0.8}
    s = _synthetic(**truth)
    ok = True

    tr, why = turn_rate(s)
    got = tr and tr["deg_per_s_per_unit_steer"]
    good = tr is not None and abs(got - truth["turn"]) <= 1.0
    ok &= good
    print(f"  {'pass' if good else 'FAIL'}  turn rate: got {got}, truth {truth['turn']} ({why or ''})")

    dm, why = depth_model(s)
    gotd = dm and dm["max_depth_m_at_full_ballast"]
    good = dm is not None and abs(gotd - truth["max_depth"]) <= 0.2
    ok &= good
    print(f"  {'pass' if good else 'FAIL'}  depth: got {gotd} m, truth {truth['max_depth']} m ({why or ''})")

    enc, why = encoder_speed(s)
    gote = enc and dict(enc["lut_points"]).get(1.0)
    good = enc is not None and gote is not None and abs(gote - truth["speed"]) <= 0.05
    ok &= good
    print(
        f"  {'pass' if good else 'FAIL'}  speed from encoder at full throttle: got {gote}, "
        f"truth {truth['speed']} ({why or ''})"
    )

    # And the honesty case: a log with no sensors must refuse, not invent.
    flat = [dict(x, heading_deg=0.0, depth_m=0.0, encoder_m=None) for x in _synthetic()]
    tr2, why2 = turn_rate(flat)
    dm2, why3 = depth_model(flat)
    good = tr2 is None and dm2 is None
    ok &= good
    print(f"  {'pass' if good else 'FAIL'}  a sensorless log yields nothing rather than a guess")

    # ---- THE CHIP THAT STOPPED MID-DIVE -----------------------------------
    # The case above is a sensor that was never there. This one is the failure the
    # liveness work is actually about and the one that crashed this file: the columns
    # are PRESENT and their values are null from halfway on, because divelog.py writes
    # a dead chip as JSON null rather than dropping the key. `.get(key, 0.0)` only
    # fires on a missing key, so it never fired, and the None went into _unwrap's
    # subtraction and into depth_model's max()/min().
    #
    # Two things are asserted and the second matters more than the first. It must not
    # raise — but a version that caught the exception and carried on with 0.0 would
    # also not raise, and it would be worse than the crash: 0.0 heading is due north,
    # so a dead compass would read as the sub snapping north and back, i.e. as an
    # enormous MEASURED turn rate, in a tool whose only output is a turn rate. So the
    # numbers are checked too: whatever survives must still be the truth the log was
    # built from, and it must have said how much it threw away.
    half = _synthetic(**truth)
    cut = len(half) // 2
    dead = [dict(s) for s in half[:cut]] + [
        dict(s, heading_deg=None, depth_m=None, psi=None, mag_cal=None) for s in half[cut:]
    ]
    try:
        tr3, why4 = turn_rate(dead)
        dm3, why5 = depth_model(dead)
        crashed = None
    except Exception as exc:  # noqa: BLE001 — the regression this case exists to catch
        tr3 = dm3 = why4 = why5 = None
        crashed = f"{type(exc).__name__}: {exc}"
    good = crashed is None
    ok &= good
    print(
        f"  {'pass' if good else 'FAIL'}  a log whose chips died mid-dive is read at all "
        f"({crashed or 'no exception'})"
    )

    # The synthetic dive turns in its first half and ballasts in its second, so killing
    # the sensors at the midpoint leaves turn rate measurable and takes the depth model
    # away entirely. That asymmetry is the point: one section must still answer, with
    # the truth it was built from, and the other must refuse.
    good = tr3 is not None and abs(tr3["deg_per_s_per_unit_steer"] - truth["turn"]) <= 1.0
    ok &= good
    print(
        f"  {'pass' if good else 'FAIL'}  ...and the half that was measured still reads "
        f"{tr3 and tr3['deg_per_s_per_unit_steer']}, not a heading of 0.0 dragged in "
        f"({why4 or ''})"
    )

    good = dm3 is None and "null" in (why5 or "")
    ok &= good
    print(
        f"  {'pass' if good else 'FAIL'}  ...and the half that was not is refused, naming "
        f"the null rather than fitting 0.0 m as 'the surface' ({why5 or ''})"
    )

    # A skip that is not reported is a quiet lie about what the number covers.
    reported = [g for g in _gaps(dead) if g.startswith(("heading:", "depth:"))]
    good = len(reported) == 2 and all("null in" in g for g in reported)
    ok &= good
    print(
        f"  {'pass' if good else 'FAIL'}  ...and the gap is REPORTED, not silently "
        f"stepped over ({len(reported)} of 2 channels named)"
    )

    print("\nselftest " + ("passed" if ok else "FAILED"))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="nav.calibrate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("dive", nargs="?", help="path to a dive .jsonl")
    ap.add_argument("--ground-truth", type=float, help="metres of a measured straight run, for the speed model")
    ap.add_argument("--selftest", action="store_true", help="check the maths against a known dive")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.dive:
        ap.error("give a dive .jsonl, or --selftest")
    p = Path(a.dive)
    if not p.exists():
        ap.error(f"{p} does not exist")
    return report(p, a.ground_truth)


if __name__ == "__main__":
    raise SystemExit(main())
