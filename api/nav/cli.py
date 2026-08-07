"""Navigation CLI (spec §10.5) — bench-usable, no browser.

  python -m nav.cli sim [--filter dr|filtered]  # run the simulator, print + log a track
  python -m nav.cli replay data/dives/sim-20260806-141233.jsonl [--filter dr|filtered|both]
  python -m nav.cli speed-cal --distance 20 --pairs 0.25:36,0.5:19,0.75:13,1.0:10 --id hullA
  python -m nav.cli calibrate data/dives/dive-*.jsonl [--ground-truth 20]
  python -m nav.cli calibrate --selftest
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
from dataclasses import dataclass, field
from pathlib import Path

from .calibrate import read_dive
from .config import settings
from .divelog import DiveLog
from .estimator import make_estimator
from .models import NavState, Origin, SensorSample
from .sim import Simulator
from .speedlut import DEFAULT_LUT, SpeedLUT


class _SampleWithTruth:
    """A SensorSample with the simulator's ground truth carried alongside it.

    DiveLog.add() reads the raw sample with getattr and writes true_x/true_y into the
    journal when it finds them — that is how ground truth reaches a replay log, and
    §4e's acceptance tests are meaningless without it: an error needs something to be
    an error FROM. It cannot ride on the SensorSample itself, because that model has
    no truth field, silently drops extra kwargs at construction and raises on
    assignment — deliberately, since a truth column on the sample would eventually be
    read by something that is not a test. So truth travels beside the sample, is only
    ever produced by the simulator, and only ever reaches the log.
    """

    __slots__ = ("_s", "true_x", "true_y")

    def __init__(self, s: SensorSample, truth: dict) -> None:
        self._s = s
        self.true_x = truth["true_x"]
        self.true_y = truth["true_y"]

    def __getattr__(self, name: str):
        # Everything that is not truth is the sample's own business. An unknown name
        # raises out of the pydantic model, which is exactly what DiveLog's
        # getattr(raw, ..., default) is written to expect.
        return getattr(self._s, name)


def _fly(sim: Simulator, origin: Origin, log: DiveLog, backend: str | None = None,
         dt: float = 0.1):
    """Run the simulator through an estimator, recording estimate AND truth.

    Shared by `sim` and by the §4e acceptance tests on purpose: a test that builds its
    fixture by a different route than the command does is testing a route nobody flies.
    """
    est = make_estimator(origin, backend=backend)
    for s, truth in sim.run_with_truth(dt):
        log.add(est.update(s), _SampleWithTruth(s, truth))
    return est


def _sim(args=None) -> int:
    origin = Origin(lat=52.48, lon=-1.90, accuracy=6, heading_deg=90, source="map_tap")
    sim = Simulator()
    dive_id = "sim-" + time.strftime("%Y%m%d-%H%M%S")
    # The JOURNAL is what `replay` reads back, and DiveLog only opens one when it is
    # given a directory. Without this the sim wrote a GeoJSON alone — conclusions, not
    # sensor readings — and the A/B harness had nothing to re-run (§4e).
    log = DiveLog(dive_id, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), origin,
                  directory=settings.dives_dir)
    est = _fly(sim, origin, log, backend=getattr(args, "filter", None))
    tx, ty, _ = sim.truth()
    err = math.hypot(est.x - tx, est.y - ty)
    path = log.save(settings.dives_dir)
    jsonl = settings.dives_dir / f"{dive_id}.jsonl"
    label = est.backend.upper()
    # est.depth is the dead reckoner's copy of the last MEASURED depth, and it is None
    # until something measures one — same Optional, same TypeError as _print_run's.
    # The scripted simulator always has a depth today, so this is a guard and not a
    # fix, but a print that crashes only when a sensor fails is a print that crashes on
    # exactly the run worth looking at.
    print(f"samples={log.count}  path={sim.path_len:.0f}m  "
          f"final depth={_depth_cell(est.depth)}")
    print(f"truth=({tx:.1f},{ty:.1f})  {label}=({est.x:.1f},{est.y:.1f})  "
          f"err={err:.1f}m ({100*err/max(1,sim.path_len):.1f}%)")
    print(f"dive written: {path}")
    print(f"journal     : {jsonl}")
    print(f"score it    : python -m nav.cli replay {jsonl} --filter both")
    return 0


# ---------------------------------------------------------------------------
# replay (§4e) — re-run a finished dive through the estimators and score them
# ---------------------------------------------------------------------------
#
# This is the harness that decides whether NAV_FILTER is ever promoted off "dr". The
# rule it exists to enforce: a filter is allowed to replace dead reckoning when it has
# beaten it on a track it did not produce, and not before. Taste does not get a vote,
# which is why the default backend is still the old one.

RAW_HEADING_KEY = "raw_heading_deg"     # the COMPASS's heading, not the estimator's

# THE MEASURED CHANNELS, AND WHAT A HOLE IN EACH ONE COSTS THIS REPLAY.
#
# Reported per channel and never rolled up into one "the log is 91% complete", because
# they are not interchangeable: with no heading the track does not advance at all,
# while a null depth only blanks a readout. An average over the two would hide the
# first behind the second, which is the same flattening this whole round exists to
# undo one layer down.
#
# Each entry is (SensorSample attribute, journal column(s) it is read from, the name an
# operator calls the part, what its absence does to the numbers printed below).
SAMPLE_CHANNELS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("heading_deg", (RAW_HEADING_KEY, "heading_deg"), "compass",
     "the track is HELD across those samples — with no bearing there is nothing to "
     "advance along, so distance flown there is missing from BOTH backends"),
    ("mag_cal", ("mag_cal",), "mag status",
     "the heading filter cannot tell whether the compass was worth trusting, so its "
     "trust gate is running blind on those samples"),
    ("gyro_z_dps", ("gyro_z_dps",), "gyro",
     "the heading filter has nothing to coast on when it distrusts the compass"),
    ("accel_fwd_ms2", ("accel_fwd_ms2",), "accelerometer",
     "the speed KF runs without its predict step there"),
    ("speed_ms_measured", ("speed_ms_measured",), "paddlewheel",
     "speed comes from the LUT alone and the snag detector has nothing to see with"),
    ("depth_m", ("depth_m",), "depth",
     "depth is blank across those samples; the track itself is unaffected (§2.4: depth "
     "is measured, never integrated)"),
    ("pressure_psi", ("psi",), "pressure",
     "the depth column across those samples has no provenance beside it"),
)


@dataclass
class ChannelGap:
    """How much of one measured channel this log could not supply, and from when.

    "From when" is the part worth carrying. A dead chip and a healthy one look
    identical in every column except the one that stopped, so the question after a bad
    dive is never "did anything fault" — it is "when did the MS5837 drop off the bus,
    and was it back before the sub was". A count alone cannot answer that.
    """
    key: str
    label: str
    consequence: str
    n_null: int
    n_total: int
    first_null_t: float | None
    absent: bool          # the journal has no such column at all — an older log

    @property
    def pct(self) -> float:
        return 100.0 * self.n_null / max(1, self.n_total)

    @property
    def always(self) -> bool:
        return self.n_null >= self.n_total

    def when(self) -> str:
        if self.absent:
            return "this journal has no such column — it predates that channel"
        if self.always:
            return "never answered once in this log — not fitted, or dead before it started"
        return f"first null at t={self.first_null_t:.1f}s"


def _channel_gaps(rows: list[dict], samples: list[SensorSample]) -> list[ChannelGap]:
    """Per-channel account of what the estimators were NOT given.

    Measured on the reconstructed samples rather than on the raw rows on purpose: what
    matters is what the estimator was actually fed, and a column that is missing from
    the journal and a column that is present-and-null arrive at the estimator as the
    same null. The rows are consulted only to tell those two APART for the report,
    which is a different question and one an operator does need answered.
    """
    gaps: list[ChannelGap] = []
    for attr, row_keys, label, consequence in SAMPLE_CHANNELS:
        nulls = [s.t for s in samples if getattr(s, attr, None) is None]
        if not nulls:
            continue
        gaps.append(ChannelGap(
            key=attr, label=label, consequence=consequence,
            n_null=len(nulls), n_total=len(samples), first_null_t=nulls[0],
            absent=not any(any(k in r for k in row_keys) for r in rows)))
    # Worst first: the operator reading this wants the channel that cost the most.
    gaps.sort(key=lambda g: -g.n_null)
    return gaps


@dataclass
class ReplayLog:
    """A dive journal, reconstructed into the inputs an estimator eats."""
    path: Path
    header: dict
    origin: Origin
    lut: SpeedLUT
    samples: list[SensorSample] = field(default_factory=list)
    truth: list[tuple[float, float] | None] = field(default_factory=list)
    logged: list[tuple[float, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Empty means every measured channel answered on every sample — which is a real
    # and reportable result, not the absence of one.
    gaps: list[ChannelGap] = field(default_factory=list)

    @property
    def has_truth(self) -> bool:
        return any(p is not None for p in self.truth)

    @property
    def duration_s(self) -> float:
        return (self.samples[-1].t - self.samples[0].t) if len(self.samples) > 1 else 0.0


@dataclass
class ReplayRun:
    """What one estimator did with those inputs."""
    backend: str
    states: list[NavState] = field(default_factory=list)

    @property
    def xy(self) -> list[tuple[float, float]]:
        return [(ns.x_m, ns.y_m) for ns in self.states]


def resolve_log(path: Path) -> tuple[Path, str | None]:
    """Find the journal to replay, given whatever the operator typed.

    WHY THE .geojson CANNOT BE REPLAYED. It is a derived artifact: to_feature() keeps
    t, depth, heading, snapped and confidence per coordinate — what the estimator
    CONCLUDED. There is no throttle in it, and with no throttle there is no speed to
    integrate, so no estimator can be re-run from it at all. Half-running one and
    printing a plausible number would be the worst outcome available, so this looks
    for the journal written beside it and refuses if there is none.
    """
    path = Path(path)
    if path.suffix.lower() != ".geojson":
        return path, None
    sibling = path.with_suffix(".jsonl")
    if sibling.exists():
        return sibling, (f"{path.name} is the derived GeoJSON (conclusions, no throttle) — "
                         f"replaying {sibling.name} instead")
    raise FileNotFoundError(
        f"{path.name} is a GeoJSON: it stores where the estimator thought it was, not what "
        f"the sensors said, so nothing can be re-run from it. The journal "
        f"({sibling.name}) is the replayable record and it is not next to it.")


def _sample_from_row(row: dict) -> SensorSample:
    """One journal line back into the SensorSample that produced it.

    EVERY MEASURED COLUMN COMES BACK AS None WHEN THE LOG DOES NOT CARRY IT, and that
    is the whole point of this function's second draft. It used to hand each missing
    column a "safe" default, and every one of those defaults was itself a reading:
    mag_cal 3 is "the compass is perfectly calibrated", gyro 0.0 is "measured: running
    dead straight", accel 0.0 is "measured: coasting", depth 0.0 is "at the surface",
    0.0 psi absolute is not a low pressure but an impossible one, and pitch/roll 0.0 is
    "measured: level". Those are precisely the substitutions divelog.py and sensors.py
    were rewritten to remove from the LIVE path — so replaying an old log through this
    reader silently re-invented, at the last hop, every reading the vehicle now
    refuses to invent at the first. A replay that fabricates its own inputs is not a
    weaker experiment than the real one, it is a different one.

    The COMMANDED columns keep their zeros, and only those: throttle/steer/left/right
    and ballast_target are what the operator asked for, not what an instrument
    measured, and a log with no steer column genuinely was flown with no steer.
    """
    # THE COMPASS COLUMN IS NOT A FALLBACK CHAIN. raw_heading_deg present-and-null is
    # the compass saying nothing, and falling through to heading_deg there would feed
    # the estimator's OWN bearing back in for exactly the samples where the compass was
    # dead — under NAV_FILTER=filtered, its filtered bearing. So the raw column, once
    # the log has one, is the answer including its nulls; heading_deg is consulted only
    # when the log predates the raw column entirely (see the note load_replay_log adds
    # about what that costs the A/B).
    if RAW_HEADING_KEY in row:
        heading = row[RAW_HEADING_KEY]
    else:
        heading = row.get("heading_deg")
    return SensorSample(
        t=row["t"],
        heading_deg=heading,
        depth_m=row.get("depth_m"),
        throttle=row.get("throttle", 0.0),
        # The one coercion left, and it is forced: SensorSample.encoder_m is not
        # Optional (it is a tether-payout BOUND, and the loose bound is the safe one),
        # so a null column cannot travel as null here. 0.0 loosens the clamp rather
        # than tightening it, which is the direction a guess is allowed to be wrong in.
        encoder_m=row.get("encoder_m") or 0.0,
        mag_cal=row.get("mag_cal"),
        pitch_deg=row.get("pitch_deg"),
        roll_deg=row.get("roll_deg"),
        # Absent and null are the same claim here: nothing measured the water this
        # tick. 0.0 would be a measurement of "stopped", which is a different thing.
        speed_ms_measured=row.get("speed_ms_measured"),
        gyro_z_dps=row.get("gyro_z_dps"),
        accel_fwd_ms2=row.get("accel_fwd_ms2"),
        steer=row.get("steer", 0.0),
        left=row.get("left", 0.0),
        right=row.get("right", 0.0),
        ballast_level=row.get("ballast"),
        ballast_target=row.get("ballast_tgt", 0.0),
        pressure_psi=row.get("psi"),
        armed=bool(row.get("armed", False)),
    )


def _brief(exc: Exception, limit: int = 180) -> str:
    """One line of exception text, fit to sit inside a note.

    Pydantic's ValidationError spans several lines and repeats a documentation URL for
    every field that failed, so pasting one raw turns a one-line note into a paragraph
    — and a note nobody reads is worth the same as no note. The failing field and the
    reason survive, which is the part that tells an operator what to go and look at.
    """
    text = " ".join(str(exc).split())
    while "For further information visit" in text:
        head, _, tail = text.partition("For further information visit")
        # lstrip first, so partition drops the URL token itself and not the space in
        # front of it — otherwise the phrase goes and the link it introduced stays.
        text = head + " " + tail.lstrip().partition(" ")[2]
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def load_replay_log(path: Path) -> ReplayLog:
    """Read a dive journal and rebuild the estimator inputs, saying what it could not get.

    Every gap is recorded as a note rather than patched with a default that reads like
    data. A replay whose paddlewheel column is missing is not the same experiment as a
    replay with one, and the summary has to admit which one was run.
    """
    path, note = resolve_log(path)
    header, rows = read_dive(path)
    notes: list[str] = [note] if note else []

    # Local metres are what everything below is scored in, and those are origin-
    # independent — but say so whenever the origin is missing or unreadable, because
    # the lat/lon printed for the track will then be off the coast of Africa and
    # somebody will otherwise report that as a bug.
    origin_d = (header or {}).get("origin") or {}
    origin = None
    if origin_d:
        try:
            origin = Origin(**origin_d)
        except Exception as exc:      # noqa: BLE001 — a bad header must not kill the replay
            # A HEADER MUST NOT BE ABLE TO STOP A DIVE BEING SCORED. The origin is
            # metadata here: it converts local metres to lat/lon for display and feeds
            # nothing the A/B is decided on — DeadReckoner seeds self.heading from
            # heading0 and then overwrites it from the very first sample, so on a log
            # with samples in it heading0 cannot reach a single number printed below.
            # Letting pydantic raise through would mean a dive whose LAUNCH METADATA is
            # imperfect cannot be replayed at all: the same shape of failure as the
            # depth TypeError this round is fixing, one layer up. nav/service.py's
            # set_origin currently bends over backwards to keep a null heading0 out of
            # this header specifically because this reader would choke on it — that
            # workaround should not be load-bearing, so the hit is taken here instead.
            why = _brief(exc)
            try:
                # THE LAT/LON ARE USUALLY FINE. Retry with just the positional fields,
                # so a null heading0 costs the reader the heading0 and not the launch
                # point — throwing away a good fix because the metadata beside it was
                # unreadable is its own small act of invention.
                origin = Origin(lat=float(origin_d["lat"]), lon=float(origin_d["lon"]),
                                accuracy=float(origin_d.get("accuracy") or 0.0))
                notes.append(f"the journal header's origin was only partly readable "
                             f"({why}) — the launch lat/lon were kept and the rest "
                             f"defaulted. heading0 is dive metadata, overwritten by the "
                             f"first sample, so nothing scored below depends on it")
            except Exception:  # noqa: BLE001 — not even a launch point in there
                notes.append(f"the journal header's origin could not be read ({why}) — "
                             f"replayed from (0,0). Every number below is in local metres "
                             f"and is unaffected; only the lat/lon are meaningless")
    if origin is None:
        origin = Origin(lat=0.0, lon=0.0, accuracy=0.0)
        if not origin_d:
            notes.append("no origin in the journal header — replayed from (0,0); "
                         "local metres are unaffected, the lat/lon are meaningless")

    lut_id = (header or {}).get("speed_lut_id", "default")
    lut = DEFAULT_LUT
    if lut_id and lut_id != "default":
        try:
            lut = SpeedLUT.load(settings.speed_lut_dir / f"{lut_id}.json")
        except Exception as exc:      # noqa: BLE001 — a missing LUT must not stop the replay
            notes.append(f"speed LUT {lut_id!r} could not be loaded ({_brief(exc)}) — "
                         f"replayed on the default LUT, so absolute distances will differ "
                         f"from the original dive")

    out = ReplayLog(path=path, header=header or {}, origin=origin, lut=lut, notes=notes)
    skipped = 0
    for row in rows:
        if "throttle" not in row or "t" not in row:
            # Pre-control-channel logs (nav/calibrate.py knows this shape too). Nothing
            # can be integrated without a throttle, and inventing one would produce a
            # track that looks like a dive and is not. A row with no timestamp is
            # likewise unusable — dt is what everything here integrates over.
            skipped += 1
            continue
        out.samples.append(_sample_from_row(row))
        tx, ty = row.get("true_x"), row.get("true_y")
        out.truth.append((tx, ty) if tx is not None and ty is not None else None)
        out.logged.append((row.get("x", 0.0), row.get("y", 0.0)))

    if skipped:
        notes.append(f"{skipped} of {len(rows)} rows carry no throttle and were skipped — "
                     f"that log predates control-channel logging")
    if out.samples and not any(RAW_HEADING_KEY in r for r in rows):
        filtered_recording = any(str(r.get("speed_src", "")).startswith("kf-") for r in rows)
        notes.append(
            "no raw compass column: heading_deg here is what the ESTIMATOR concluded"
            + (". This dive was recorded under NAV_FILTER=filtered, so the replay is "
               "re-filtering an already-filtered heading and the comparison below is "
               "NOT a valid A/B." if filtered_recording else
               " (recorded under 'dr', where that equals the compass — so this replay is "
               "still sound)."))
    # The journal header records the origin and the LUT but not the flow vector, so a
    # dive flown with a current entered replays without it. Both backends get the same
    # (zero) current, so the A/B is still fair; the absolute track is not.
    notes.append("current: not recorded in the journal — both backends replayed with zero flow")
    # WHAT COULD NOT BE READ, counted. This used to be one hand-written note about the
    # paddlewheel, because the paddlewheel was the only channel that could arrive null;
    # every other missing column was quietly filled with a default by _sample_from_row
    # and so had nothing to report. Now that the fills are gone, the gaps are real and
    # every measured channel needs the same accounting the wheel always got.
    out.gaps = _channel_gaps(rows, out.samples)
    return out


def replay(log: ReplayLog, backend: str) -> ReplayRun:
    """Run one estimator over the whole log. No snapping: the journal does not record
    the centreline, so both backends are scored on the raw integration, which is the
    part the filter actually changes."""
    est = make_estimator(log.origin, log.lut, None, None, backend=backend)
    run = ReplayRun(backend=backend)
    for s in log.samples:
        run.states.append(est.update(s))
    return run


def _errors(xy: list[tuple[float, float]],
            ref: list[tuple[float, float] | None]) -> list[float]:
    """Per-sample distance between an estimate and a reference track.

    Samples with no reference are dropped rather than scored as zero error: a log where
    truth only covers part of the run must be scored on that part, not flattered by the
    rest.
    """
    return [math.hypot(x - r[0], y - r[1]) for (x, y), r in zip(xy, ref) if r is not None]


def _episodes(ts: list[float], flags: list[bool]) -> list[tuple[float, float]]:
    """Contiguous True runs as (start_t, end_t). A count of samples is not an event
    count: 40 consecutive snagged frames at 10 Hz is ONE snag, and reporting 40 would
    make a single obstruction look like a rattling failure."""
    out, start = [], None
    for t, f in zip(ts, flags):
        if f and start is None:
            start = t
        elif not f and start is not None:
            out.append((start, t))
            start = None
    if start is not None:
        out.append((start, ts[-1]))
    return out


def score(run: ReplayRun, log: ReplayLog) -> dict:
    """Everything §4e asks for, as numbers: divergence over time, final delta, % of
    time gyro-only, % of time on each speed source, snag events."""
    ts = [ns.t for ns in run.states]
    xy = run.xy
    n = len(run.states) or 1
    src: dict[str, int] = {}
    for ns in run.states:
        src[ns.speed_src] = src.get(ns.speed_src, 0) + 1

    errs = _errors(xy, log.truth)
    logged_errs = _errors(xy, list(log.logged))
    return {
        "backend": run.backend,
        "n": len(run.states),
        "final_xy": xy[-1] if xy else (0.0, 0.0),
        # None when nothing measured the depth on the last sample — INCLUDING when
        # there is no last sample. 0.0 was the old answer to both and it is a reading:
        # "the sub finished at the surface". A dive whose MS5837 dropped off the bus at
        # 4.23 m while the hull went on down to 7.40 m would have been scored, and
        # printed, as having surfaced.
        "final_depth": run.states[-1].depth_m if run.states else None,
        # --- against ground truth (only the simulator has any) ---
        "truth_final": errs[-1] if errs else None,
        "truth_mean": (sum(errs) / len(errs)) if errs else None,
        "truth_worst": max(errs) if errs else None,
        "truth_n": len(errs),
        # Paired with _errors' own filter, so a partially-truthed log plots the stretch
        # it can score and not a shifted version of it.
        "truth_series": list(zip([t for t, r in zip(ts, log.truth) if r is not None], errs)),
        # --- against the track the vehicle actually flew and logged ---
        "logged_final": logged_errs[-1] if logged_errs else None,
        "logged_mean": (sum(logged_errs) / len(logged_errs)) if logged_errs else None,
        # --- the estimator's own report on itself ---
        "gyro_only_pct": 100.0 * sum(1 for ns in run.states if ns.gyro_only) / n,
        "src_pct": {k: 100.0 * v / n for k, v in sorted(src.items(), key=lambda kv: -kv[1])},
        "snags": _episodes(ts, [ns.snagged for ns in run.states]),
        "ts": ts,
        "xy": xy,
    }


def _timeline(series: list[tuple[float, float]], want: int = 6) -> list[tuple[float, float]]:
    """Thin a per-sample error series down to a handful of checkpoints. Divergence over
    time is a shape — does it grow, or settle — and six numbers show that where 1700 do not.

    Indices are computed rather than sliced so the last checkpoint is the end of the run
    exactly once: a duplicated final column reads as a stutter in the data.
    """
    n = len(series)
    if n <= want:
        return list(series)
    idx = sorted({round((i + 1) * (n - 1) / want) for i in range(want)})
    return [series[i] for i in idx]


def _fmt_snags(snags: list[tuple[float, float]]) -> str:
    if not snags:
        return "none"
    return "  ".join(f"t={a:.0f}s for {b - a:.1f}s" for a, b in snags[:4]) + (
        f"  (+{len(snags) - 4} more)" if len(snags) > 4 else "")


def _depth_cell(depth: float | None) -> str:
    """A depth for printing, or the cannot-tell that a dead MS5837 earns.

    THE CRASH THIS REPLACES. NavState.depth_m became Optional so that a stopped depth
    sensor reads as blank everywhere instead of as its last value; this line was an
    f-string format of it, so `nav.cli replay` — the harness THIS COMMIT NAMES as the
    gate for promoting NAV_FILTER — died with a TypeError on any dive whose depth
    sensor stopped before the last sample. The one tool whose job is to judge a dive
    with a failed sensor could not read a dive with a failed sensor.

    "?" and not "0.0 m", and not the last depth that WAS measured: the sub goes on
    descending after the chip stops, so the last reading is the one number guaranteed
    to be wrong by an amount nobody can bound.
    """
    return "?" if depth is None else f"{depth:.1f} m"


def _print_run(sc: dict) -> None:
    x, y = sc["final_xy"]
    print(f"\n--- {sc['backend']} ---")
    print(f"  final          ({x:.1f}, {y:.1f}) m   depth {_depth_cell(sc['final_depth'])}")
    if sc["truth_final"] is not None:
        print(f"  vs truth       final {sc['truth_final']:.2f} m   mean {sc['truth_mean']:.2f} m"
              f"   worst {sc['truth_worst']:.2f} m")
        line = "   ".join(f"t={t:.0f}s {e:.1f}m" for t, e in _timeline(sc["truth_series"]))
        print(f"  divergence     {line}")
    if sc["logged_final"] is not None:
        print(f"  vs logged      final {sc['logged_final']:.2f} m   mean {sc['logged_mean']:.2f} m"
              f"    (the track the vehicle flew at the time)")
    print(f"  gyro-only      {sc['gyro_only_pct']:.1f}% of samples")
    print("  speed source   " + "   ".join(f"{k} {v:.1f}%" for k, v in sc["src_pct"].items()))
    print(f"  snag           {_fmt_snags(sc['snags'])}")


def _print_side_by_side(a: dict, b: dict) -> None:
    print(f"\n--- {a['backend']} vs {b['backend']} ---")
    print(f"  {'':<26}{a['backend']:>12}{b['backend']:>12}")

    def row(label, va, vb, unit="m", fmt="{:.2f}"):
        gap = "" if unit in ("", "%") else " "

        def cell(v):
            return "n/a" if v is None else f"{fmt.format(v)}{gap}{unit}"
        print(f"  {label:<26}{cell(va):>12}{cell(vb):>12}")

    if a["truth_final"] is not None:
        row("final error vs truth", a["truth_final"], b["truth_final"])
        row("mean track error", a["truth_mean"], b["truth_mean"])
        row("worst track error", a["truth_worst"], b["truth_worst"])
    row("gyro-only", a["gyro_only_pct"], b["gyro_only_pct"], unit="%", fmt="{:.1f}")
    row("snag events", float(len(a["snags"])), float(len(b["snags"])), unit="", fmt="{:.0f}")

    sep = math.hypot(a["final_xy"][0] - b["final_xy"][0], a["final_xy"][1] - b["final_xy"][1])
    print(f"\n  the two tracks end {sep:.2f} m apart")

    if a["truth_final"] is None:
        # THE HONEST ANSWER. Two estimates disagreeing tells you they disagree and
        # nothing whatsoever about which one is right. Only a track neither of them
        # produced can settle that, and this log does not contain one.
        print("  no ground truth in this log, so this says how far apart they are and NOT")
        print("  which is right. Score a filter promotion on a sim log, or on a dive with a")
        print("  surfaced GNSS fix to compare against.")
        return
    d_final = a["truth_final"] - b["truth_final"]
    d_mean = a["truth_mean"] - b["truth_mean"]
    winner = b["backend"] if d_mean > 0 else a["backend"]
    print(f"  VERDICT: {winner} is closer to truth — mean track error by "
          f"{abs(d_mean):.2f} m, final position by {abs(d_final):.2f} m")


def _replay(args) -> int:
    try:
        log = load_replay_log(Path(args.log))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if len(log.samples) < 2:
        print(f"error: {log.path.name} has {len(log.samples)} replayable samples — nothing to score",
              file=sys.stderr)
        # The notes say WHY (usually: the rows predate control-channel logging). An
        # error that does not carry its own explanation sends someone to read the file
        # by hand to find out what a tool already knew.
        for note in log.notes:
            print(f"  note : {note}", file=sys.stderr)
        return 2

    print(f"dive     : {log.path.name}")
    print(f"samples  : {len(log.samples)}   duration {log.duration_s:.0f} s")
    print(f"origin   : {log.origin.lat:.5f}, {log.origin.lon:.5f}  ({log.origin.source})")
    print(f"speed LUT: {log.lut.id}")
    print(f"truth    : {'present in the log' if log.has_truth else 'ABSENT (no simulator ran this)'}")
    for note in log.notes:
        print(f"  note   : {note}")

    # HOW MUCH OF THIS LOG WAS UNREADABLE, before any score is printed. It goes above
    # the numbers deliberately: the numbers below are computed over whatever the
    # journal could supply, and a divergence figure from a dive that spent 63% of its
    # samples with no compass is a different quantity from one that had a compass
    # throughout. Printed even when clean, because "every channel answered" is a result
    # the operator wants stated rather than inferred from the absence of a warning.
    if log.gaps:
        print("gaps     : channels this journal could not supply")
        for g in log.gaps:
            print(f"  {g.label:<13} null in {g.n_null} of {g.n_total} samples "
                  f"({g.pct:.0f}%) — {g.when()}")
            print(f"  {'':<13} {g.consequence}")
    else:
        print("gaps     : none — every measured channel answered on every sample")

    backends = ["dr", "filtered"] if args.filter == "both" else [args.filter]
    scores = []
    for name in backends:
        sc = score(replay(log, name), log)
        scores.append(sc)
        _print_run(sc)
    if len(scores) == 2:
        _print_side_by_side(scores[0], scores[1])
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
    sm = sub.add_parser("sim", help="fly the scripted path and write a replayable dive")
    sm.add_argument("--filter", choices=["dr", "filtered"], default=None,
                    help="estimator backend for this run (default: NAV_FILTER)")
    rp = sub.add_parser("replay", help="re-run a dive log through the estimators and score them")
    rp.add_argument("log", help="path to a dive .jsonl journal")
    rp.add_argument("--filter", choices=["dr", "filtered", "both"], default="both",
                    help="which estimator(s) to run (default: both, side by side)")
    cal = sub.add_parser("calibrate", help="derive model constants from a real dive log")
    cal.add_argument("dive", nargs="?")
    cal.add_argument("--ground-truth", type=float)
    cal.add_argument("--selftest", action="store_true")
    sc = sub.add_parser("speed-cal")
    sc.add_argument("--distance", type=float, required=True, help="measured run length in metres")
    sc.add_argument("--pairs", required=True, help="throttle:seconds,throttle:seconds,…")
    sc.add_argument("--id", default="default")
    for name in ("mag-cal", "state", "readiness"):
        sp = sub.add_parser(name)
        sp.add_argument("--base", default="http://127.0.0.1:8000")
    args = p.parse_args(argv)

    if args.cmd == "sim":        return _sim(args)
    if args.cmd == "replay":     return _replay(args)
    if args.cmd == "calibrate":
        from .calibrate import main as _cal
        argv2 = []
        if args.selftest: argv2.append("--selftest")
        else:
            argv2.append(args.dive or "")
            if args.ground_truth is not None: argv2 += ["--ground-truth", str(args.ground_truth)]
        return _cal(argv2)
    if args.cmd == "speed-cal":  return _speed_cal(args)
    if args.cmd == "mag-cal":    return _mag_cal(args)
    if args.cmd == "state":      return _get(args, "/api/nav/state")
    if args.cmd == "readiness":  return _get(args, "/api/readiness")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
