"""Navigation CLI (spec §10.5) — bench-usable, no browser.

  python -m nav.cli sim [--filter dr|filtered]  # run the simulator, print + log a track
  python -m nav.cli replay data/dives/sim-20260806-141233.jsonl [--filter dr|filtered|both]
  python -m nav.cli speed-cal --distance 20 --pairs 0.25:36,0.5:19,0.75:13,1.0:10 --id hullA
  python -m nav.cli calibrate data/dives/dive-*.jsonl [--ground-truth 20]
  python -m nav.cli calibrate --selftest
  python -m nav.cli area-fetch --at 52.4785,-1.9105   # CREATE an area at a launch
                                                      # point and download everything
                                                      # for it — BOOTSTRAP, needs internet
  python -m nav.cli area-fetch gas-street [--refresh] [--radius-m 1000] [--detail high]
  python -m nav.cli area-fetch gas-street --dry-run   # what is on the card, fetch nothing
  python -m nav.cli crt-fetch gas-street          # CRT hazards — BOOTSTRAP, needs internet
  python -m nav.cli crt-fetch --list             # what the Trust publishes (needs internet)
  python -m nav.cli soundings data/dives/dive-*.jsonl [--area gas-street] [--dry-run]
  python -m nav.cli soundings --selftest
  python -m nav.cli mag-cal   [--base http://127.0.0.1:8000]   # guide IMU calibration
  python -m nav.cli state     [--base ...]
  python -m nav.cli readiness [--base ...]

WHICH OF THESE NEED THE INTERNET (§3, the two-phase rule). Exactly two, and both
are BOOTSTRAP-time commands that say so before they do anything: `area-fetch`,
which fills a whole offline area, and `crt-fetch`, which fills in the hazard
layers alone. Everything else — including `soundings`, which reads a journal off
the card and writes to a store on the same card — runs unchanged in the isolated
canal-side segment, where there is no WAN and no hostname resolution.

`area-fetch` IS THE ONE TO RUN BEFORE A TRIP. It is the same job the console runs
by itself when a launch point is set (nav/service.py, AreaFetch), driven from a
terminal instead of a WebSocket, so a card filled at home and a card filled by
tapping the map are the same card.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import areas as areamod
from . import nominal as nominalmod
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


# ===========================================================================
# crt-fetch and soundings (§3) — filling an area's overlays
# ===========================================================================
#
# BOTH ARE DRIVERS, NOT IMPLEMENTATIONS. nav/crt.py does the downloading and
# nav/soundings.py owns what a sounding is and what qualifies as one; what lives
# here is the part an operator types, the PREFLIGHT that says whether the command
# can possibly work, and — for the fetch — a report of what actually changed on
# disk. Both are imported inside their command rather than at the top of this
# file, exactly as `calibrate` already is: `python -m nav.cli sim` must not stop
# working because a module six of these commands never touch is missing from a
# card.
#
# WHAT THE FETCH REPORT IS TAKEN FROM. Not the downloader's return value — the
# directory. `download_hazards` deliberately does not raise (a Trust server having
# a bad afternoon must not throw away an area's imagery), so it reports failure by
# returning, and a driver that printed its own optimism would be the one place in
# this chain where a layer that never landed looks fetched. The before/after is a
# listing of the files.


def _reachable(url: str, timeout: float = 4.0) -> tuple[bool, str]:
    """Is there any internet behind this URL, answered inside a fixed deadline.

    THE ISOLATED SEGMENT HAS NO DNS (§3). Not "slow DNS" — no resolver to ask, so
    a lookup can sit for tens of seconds before anything raises, and a bootstrap
    command that appears to hang at the water's edge is read as a broken tool
    rather than as the correct answer to "is there internet here?". Both nav/crt.py
    and satellite.py say the same thing about that in their own words; this is the
    only place in the repo that does something about it. The lookup runs on a
    daemon thread and the deadline is enforced HERE, because a socket timeout
    bounds the connect and does not bound getaddrinfo.

    A PROBE, NOT A GUARANTEE, and the message says which host was asked. It
    answers "something accepted a TCP connection there"; the fetch that follows
    can still fail and reports its own failure when it does. What this buys is the
    difference between a clean "unavailable — come back before you go isolated"
    and a wait with no message.
    """
    parts = urlsplit(url if "//" in url else "//" + url)
    host = parts.hostname
    if not host:
        return False, f"{url!r} carries no hostname to probe"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    box: dict = {}

    def probe():
        try:
            socket.create_connection((host, port), timeout=timeout).close()
            box["ok"] = True
        except Exception as exc:  # noqa: BLE001 — every failure here is one answer
            box["err"] = exc

    th = threading.Thread(target=probe, daemon=True)
    th.start()
    th.join(timeout + 1.0)
    if th.is_alive():
        # Daemon, so it cannot hold the interpreter open on the way out. A wedged
        # resolver is exactly the case this exists for and it must not become a
        # command that never returns.
        return False, (f"{host}:{port} did not answer within {timeout:.0f}s and the "
                       f"lookup is still outstanding — the usual reading is a tether "
                       f"with no route off it and no DNS server to ask")
    if box.get("ok"):
        return True, f"{host}:{port} answered"
    exc = box.get("err")
    if isinstance(exc, socket.gaierror):
        return False, (f"{host} does not resolve ({exc}) — there is no name service on "
                       f"this network, which is the NORMAL state of the isolated "
                       f"segment and not a fault")
    return False, f"{host}:{port} unreachable ({exc})"


def _crt_files(name: str) -> dict[str, int]:
    """The CRT layer files on disk for one area, by name and size. The fetch's
    before/after is two of these, so what gets printed is what is on the card."""
    from . import crt
    out: dict[str, int] = {}
    d = crt.area_dir(name)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.geojson")):
        try:
            out[p.name] = p.stat().st_size
        except OSError:
            out[p.name] = -1
    return out


def _print_crt_diff(before: dict[str, int], after: dict[str, int]) -> None:
    keys = sorted(set(before) | set(after))
    if not keys:
        print("  (no layer files at all — nothing has ever been fetched for this area)")
        return
    for k in keys:
        b, a = before.get(k), after.get(k)
        if a is None:
            print(f"  {k:<44} GONE")
        elif b is None:
            print(f"  {k:<44} NEW        {a:>10,} bytes")
        elif a != b:
            print(f"  {k:<44} UPDATED    {a:>10,} bytes  (was {b:,})")
        else:
            print(f"  {k:<44} unchanged  {a:>10,} bytes")


def _crt_fetch(args) -> int:
    """Download the CRT hazard layers for an area. BOOTSTRAP-time: needs internet."""
    try:
        from . import crt
    except ImportError as exc:      # noqa: F841 — reported, not raised
        print(f"error: api/nav/crt.py is not in this build ({exc}) — nothing else in "
              f"the repo can fetch CRT layers", file=sys.stderr)
        return 2

    print("crt-fetch: the Canal & River Trust hazard layers for an offline area")
    print("  BOOTSTRAP-time (§3). It needs the internet and there is none in the")
    print("  isolated canal-side segment, so run it before you go. Sluices, weirs,")
    print("  stop-plank grooves and outfalls are invisible from the surface.\n")

    # PREFLIGHT — every reason this cannot work, gathered before anything is
    # tried, so one run tells the whole story. A command that reports the first
    # blocker, gets fixed, and then reports the second is a command somebody
    # drives to the canal twice.
    online, why = _reachable(settings.crt_hub_search_url)
    print(f"internet : {('available — ' + why) if online else ('UNAVAILABLE — ' + why)}")

    if args.list:
        if not online:
            print("\nthe layer catalogue lives on the Trust's servers and cannot be "
                  "listed from here.", file=sys.stderr)
            return 2
        print("\nlayers the Trust currently publishes (key, layer id, national count, "
              "licence class):")
        return asyncio.run(crt._main(["--list"]))

    name = crt.safe_area_name(args.area or "")
    if not name:
        print(f"error: {args.area!r} is not a usable area name — it has to be plain "
              f"letters, digits, spaces, dot, dash or underscore, because it becomes a "
              f"directory name.", file=sys.stderr)
        return 2

    if args.bbox:
        try:
            bbox = [float(v) for v in args.bbox.replace(" ", "").split(",")]
            if len(bbox) != 4:
                raise ValueError("needs four numbers")
        except ValueError as exc:
            print(f"error: --bbox must be W,S,E,N in degrees ({exc})", file=sys.stderr)
            return 2
        bbox_note = "given on the command line"
    else:
        bbox = crt.area_bbox(name)
        bbox_note = f"from {settings.areas_dir / (name + '.json')}"

    print(f"area     : {name}")
    print(f"store    : {crt.area_dir(name)}")
    if bbox:
        print(f"bbox     : {bbox[0]:.4f},{bbox[1]:.4f} .. {bbox[2]:.4f},{bbox[3]:.4f}  "
              f"({bbox_note})")
    else:
        print(f"bbox     : MISSING — {bbox_note} does not exist or carries no bbox. "
              f"Hazards belong to an area, so download the area first or pass --bbox.")

    before = _crt_files(name)
    if not online or not bbox:
        print("\nnothing was fetched. What is on this card is unchanged:")
        _print_crt_diff(before, before)
        print("\nAn ABSENT layer is not an empty one. Nothing above claims this water is")
        print(f"clear — it says nobody has downloaded what is in it. "
              f"GET /api/areas/{name}/crt says the same thing to the console.")
        return 2

    async def say(msg: dict) -> None:
        print("  " + " ".join(f"{k}={v}" for k, v in msg.items() if k != "bbox"),
              flush=True)

    print("\nfetching (sequential and rate-limited — this is somebody's free quota):")
    try:
        res = asyncio.run(crt.download_hazards(name, bbox, progress=say))
    except Exception as exc:  # noqa: BLE001 — documented not to raise; believe the disk
        print(f"\nerror: the fetch raised ({_brief(exc)}). Whatever landed before it "
              f"stopped is listed below.", file=sys.stderr)
        res = {"ok": False, "error": _brief(exc)}

    after = _crt_files(name)
    print("\nwhat is on the card now (read off the disk, not off the downloader's "
          "report):")
    _print_crt_diff(before, after)
    if res.get("ok"):
        print(f"\nlayers   : {res.get('layers')} written, {res.get('features')} features, "
              f"{res.get('skipped')} skipped")
    else:
        print(f"\nfailed   : {res.get('error')}", file=sys.stderr)
    for w in res.get("warnings") or []:
        # Every one of these is a claim about what the file is worth — a licence
        # that refuses reuse, a count that disagrees with the server's own, a
        # layer that was NOT written. They are printed in full rather than
        # counted: a warning nobody reads is worth the same as no warning.
        print(f"  warn   : {w}")
    print(f"provenance: {crt.provenance_path(name)}")
    # WHETHER THAT FETCH UPGRADED THE DEPTH GUIDANCE, said out loud. If one of the
    # layers that just landed publishes a draught per navigation, the NOMINAL layer
    # stops quoting a figure somebody typed into nominal.py and starts quoting the
    # Trust; if none does, that is worth knowing too, because it means the
    # hand-typed table is the best this area will ever have.
    _src, scan = nominalmod.crt_depth_layer(name)
    print(f"nominal  : {scan}")
    print(f"serve it : GET /api/areas/{name}/crt")
    return 0 if res.get("ok") else 1


# ===========================================================================
# area-fetch (§3) — the WHOLE bootstrap for one area, in one command
# ===========================================================================
#
# WHY THIS EXISTS ALONGSIDE crt-fetch. crt-fetch fills in ONE of the three things
# an offline area needs, and it requires an area that already exists to fill in —
# which, until this round, nothing in the repo ever created. data/areas/ was empty,
# data/crt/gas-street/ held 26 perfectly good hazard layers belonging to no area,
# and the console said "no chart data is downloaded" forever. This is the command
# that creates the area and fills all three: the waterway centreline, the hazard
# charts and the imagery, in that order.
#
# IT IS THE SAME JOB THE CONSOLE RUNS. Not a parallel implementation of it —
# nav/service.py's AreaFetch, driven with a printer instead of a WebSocket, so a
# fetch done at the kitchen table the night before and a fetch started by tapping
# a launch point produce byte-identical cards and identical records on disk. The
# alternative is two code paths that agree until the day they do not, and the day
# they do not is at the water.
#
# SO IT NEEDS THE INTERNET, and it says so first, using the same probe crt-fetch
# uses (_reachable, above) — and it hands the answer to the job rather than letting
# the job probe again, so one command means one probe.

# How often the progress printer is allowed to speak while a source grinds through
# a thousand tiles. Every status CHANGE prints regardless; this only throttles the
# "still going" line, because a terminal scrolling at six lines a second is a
# terminal nobody reads the failures out of.
_PROGRESS_GAP_S = 2.0


def _print_sources(snap: dict) -> None:
    """The per-source table. NOT one percentage — see nav/service.py's FETCH_SOURCES.

    "charts done, imagery failed" tells an operator to drive anyway and expect a
    blank background. "73%" tells them nothing they can act on, and 73% of an
    imagery pyramid and 73% of the hazard layers are not remotely the same news.
    """
    for key in snap.get("order") or sorted(snap.get("sources") or {}):
        s = (snap.get("sources") or {}).get(key) or {}
        n, of = s.get("done"), s.get("total")
        count = f"{n}/{of}" if (n is not None and of) else ""
        line = f"  {key:<11} {s.get('status', '?'):<8} {count:>10}  {s.get('detail', '')}"
        print(line.rstrip())
        if s.get("why"):
            print(f"  {'':<11} {'':<8} {'':>10}  {s['why']}")


def _area_fetch(args) -> int:
    """Create/complete one offline area: centreline + hazard charts + imagery."""
    try:
        from . import service as svcmod
    except Exception as exc:  # noqa: BLE001 — reported, never raised
        print(f"error: api/nav/service.py could not be imported ({_brief(exc)}). This "
              f"command drives the same job the API serves, so it needs the API's "
              f"dependencies (fastapi, pydantic) installed.", file=sys.stderr)
        return 2

    print("area-fetch: everything one offline area needs, downloaded in one go")
    print("  BOOTSTRAP-time (§3). The waterway centreline, the Canal & River Trust")
    print("  hazard layers and the satellite imagery — in that order, so a hotspot")
    print("  that dies half way leaves you the two that keep the sub out of a")
    print("  culvert and loses only the picture. There is no internet at the canal.\n")

    lat = lon = None
    if args.at:
        try:
            lat, lon = (float(v) for v in args.at.replace(" ", "").split(","))
        except ValueError:
            print("error: --at must be LAT,LON in degrees, e.g. --at 52.4785,-1.9105",
                  file=sys.stderr)
            return 2
    name = args.name or args.area
    meta = svcmod._area_meta(areamod.slugify(name)) if name else None
    if meta is None:
        # NO AREA BY THAT NAME YET, so nav/areas.py defines one. It owns the radius,
        # the reuse rule and both caps — and its plan_area/create_area is what the
        # console calls when a launch point is tapped, so a card filled from this
        # terminal and one filled at the water are the same card. `plan` is printed
        # first because a refusal here is a sentence to read, not a traceback.
        if lat is None:
            print("error: name an area that exists, or give a launch point with "
                  "--at LAT,LON. An offline area needs to know where it is.",
                  file=sys.stderr)
            return 2
        plan = areamod.plan_area(lat, lon, radius_m=args.radius_m, name=name,
                                 detail=args.detail or "standard")
        print(f"plan     : {plan['action']} — {plan['why']}")
        if plan["action"] == "refuse":
            print("\nnothing was created and nothing was fetched.", file=sys.stderr)
            return 2
        if args.dry_run:
            print("\n--dry-run: no area was created and nothing was fetched.")
            return 0
        try:
            plan = areamod.create_area(lat, lon, radius_m=args.radius_m, name=name,
                                       detail=args.detail or "standard")
        except ValueError as exc:
            print(f"\nerror: {exc}", file=sys.stderr)
            return 2
        name = plan["name"]
        meta = svcmod._area_meta(name) or {}
    else:
        name = meta["name"]
    bbox = meta.get("bbox")
    if not bbox:
        print(f"error: area {name!r} has no usable bbox, so there is no box to fetch. "
              f"Nothing here will guess one.", file=sys.stderr)
        return 2
    if args.detail is None:
        # Not asked for, so keep whatever pyramid this area already has — mixing
        # zoom ranges into one archive leaves the completeness check counting tiles
        # against a range no run ever fetched.
        zmin = int(meta.get("minzoom") or settings.sat_min_zoom)
        zmax = int(meta.get("maxzoom") or settings.sat_max_zoom)
    else:
        zmin, zmax = areamod.zooms_for(args.detail)
    cap = svcmod.fetch_cap(bbox, zmin, zmax)

    print(f"area     : {name}" + (f"  ({meta['label']})" if meta.get("label") else ""))
    print(f"store    : {settings.areas_dir / name}.mbtiles  +  {settings.crt_dir / name}/")
    print(f"bbox     : {bbox[0]:.5f},{bbox[1]:.5f} .. {bbox[2]:.5f},{bbox[3]:.5f}")
    o = meta.get("origin") or {}
    if o.get("lat") is not None:
        print(f"launch   : {o['lat']:.5f},{o['lon']:.5f}  "
              f"(a {float(o.get('radius_m') or settings.area_radius_m):.0f} m box around it)")
    # THE CAP, BEFORE ANYTHING IS DOWNLOADED. An operator who taps a launch point on
    # a metered hotspot is entitled to see the number first, not to discover it in
    # their data bill.
    print(f"size     : {cap['tiles']} tiles ~{cap['mb']} MB at z{zmin}-{zmax}  "
          f"(cap {cap['tile_cap']} tiles ~{cap['mb_cap']} MB)")
    if not cap["within"]:
        print(f"\nrefused: {cap['title']}", file=sys.stderr)
        return 2

    before = svcmod.area_completeness(name)
    print(f"state    : {before.get('state')} — {before['title']}")
    print("\non this card already (read off the disk):")
    for key in [k for k, _, _ in svcmod.FETCH_SOURCES]:
        src = before["sources"].get(key) or {}
        print(f"  {key:<11} {src.get('status', '?')}")
    if args.dry_run:
        print("\n--dry-run: nothing was fetched and nothing was written.")
        return 0

    return asyncio.run(_area_fetch_run(svcmod, name, bbox, zmin, zmax,
                                       (meta.get("origin") or {}).get("radius_m"),
                                       bool(args.refresh)))


async def _area_fetch_run(svcmod, name: str, bbox, zmin: int, zmax: int,
                          radius, refresh: bool) -> int:
    # ONE PROBE PER COMMAND. internet_available() is the service's gate and it is
    # nothing but a call to _reachable above; resolving it here and handing the
    # answer to the job means the operator sees the verdict in the preflight, where
    # the rest of this file puts it, and the job does not go and ask again.
    ok, why = await svcmod.internet_available()
    print(f"\ninternet : {('available — ' + why) if ok else ('UNAVAILABLE — ' + why)}")
    if not ok:
        print("\nnothing was fetched. What is on this card is unchanged, and an ABSENT")
        print("layer is not an empty one — nothing above claims this water is clear.")
        return 2

    last: dict[str, tuple[str, float]] = {}

    async def say(snap: dict) -> None:
        # Persisted on every step, not only at the end. The area's own metadata is
        # what the NEXT process reads, and a fetch killed by Ctrl-C at the bank has
        # to leave a record saying it was killed — otherwise the console shows a
        # download that is not running and nobody is coming back to finish.
        svcmod._record_fetch(name, snap)
        now = time.monotonic()
        for key, s in (snap.get("sources") or {}).items():
            st = s["status"]
            if st == "pending":
                continue
            was, at = last.get(key, (None, 0.0))
            if st == was:
                # A SOURCE THAT HAS FINISHED SAYS SO ONCE. The first version of this
                # throttled on time alone, so every finished source re-announced
                # itself every two seconds for the rest of the job — a terminal in
                # which "centreline done" scrolled thirteen times while the imagery
                # ran, and the one line that mattered (a failure) had to be found
                # among them. Only a RUNNING source ticks.
                if st != "running" or (now - at) < _PROGRESS_GAP_S:
                    continue
            last[key] = (st, now)
            n, of = s.get("done"), s.get("total")
            count = f"{n}/{of}" if (n is not None and of) else ""
            print(f"  {key:<11} {st:<8} {count:>10}  {s.get('detail', '')}".rstrip(),
                  flush=True)

    print("\nfetching (sequential and rate-limited — these are free public services):")
    job = svcmod.AreaFetch(name, bbox, zmin, zmax, refresh=refresh, radius_m=radius,
                           reason="python -m nav.cli area-fetch", on_change=say)
    try:
        snap = await job.run(net=(ok, why))
    except KeyboardInterrupt:
        job.crash(KeyboardInterrupt("stopped at the keyboard"))
        snap = job.snapshot()
        print("\nstopped. What landed is on the card; the rest is not.", file=sys.stderr)
    # THE FINAL STATE, WRITTEN BEFORE ANYTHING IS ASKED ABOUT THE CARD. Progress is
    # persisted by the callback above, so the last thing on disk while the job was
    # alive says DOWNLOADING — and area_completeness() reads that and quite correctly
    # refuses to call an area complete while a download is in flight. Miss this line
    # and a fetch in which every single source succeeded reports "a fetch still
    # downloading" and exits non-zero. It did, on the first real run of this command;
    # the server path had the equivalent line in NavService._fetch_ended and this one
    # did not.
    svcmod._record_fetch(name, snap)

    print("\nwhat each source did:")
    _print_sources(snap)

    # THE VERDICT IS READ OFF THE DISK, not off the job's own report — the same rule
    # crt-fetch follows for the same reason. A driver that printed its own optimism
    # would be the one place in this chain where a layer that never landed looks
    # fetched.
    after = svcmod.area_completeness(name)
    print(f"\non this card now:")
    for key in [k for k, _, _ in svcmod.FETCH_SOURCES]:
        s = after["sources"].get(key) or {}
        print(f"  {key:<11} {s.get('status', '?'):<10} {s.get('title', '')}")
    print(f"\n{after['title']}")
    print(f"activate  : POST /api/areas/{name}/activate")
    print(f"check     : GET  /api/areas/{name}/complete")
    return 0 if after["complete"] else 1


# --- soundings --------------------------------------------------------------
# A pass-through, on purpose and by invitation: nav/soundings.py's own docstring
# specifies this wiring ("main(argv) parses its own argv and returns an exit code,
# exactly like calibrate.main"), and that module is the one that gets to decide
# what a sounding is — a depth is only evidence about the BED when the journal
# shows the sub arriving on something solid, and that rule has no business being
# reimplemented, or second-guessed, in a command-line front end.
#
# The one thing added here is choosing the area when the operator did not, which
# is the question they cannot answer at the bank: an area on the card has a name
# somebody typed weeks ago, and the dive knows where it started.


def _area_for_journal(journal: Path) -> tuple[list[str], list[str], str]:
    """Which known areas' bounding boxes contain this dive's launch point.

    Ambiguity is refused by the caller rather than resolved. Two overlapping areas
    mean the soundings could go into the wrong store, and a sounding filed against
    the wrong stretch of canal is worse than no sounding: it is a measurement of
    somewhere else, in the file that will one day be the only record of this bed.
    """
    try:
        header, _rows = read_dive(journal)
    except Exception as exc:  # noqa: BLE001
        return [], [], f"the journal header could not be read ({_brief(exc)})"
    o = (header or {}).get("origin") or {}
    try:
        lat, lon = float(o["lat"]), float(o["lon"])
    except (KeyError, TypeError, ValueError):
        return [], [], "this journal's header carries no launch point"
    inside, names = [], []
    for p in sorted(settings.areas_dir.glob("*.json")):
        names.append(p.stem)
        try:
            bb = json.loads(p.read_text(encoding="utf-8")).get("bbox")
        except Exception:  # noqa: BLE001
            continue
        if bb and len(bb) == 4 and bb[0] <= lon <= bb[2] and bb[1] <= lat <= bb[3]:
            inside.append(p.stem)
    return inside, names, f"launch point {lat:.5f}, {lon:.5f}"


def _soundings(args) -> int:
    """Dive journal -> the area's sounding store. Offline: no internet is involved."""
    try:
        from .soundings import main as _snd, store_path_for
    except ImportError as exc:
        print(f"error: api/nav/soundings.py is not in this build ({exc})",
              file=sys.stderr)
        return 2

    if args.selftest:
        return _snd(["--selftest"])
    if not args.dive:
        print("error: give a dive .jsonl journal, or --selftest", file=sys.stderr)
        return 2

    area = args.area
    if not area and not args.centreline:
        inside, names, how = _area_for_journal(Path(args.dive))
        if len(inside) == 1:
            area = inside[0]
            print(f"area     : {area}  (chosen: this dive's {how} is inside its bbox "
                  f"and no other area's)")
        elif not inside:
            print(f"error: no area on this card has a bounding box containing this "
                  f"dive's {how}. Pass --area <name> or --centreline <file>.",
                  file=sys.stderr)
            print(f"  known : {', '.join(names) if names else '(no areas on this card)'}",
                  file=sys.stderr)
            return 2
        else:
            print(f"error: this dive's {how} falls inside {len(inside)} areas "
                  f"({', '.join(inside)}), so the store cannot be chosen for you. "
                  f"Pass --area.", file=sys.stderr)
            return 2

    argv2 = [args.dive]
    if area:
        argv2 += ["--area", area]
    if args.centreline:
        argv2 += ["--centreline", args.centreline]
    if args.store:
        argv2 += ["--store", args.store]
    if args.cell_m is not None:
        argv2 += ["--cell-m", str(args.cell_m)]
    if args.dry_run:
        argv2.append("--dry-run")
    if args.json:
        argv2.append("--json")
    rc = _snd(argv2)
    # Not printed under --json: that output is somebody's stdin, and a friendly
    # trailer on the end of a JSON document is a parse error with a helpful tone.
    if not args.json and rc == 0 and area and not args.store:
        print(f"store    : {store_path_for(area)}")
        print(f"serve it : GET /api/areas/{area}/depth/surveyed")
    return rc


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
    # A CONSOLE THAT CANNOT SPELL A CHARACTER MUST NOT KILL THE COMMAND. Measured
    # on the ROG Ally, 2026-08-07: `crt-fetch` downloaded all 26 CRT layers for an
    # area, wrote them, and then died with UnicodeEncodeError on the LAST thing it
    # prints — a Trust licence string carrying U+FFFD, which cp1252 has no
    # character for. A good fetch reported itself as a crash, and the exit code
    # said the download had failed when 721 features were sitting on the card.
    # Every string that arrives from off this vehicle (licence text, service
    # names, exception messages) can do this, so it is fixed once here rather than
    # guarded at each print. 'replace' loses a glyph; strict loses the run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001 — not a TextIOWrapper (a pipe under test, say)
            pass
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
    af = sub.add_parser("area-fetch",
                        help="create/complete one offline area — centreline, CRT "
                             "hazard layers and satellite imagery "
                             "(BOOTSTRAP-time: needs internet)")
    af.add_argument("area", nargs="?",
                    help="an area name; with --at, an existing area covering that "
                         "point is used instead of making a second one")
    af.add_argument("--at", default=None,
                    help="LAT,LON launch point — creates the area around it if none "
                         "covers it yet")
    af.add_argument("--name", default=None, help="name the area explicitly")
    # The default is NOT quoted here. It lives in nav/service.py beside the job that
    # uses it, and a number copied into a help string is a number that will one day
    # advertise a cap the code no longer applies. The command prints the effective
    # radius, the tile count and the ceiling in its preflight, before it fetches.
    af.add_argument("--radius-m", type=float, default=None,
                    help="half-width of the box around --at, in metres. The preflight "
                         "prints the value used, the resulting tile count and the cap")
    af.add_argument("--detail", choices=["standard", "high"], default=None,
                    help="'high' adds one zoom level, which roughly quadruples the "
                         "tiles. Omitted means: keep the detail this area already has")
    af.add_argument("--refresh", action="store_true",
                    help="re-download sources that are already on the card "
                         "(by default they are skipped and nothing is re-requested)")
    af.add_argument("--dry-run", action="store_true",
                    help="report what is on the card and fetch nothing")
    cf = sub.add_parser("crt-fetch",
                        help="download the CRT hazard layers for an area "
                             "(BOOTSTRAP-time: needs internet)")
    cf.add_argument("area", nargs="?", help="an area already on this card (see /api/areas)")
    cf.add_argument("--bbox", default=None,
                    help="W,S,E,N in degrees, overriding the area's own bbox")
    cf.add_argument("--list", action="store_true",
                    help="list the layers the Trust publishes and fetch nothing")
    so = sub.add_parser("soundings",
                        help="extract bed soundings from a dive journal into an "
                             "area's sounding store (offline)")
    so.add_argument("dive", nargs="?", help="path to a dive .jsonl journal")
    so.add_argument("--area", default=None,
                    help="which area's store and centreline (default: the area whose "
                         "bbox contains the dive's launch point; refused if ambiguous)")
    so.add_argument("--centreline", default=None,
                    help="explicit centreline GeoJSON, overriding --area")
    so.add_argument("--store", default=None,
                    help="sounding store to accumulate into "
                         "(default: data/soundings/<area>.json)")
    so.add_argument("--cell-m", type=float, default=None,
                    help="cell length along the channel axis, 5-10 m")
    so.add_argument("--dry-run", action="store_true",
                    help="report what would be stored and write nothing")
    so.add_argument("--json", action="store_true", help="machine-readable output")
    so.add_argument("--selftest", action="store_true",
                    help="check the sounding maths and its refusals")
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
    if args.cmd == "area-fetch": return _area_fetch(args)
    if args.cmd == "crt-fetch":  return _crt_fetch(args)
    if args.cmd == "soundings":  return _soundings(args)
    if args.cmd == "mag-cal":    return _mag_cal(args)
    if args.cmd == "state":      return _get(args, "/api/nav/state")
    if args.cmd == "readiness":  return _get(args, "/api/readiness")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
