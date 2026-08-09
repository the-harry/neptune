"""Post-dive soundings — make the sub its own depth surveyor.

    python -m nav.soundings data/dives/dive-20260806-141233.jsonl --area cowley
    python -m nav.soundings <journal> --centreline data/areas/cowley.geojson --dry-run
    python -m nav.soundings --selftest          # prove the maths and the refusals

WHY THIS EXISTS
    There is no canal bathymetry to download. i-Boating's UK layer is proprietary
    UKHO-derived coastal data with no canal soundings and a licence forbidding reuse,
    the Environment Agency's multibeam is estuarine, EA LIDAR cannot see through
    water, and the Canal & River Trust's own hydrographic surveys are internal. The
    sub surveys its own canal or nobody does. Every dive already carries a measured
    depth and a position; this turns the ones that touched the bottom into a map.

WHAT A SOUNDING IN HERE IS, AND WHAT IT IS NOT
    The MS5837 measures the depth of the SUB. Nothing aboard measures the depth of
    the BED — there is no echosounder and no altimeter. So a depth sample is only
    evidence about the bed when there is evidence the sub was ON the bed, and even
    then the number is a LOWER BOUND:

      * the pressure port sits above the keel, so a landed sub reads short of the
        bed by its own draft;
      * it may have landed on silt, weed, a sunken trolley or a fallen branch, all
        of which stop it above the hard bottom;
      * canal levels move with rainfall and lock use, so the surface it is measured
        from is the surface of THAT DAY. There is no vertical datum here.

    Every one of those errors has the same sign. The bed is AT LEAST this deep and
    may be deeper — which is why the quantity is named lower_bound_m in the store
    and on disk, why each cell also carries bound="lower", and why the reports shout it.
    A lower bound is the shape of answer that is safe to be wrong in: it under-
    promises clearance. Dressed up as a measurement it would do the opposite.

    THE TEMPTATION THIS FILE REFUSES. Every depth sample is technically a lower
    bound on the bed beneath it — if the sub was at 1.2 m and floating free, the bed
    there is at least 1.2 m. Binning all of them would produce a full, plausible,
    technically-true map that would be read as "the canal is 1.2 m here" when it is
    2.5 m. A bound is only worth recording when it is TIGHT, and contact with the
    bottom is the only thing aboard that makes it tight. So a sample counts only
    when the journal shows the sub arriving on something solid.

THE EVIDENCE OF BOTTOM CONTACT, AND WHY IT IS THIS ONE
    The sub's only vertical control is a syringe. Filling it makes the sub heavier;
    heavier means it sinks. So:

        the sub was descending, then STOPPED descending while the syringe was
        STILL TAKING ON WATER

    is contact. Something that is not buoyancy is holding it up, and the only thing
    down there is the bed. The awkward twin — the sub reaching neutral buoyancy and
    hovering — is exactly what the "still filling" clause excludes, because a
    hovering sub that is handed more ballast has to go down.

    WHAT WAS CONSIDERED AND REJECTED, so nobody re-adds it:
      * depth simply flat: that is the ordinary settled hold nav/calibrate.py fits
        the ballast->depth curve against. It is equilibrium, not the bed.
      * ballast at full and depth flat: still ambiguous. "Stopped sinking" and
        "landed" are different claims and this cannot separate them.
      * snagged: high thrust with no measured speed means the sub is pinned on
        SOMETHING — a lock gate or a bridge pier at mid-water pins it just as well
        as the bed does, and the depth then belongs to the obstruction.
      * a static pitch/roll offset: real when a hull settles on a slope, but the
        trim swings with the ballast anyway (docs/hardware.md §12), so it cannot
        carry the claim on its own.
      * accel_fwd_ms2 spikes: a bump is a bump. A wall is also a bump.

WHY LONGITUDINAL CELLS AND NOT A GRID
    Position error here runs to metres and in places exceeds the canal's half-width,
    so an (x, y) raster would draw cross-channel structure that the navigation
    cannot support — false precision, and the prettier it looks the more it lies.
    A canal is a 1-D object: samples are projected onto the CRT centreline with
    nav/snap.py (the one projection in this codebase) and binned by distance ALONG
    the channel, in cells of 5-10 m. Cross-track information is deliberately thrown
    away rather than invented, and the cross-track distance survives as provenance.

WHY MAX AND NOT MEAN
    Each cell keeps the DEEPEST contact depth seen in it. For a lower bound that is
    the most the cell knows: averaging a 1.4 m touchdown with a 0.6 m touchdown on
    the same trolley produces 1.0 m, a number nothing measured and a bound that is
    weaker than one already in hand.

WIRING (for whoever owns nav/cli.py)
    main(argv) parses its own argv and returns an exit code, exactly like
    calibrate.main. The whole integration is:

        from .soundings import main as _soundings
        ...
        sd = sub.add_parser("soundings", help="extract bed soundings from a dive log")
        sd.add_argument("dive", nargs="?"); sd.add_argument("--area")
        sd.add_argument("--centreline"); sd.add_argument("--store")
        sd.add_argument("--cell-m", type=float); sd.add_argument("--dry-run", action="store_true")
        sd.add_argument("--json", action="store_true"); sd.add_argument("--selftest", action="store_true")
        ...
        if args.cmd == "soundings": return _soundings(argv_for_soundings)

    or simply hand the raw argv through: main(["<journal>", "--area", "cowley"]).
    Nothing in here touches the network or resolves a hostname: the centreline is
    read from the area file that BOOTSTRAP downloaded, and a missing one is reported
    ABSENT rather than fetched. It is safe to run canal-side with no internet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path

# read_dive and _column_state are IMPORTED rather than copied. There is one journal
# reader in this codebase (it already tolerates the truncated final line a killed
# process leaves) and one definition of absent/silent/partial for a measured column;
# a second copy of either would drift, and the drift would show up as two tools
# disagreeing about whether a sensor was fitted. calibrate.py is under the hardware
# freeze so it cannot be edited to export them under public names — importing a name
# from a frozen file is not editing it.
from .calibrate import _column_state, read_dive
from .config import settings
from .geo import to_latlon, to_local
from .snap import nearest_on_polyline

SCHEMA = "neptune.soundings/1"

# The name of the quantity, carried in the store header AND in every cell's field
# name. It travels with the number because the number is meaningless without it,
# and it is one name rather than two: api/tests/test_soundings.py and the console's
# SURVEYED DEPTH layer both read this key, and a second spelling of the same figure
# is how two files start disagreeing about what they are showing.
QUANTITY = "lower_bound_m"

MEANS = (
    "Each cell holds the DEEPEST depth the sub itself reached while the journal "
    "showed it resting on something solid. The bed is AT LEAST this deep and may "
    "be deeper: the pressure port sits above the keel, the sub may have landed on "
    "silt or debris, and nothing aboard measures the bed directly. This is a lower "
    "bound on bed depth, not a measurement of it, and it must never be drawn as one."
)

UNSURVEYED = (
    "Cells absent from this file are UNSURVEYED — the sub has never left "
    "bottom evidence there. Absent is not shallow, and it is not zero. A "
    "renderer must draw absence as absence."
)

DATUM = (
    "the water surface as it was on the day of each dive. Canal levels move with "
    "rainfall and lock use, so there is no vertical datum here and two dives a "
    "month apart can disagree by a hand's width. Each cell records which dive its "
    "deepest sounding came from, and when."
)

# ---- what counts as a touchdown ------------------------------------------------
# 5-10 m along the channel. 8 m is a little over four sub-lengths and comfortably
# wider than the along-track error of a short dive; finer cells would imply a
# longitudinal precision the dead reckoner does not have.
CELL_M_DEFAULT = 8.0
CELL_M_MIN, CELL_M_MAX = 5.0, 10.0

# The depth must hold still for this long. Shorter and a slow sink through the
# tolerance band is indistinguishable from a landing.
MIN_CONTACT_S = 3.0
# ...and over at least this many samples, so an unexpectedly slow log rate cannot
# turn two readings into a "steady" stretch.
MIN_CONTACT_SAMPLES = 8
# "Held still" band. Same 0.05 m calibrate.py calls settled, on purpose: the two
# tools must not disagree about whether a hold was a hold.
DEPTH_FLAT_M = 0.05
# How much of the calibrated stroke must go IN during the hold before it counts as
# evidence rather than equilibrium. A tenth of the stroke is about a second of
# pumping (docs/hardware.md §8.3: 4000 steps at 400 steps/s), far more ballast than
# a hovering sub can absorb without sinking.
#
# THIS NUMBER IS A PLACEHOLDER, like every other pre-hardware constant in this
# project — nobody has yet watched a real hull land. If the first dives show
# equilibrium holds being reported as touchdowns, raise it; if genuine landings are
# being missed because the fill finished before the sub arrived, the fix is the
# procedure (arrive with the fill still running), not a lower threshold. Lowering
# it trades the one thing this file exists to protect.
MIN_FILL_RISE = 0.10
# A landing must be preceded by an actual descent, within this long. This is what
# separates the bed from the surface: a sub floating with the syringe filling has
# not descended, and its flat 0.0 m is buoyancy, not a bottom.
MIN_DESCENT_M = 0.3
DESCENT_LOOKBACK_S = 20.0
# Shallower than this and it is the sub sitting on the surface with a bit of noise.
MIN_SOUNDING_DEPTH_M = 0.3

# Beyond this from the centreline the sample is not in this channel at all — a
# marina arm, a lock chamber, or a track that has drifted off the map. Binning it
# longitudinally would file a real sounding under the wrong stretch of canal, which
# is worse than dropping it, so it is dropped and counted. Defaulted from the
# estimator's own snapping limit so the two agree about what "on this waterway" means.
MAX_OFFLINE_M = settings.snap_max_dist_m


def soundings_dir() -> Path:
    """Where per-area stores live. Derived from NAV_DATA_DIR, created on demand."""
    return settings.data_dir / "soundings"


def store_path_for(area: str) -> Path:
    return soundings_dir() / f"{area}.json"


# ==========================================================================
# The channel axis
# ==========================================================================
@dataclass(frozen=True)
class Centreline:
    """The CRT waterway centreline for one area, in [lon,lat] and in local metres.

    LINES ARE KEPT SEPARATE. service.py flattens a MultiLineString into a single
    polyline, which is right for "how far am I from the water" and wrong here: the
    join between two disjoint ways becomes a phantom segment, and distance-ALONG
    measured through it is a distance nothing travelled. Every cell index in the
    store is a distance along one specific line, so the lines cannot be merged.
    """

    name: str
    source: str  # the file it was read from
    lines_lonlat: list[list[tuple[float, float]]]
    lines_local: list[list[tuple[float, float]]]  # metres about (ref_lat, ref_lon)
    ref_lat: float
    ref_lon: float
    fingerprint: str
    n_points: int
    length_m: float

    def meta(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "lines": len(self.lines_lonlat),
            "points": self.n_points,
            "length_m": round(self.length_m, 1),
            "source": self.source,
        }


def _lines_from_geojson(gj: dict) -> list[list[tuple[float, float]]]:
    """Every LineString in the file, each kept as its own polyline."""
    lines: list[list[tuple[float, float]]] = []

    def walk(g):
        if not isinstance(g, dict):
            return
        t = g.get("type")
        if t == "LineString":
            lines.append([(c[0], c[1]) for c in g.get("coordinates", [])])
        elif t == "MultiLineString":
            for ln in g.get("coordinates", []):
                lines.append([(c[0], c[1]) for c in ln])
        elif t == "Feature":
            walk(g.get("geometry") or {})
        elif t == "FeatureCollection":
            for f in g.get("features", []):
                walk(f)

    walk(gj)
    return [ln for ln in lines if len(ln) >= 2]


def load_centreline(path: Path, name: str | None = None) -> tuple[Centreline | None, str | None]:
    """Read an area's waterway centreline. (Centreline, None) or (None, why).

    ABSENT IS AN ANSWER AND IT IS NOT "EMPTY". Canal-side there is no internet and
    no hostname resolution, so a missing centreline cannot be fetched and must not
    be papered over: without a channel axis there is nothing to bin along, and a
    store written from a guessed axis would be silently misfiled forever. The
    centreline is a BOOTSTRAP-time download (nav/satellite.fetch_centreline); this
    runtime path only ever reads it off the disk.
    """
    path = Path(path)
    name = name or path.stem
    if not path.exists():
        return None, (
            f"the waterway centreline for '{name}' is ABSENT: {path} does not "
            f"exist. It is downloaded at BOOTSTRAP with the area (there is no "
            f"internet canal-side, and nothing here will try). Without a channel "
            f"axis there is no distance-along to bin soundings by, so none are "
            f"derivable — which is not the same as this canal having no soundings."
        )
    try:
        gj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — a corrupt file must not look like an empty one
        return None, f"the centreline file {path} is present but unreadable ({exc})"
    lines = _lines_from_geojson(gj)
    if not lines:
        return None, (
            f"{path} holds no LineString or MultiLineString — it is present but "
            f"carries no channel axis, so it cannot say where along the canal "
            f"anything is"
        )

    ref_lon, ref_lat = lines[0][0]
    lines_local, length, npts = [], 0.0, 0
    for ln in lines:
        loc = [to_local(lat, lon, ref_lat, ref_lon) for (lon, lat) in ln]
        lines_local.append(loc)
        npts += len(loc)
        length += sum(math.hypot(loc[i][0] - loc[i - 1][0], loc[i][1] - loc[i - 1][1]) for i in range(1, len(loc)))

    # The fingerprint is what stops two different centrelines being accumulated into
    # one store. Cell 47 means "376-384 m along line 0 OF THIS GEOMETRY"; re-download
    # the area, get the ways back in a different order, and cell 47 is somewhere else
    # entirely while every old sounding still claims it. Rounded to 1e-7 deg (~1 cm)
    # so a re-serialised copy of the same geometry still matches.
    h = hashlib.sha1()
    for ln in lines:
        h.update(b"|")
        for lon, lat in ln:
            h.update(f"{lon:.7f},{lat:.7f};".encode())
    return (
        Centreline(
            name=name,
            source=str(path),
            lines_lonlat=lines,
            lines_local=lines_local,
            ref_lat=ref_lat,
            ref_lon=ref_lon,
            fingerprint=h.hexdigest()[:16],
            n_points=npts,
            length_m=length,
        ),
        None,
    )


def snap_to_axis(cl: Centreline, lat: float, lon: float):
    """(line_index, distance_along_m, distance_off_axis_m) for one lat/lon, or None.

    The projection itself is nav/snap.py's — there is exactly one point-to-polyline
    projection in this codebase and this is not a second one. All that happens here
    is picking the nearest of several disjoint lines.
    """
    x, y = to_local(lat, lon, cl.ref_lat, cl.ref_lon)
    best = None
    for i, loc in enumerate(cl.lines_local):
        near = nearest_on_polyline(x, y, loc)
        if near is None:
            continue
        _sx, _sy, off, along = near
        if best is None or off < best[2]:
            best = (i, along, off)
    return best


def _point_at_along(cl: Centreline, line_i: int, along_m: float) -> tuple[float, float]:
    """The lat/lon of a distance along one centreline — where a cell is DRAWN.

    Interpolation along a polyline, not a projection onto one, so snap.py has
    nothing to say about it. Clamped at both ends: a cell's midpoint can sit a
    little past the last vertex when the final cell is short.
    """
    loc = cl.lines_local[line_i]
    d = max(0.0, along_m)
    for i in range(1, len(loc)):
        ax, ay = loc[i - 1]
        bx, by = loc[i]
        seg = math.hypot(bx - ax, by - ay)
        if seg <= 0:
            continue
        if d <= seg:
            f = d / seg
            return to_latlon(ax + (bx - ax) * f, ay + (by - ay) * f, cl.ref_lat, cl.ref_lon)
        d -= seg
    ex, ey = loc[-1]
    return to_latlon(ex, ey, cl.ref_lat, cl.ref_lon)


# ==========================================================================
# Bottom evidence
# ==========================================================================
@dataclass
class ContactTally:
    """Why each candidate stretch was or was not a touchdown. Printed, never hidden."""

    flat_runs: int = 0
    too_short: int = 0
    no_fill: int = 0
    too_shallow: int = 0
    no_descent: int = 0
    contacts: int = 0
    contact_samples: int = 0
    # position rejections, filled in during binning
    no_position: int = 0
    held_position: int = 0
    off_channel: int = 0
    worst_off_m: float = 0.0
    binned: int = 0


def _flat_depth_runs(samples) -> list[list[dict]]:
    """Maximal stretches where the depth stopped moving and both channels answered.

    A null in EITHER channel ends the run rather than being stepped over, for the
    reason calibrate._null_free spells out: this is a claim about a whole stretch of
    time, and a stretch the instrument was absent for is not one anybody watched.
    A hole with the ends closed up would read as one continuous hold on the bed
    while the sub was, for all this file knows, halfway to the surface.
    """
    runs, cur = [], []
    for s in samples:
        d, b = s.get("depth_m"), s.get("ballast")
        if d is None or b is None:
            if len(cur) >= MIN_CONTACT_SAMPLES:
                runs.append(cur)
            cur = []
            continue
        if cur and abs(d - cur[0]["depth_m"]) > DEPTH_FLAT_M:
            if len(cur) >= MIN_CONTACT_SAMPLES:
                runs.append(cur)
            cur = []
        cur.append(s)
    if len(cur) >= MIN_CONTACT_SAMPLES:
        runs.append(cur)
    return runs


def _fill_rise(run) -> float:
    """The most ballast the syringe took ON during the run, without letting it out first.

    Last-minus-first would be fooled by a run that empties and then refills; the
    running minimum asks the question that matters — at any moment in this hold, how
    much more water was aboard than at the lightest the sub had been so far.
    """
    lo, rise = run[0]["ballast"], 0.0
    for s in run:
        b = s["ballast"]
        lo = min(lo, b)
        rise = max(rise, b - lo)
    return rise


def _descended_into(samples, run) -> bool:
    """Was the sub on its way DOWN before it stopped? Surface float vs bottom landing.

    Without this, a sub bobbing at the surface with the syringe filling — depth flat
    near zero, ballast climbing — is a textbook 'contact': the whole rule reads as
    satisfied while the hull is in the air. The descent is what makes 'it stopped'
    mean 'something stopped it'.
    """
    t0, d0 = run[0]["t"], run[0]["depth_m"]
    prior = [s["depth_m"] for s in samples if s.get("depth_m") is not None and t0 - DESCENT_LOOKBACK_S <= s["t"] < t0]
    return bool(prior) and (d0 - min(prior)) >= MIN_DESCENT_M


def contact_runs(samples) -> tuple[list[list[dict]], ContactTally]:
    """The stretches this journal shows the sub resting on the bottom.

    Deliberately NOT gated on `armed`, unlike calibrate's segments: calibrate is
    measuring propulsion, so a disarmed sample proves nothing to it. A sounding is a
    pressure reading taken while the hull is on the bed, and the thrusters have
    nothing to do with it — gating on armed would throw away exactly the quiet
    minutes when the sub is sitting still on the bottom doing the work it is for.
    """
    tally = ContactTally()
    out = []
    for run in _flat_depth_runs(samples):
        tally.flat_runs += 1
        if (run[-1]["t"] - run[0]["t"]) < MIN_CONTACT_S:
            tally.too_short += 1
            continue
        if _fill_rise(run) < MIN_FILL_RISE:
            tally.no_fill += 1
            continue
        if min(s["depth_m"] for s in run) < MIN_SOUNDING_DEPTH_M:
            tally.too_shallow += 1
            continue
        if not _descended_into(samples, run):
            tally.no_descent += 1
            continue
        tally.contacts += 1
        tally.contact_samples += len(run)
        out.append(run)
    return out, tally


def _why_no_contact(samples, tally: ContactTally) -> str:
    """The refusal sentence, in calibrate.py's voice: name the failure, not the shrug.

    A tool that always produces an answer is worse than no tool. Each rung below
    sends an operator to a DIFFERENT job — fit the part, home the syringe, fly the
    dive differently — and collapsing them into "no soundings found" sends them to
    the wrong one, which is the same class of harm as inventing a number.
    """
    if not samples:
        return "this journal has no samples at all — nothing was ever logged"

    dstate = _column_state(samples, "depth_m")
    if dstate == "absent":
        return (
            "this journal has no depth_m column at all — it predates depth logging, "
            "so there is no depth in it to be a sounding (the MS5837 may well have "
            "been fitted and fine; nothing here recorded it)"
        )
    if dstate == "silent":
        return (
            "the depth_m column is present and null on every sample — the pressure "
            "sensor never answered once in this dive, so nothing here measured a "
            "depth and no sounding can come out of it"
        )

    bstate = _column_state(samples, "ballast")
    if bstate == "absent":
        return (
            "this journal has no ballast column at all — it carries the estimator's "
            "conclusions but not the raw control channels (divelog.py only writes "
            "them when a SensorSample is logged beside the NavState), so it predates "
            "or was written without them. Its depths are real and are not lost; there "
            "is simply nothing in it that can tell the sub landing on the bed from the "
            "sub hanging at neutral buoyancy, and those are the two readings a flat "
            "depth has"
        )
    if bstate == "silent":
        return (
            "the ballast column is present and null on every sample — the stepper was "
            "never homed, so the syringe had no position to report all dive (there is "
            "no position sensor on it). Bottom contact is recognised by the sub "
            "stopping WHILE STILL TAKING ON WATER, and this dive has no record of "
            "water going in. Run ballast_home() before the next dive"
        )

    if tally.flat_runs == 0:
        return (
            "the depth never held still for long enough in this dive — the sub was "
            "moving vertically throughout, so it was never resting on anything"
        )
    # Each rung below is a stretch that ALMOST qualified, and each names a different
    # next move. They are tested for being non-zero rather than for adding up to the
    # total: a tally that happens to balance is not a diagnosis, and an arithmetic
    # coincidence must never be allowed to print "the sub stopped 0 times".
    if tally.no_fill:
        return (
            f"the depth went flat {tally.no_fill} time(s), and not once while the "
            f"syringe was still filling. A depth that simply goes flat is the settled "
            f"hold nav/calibrate.py fits the ballast curve to — it is neutral buoyancy, "
            f"and neutral buoyancy happens at any depth. To leave bottom evidence, "
            f"arrive on the bed with the fill still running rather than filling, "
            f"waiting, and sinking after the syringe is done"
        )
    if tally.too_shallow:
        return (
            f"{tally.too_shallow} stretch(es) held still under a filling syringe, but "
            f"all of them shallower than {MIN_SOUNDING_DEPTH_M} m — that is the sub "
            f"floating while it takes on water, not the sub landing on anything"
        )
    if tally.no_descent:
        return (
            f"{tally.no_descent} stretch(es) held still under a filling syringe, but "
            f"with no descent recorded in the {DESCENT_LOOKBACK_S:.0f} s before them. "
            f"Something stopping is only evidence when it was going somewhere; this "
            f"journal may simply start with the sub already down"
        )
    if tally.too_short:
        return (
            f"the depth held still {tally.too_short} time(s), every one of them for "
            f"less than {MIN_CONTACT_S:.0f} s. A hold that brief is not distinguishable "
            f"from a sub sinking slowly through the {DEPTH_FLAT_M} m band this calls flat"
        )
    return (
        f"no stretch of this dive shows the sub stopping while the syringe was still "
        f"filling ({tally.flat_runs} flat stretch(es) examined)"
    )


# ==========================================================================
# Extraction: one journal -> cells
# ==========================================================================
def _dive_id(header, path: Path) -> str:
    if header and header.get("dive_id"):
        return str(header["dive_id"])
    return path.stem


def _adjustment_for(journal: Path) -> tuple[tuple[float, float, float], str]:
    """The operator's post-hoc drag of this track (§4.5), which the JOURNAL does not carry.

    divelog.py stores raw local x/y in the .jsonl and applies the translate+rotate
    only when writing the .geojson, so a track that was dragged back onto the
    waterway is straight again in the file this tool reads. Ignoring that would put
    every sounding from that dive at the offset the operator had already corrected —
    and the whole reason they dragged it is that they knew where it really was. So
    the sibling .geojson is consulted for the adjustment and it is applied here.
    """
    sib = journal.with_suffix(".geojson")
    if not sib.exists():
        return (0.0, 0.0, 0.0), "none recorded (no sibling .geojson beside this journal)"
    try:
        pr = json.loads(sib.read_text(encoding="utf-8")).get("properties", {})
        a = pr.get("adjustment") or {}
        adj = (float(a.get("dx_m", 0.0)), float(a.get("dy_m", 0.0)), float(a.get("rotation_deg", 0.0)))
    except Exception as exc:  # noqa: BLE001
        return (0.0, 0.0, 0.0), f"sibling .geojson unreadable ({exc}) — raw track used"
    if adj == (0.0, 0.0, 0.0):
        return adj, "identity (the track was never dragged)"
    return adj, f"dx={adj[0]:.1f} m dy={adj[1]:.1f} m rot={adj[2]:.1f} deg, from {sib.name}"


def _apply_adjustment(x: float, y: float, adj) -> tuple[float, float]:
    """Exactly divelog.DiveLog._apply — translate + rotate, same order, same signs."""
    dx, dy, rot = adj
    th = math.radians(rot)
    c, s = math.cos(th), math.sin(th)
    return dx + x * c - y * s, dy + x * s + y * c


def extract_dive(
    journal: Path, cl: Centreline, cell_m: float = CELL_M_DEFAULT, max_offline_m: float = MAX_OFFLINE_M
) -> tuple[dict | None, str | None]:
    """One dive journal -> per-cell lower bounds. (result, None) or (None, why).

    Pure: reads the journal (and its sibling .geojson) and returns a dict. Writing
    is merge_dive() + save_store()'s job, so a caller can dry-run the whole thing.
    """
    journal = Path(journal)
    header, samples = read_dive(journal)
    dive_id = _dive_id(header, journal)

    # Emptiness is diagnosed before the origin: a journal from a process that died at
    # the header, or one truncated to nothing, has no origin EITHER, and "no origin
    # was set" would send someone to check a procedure that was followed.
    if not samples:
        return None, (
            f"{journal.name} contains no samples — either the dive never logged "
            f"one, or the file was truncated before the first"
        )

    origin = (header or {}).get("origin")
    if not origin or origin.get("lat") is None or origin.get("lon") is None:
        return None, (
            f"{journal.name} has no origin in its header — x/y in it are metres "
            f"from a datum this file does not carry, so nothing in it can be put "
            f"on the canal. The dive was logged before an origin was set"
        )

    # What each channel could supply, recorded even when the dive DOES yield soundings.
    # calibrate.py prints its gaps above the numbers for this reason: a survey fitted
    # to the stretch where the instrument happened to be alive, with no mention that
    # the rest existed, covers a different piece of canal from the one it appears to.
    columns = {k: _column_state(samples, k) for k in ("depth_m", "ballast", "confidence")}

    runs, tally = contact_runs(samples)
    if not runs:
        return None, _why_no_contact(samples, tally)

    adj, adj_note = _adjustment_for(journal)
    lat0, lon0 = float(origin["lat"]), float(origin["lon"])

    cells: dict[str, dict] = {}
    confs: list[float] = []
    snapped_n = 0
    for run in runs:
        seen_here: set[str] = set()  # a run counts once per cell, as one touchdown
        for s in run:
            x, y = s.get("x"), s.get("y")
            if x is None or y is None:
                tally.no_position += 1
                continue
            if s.get("no_heading"):
                # x/y are the last place the sub was TRACKED to, not where it is —
                # divelog writes no_heading exactly so a run of identical coordinates
                # cannot be read as a sub sitting still. A sounding filed at a held
                # position is filed wherever the compass died, which may be a long way
                # from where the hull actually landed.
                tally.held_position += 1
                continue
            ax, ay = _apply_adjustment(float(x), float(y), adj)
            lat, lon = to_latlon(ax, ay, lat0, lon0)
            near = snap_to_axis(cl, lat, lon)
            if near is None:
                tally.no_position += 1
                continue
            line_i, along, off = near
            tally.worst_off_m = max(tally.worst_off_m, off)
            if off > max_offline_m:
                tally.off_channel += 1
                continue
            tally.binned += 1
            if s.get("snapped"):
                # The estimator had already pulled this fix onto the centreline during
                # the dive, so the off-axis distance recorded below is what was left
                # AFTER that correction, not the raw position error. A store built
                # mostly from snapped samples looks better positioned than it is, and
                # the fraction is carried into the provenance so a reader can tell.
                snapped_n += 1

            depth = float(s["depth_m"])
            conf = s.get("confidence")
            if conf is not None:
                confs.append(float(conf))
            key = f"{line_i}:{int(along // cell_m)}"
            c = cells.get(key)
            if c is None:
                idx = int(along // cell_m)
                lo_m, hi_m = idx * cell_m, (idx + 1) * cell_m
                clat, clon = _point_at_along(cl, line_i, (lo_m + hi_m) / 2.0)
                # The cell as a piece of the channel, not a dot on it: three points
                # along the axis, so a renderer draws the stretch that was surveyed
                # rather than a marker somebody has to guess the extent of.
                geom = [
                    [round(lon_, 7), round(lat_, 7)]
                    for lat_, lon_ in (_point_at_along(cl, line_i, d) for d in (lo_m, (lo_m + hi_m) / 2.0, hi_m))
                ]
                c = cells[key] = {
                    "line": line_i,
                    "cell": idx,
                    "from_m": round(lo_m, 1),
                    "to_m": round(hi_m, 1),
                    "lat": round(clat, 7),
                    "lon": round(clon, 7),
                    "geom": geom,
                    QUANTITY: depth,
                    "bound": "lower",
                    "samples": 0,
                    "contacts": 0,
                    "confidence_min": None,
                    "confidence_sum": 0.0,
                    "confidence_n": 0,
                    "offset_m_max": 0.0,
                    "t_deepest": s["t"],
                    "confidence_at_deepest": conf,
                }
            c["samples"] += 1
            c["offset_m_max"] = max(c["offset_m_max"], round(off, 1))
            if conf is not None:
                c["confidence_sum"] += float(conf)
                c["confidence_n"] += 1
                c["confidence_min"] = (
                    float(conf) if c["confidence_min"] is None else min(c["confidence_min"], float(conf))
                )
            # MAX, not mean: the deepest thing the sub reached in this cell is the
            # most this cell knows, and averaging it with a shallower landing throws
            # away a bound already in hand.
            if depth > c[QUANTITY]:
                c[QUANTITY] = depth
                c["t_deepest"] = s["t"]
                c["confidence_at_deepest"] = conf
            if key not in seen_here:
                seen_here.add(key)
                c["contacts"] += 1

    if not cells:
        bits = []
        if tally.held_position:
            bits.append(
                f"{tally.held_position} while the compass was dead and the position "
                f"was being HELD (no_heading), so their coordinates are wherever the "
                f"track stopped rather than where the sub landed"
            )
        if tally.off_channel:
            bits.append(
                f"{tally.off_channel} more than {max_offline_m:.0f} m from the "
                f"'{cl.name}' centreline (worst {tally.worst_off_m:.0f} m) — that is "
                f"not this channel, and binning them along it would file real "
                f"soundings under the wrong stretch of canal"
            )
        if tally.no_position:
            bits.append(f"{tally.no_position} with no usable position at all")
        why = "; ".join(bits) or "no reason recorded"
        return None, (
            f"{tally.contact_samples} sample(s) DID show bottom contact, and every "
            f"one was discarded for position: {why}. The depths are real; this "
            f"journal cannot say where they were taken"
        )

    for c in cells.values():
        c[QUANTITY] = round(c[QUANTITY], 2)
        n = c.pop("confidence_n")
        ssum = c.pop("confidence_sum")
        c["confidence_mean"] = round(ssum / n, 3) if n else None
        if c["confidence_min"] is not None:
            c["confidence_min"] = round(c["confidence_min"], 3)

    return {
        "dive_id": dive_id,
        "journal": str(journal),
        "started_at": (header or {}).get("started_at"),
        "area": cl.name,
        "cell_length_m": cell_m,
        "centreline": cl.meta(),
        "origin": {"lat": lat0, "lon": lon0, "accuracy_m": origin.get("accuracy"), "source": origin.get("source")},
        "adjustment": {"dx_m": adj[0], "dy_m": adj[1], "rotation_deg": adj[2], "note": adj_note},
        "contacts": tally.contacts,
        "contact_samples": tally.contact_samples,
        "binned_samples": tally.binned,
        # Confidence is NOT defaulted to 1.0 when the journal never recorded it. 1.0
        # is "perfectly trusted", the strongest claim in the system, and a cell built
        # from a dive that never said would be indistinguishable from one built on a
        # good fix — which is the exact distinction this provenance exists to make.
        "confidence_mean": round(sum(confs) / len(confs), 3) if confs else None,
        "confidence_min": round(min(confs), 3) if confs else None,
        "confidence_recorded": bool(confs),
        "snapped_fraction": round(snapped_n / max(1, tally.binned), 3),
        "columns": columns,
        "rejected": {
            "held_position": tally.held_position,
            "off_channel": tally.off_channel,
            "no_position": tally.no_position,
        },
        "tally": {
            "flat_runs": tally.flat_runs,
            "too_short": tally.too_short,
            "no_fill": tally.no_fill,
            "too_shallow": tally.too_shallow,
            "no_descent": tally.no_descent,
        },
        "cells": cells,
    }, None


# ==========================================================================
# The per-area store: many dives, accumulating
# ==========================================================================
def new_store(area: str, cell_m: float, cl: Centreline) -> dict:
    return {
        "schema": SCHEMA,
        "area": area,
        "quantity": QUANTITY,
        "means": MEANS,
        "unsurveyed": UNSURVEYED,
        "datum": DATUM,
        "cell_length_m": cell_m,
        "centreline": cl.meta(),
        "updated_at": None,
        "dives": {},
        "cells": [],
    }


def load_store(path: Path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_store(path: Path, store: dict) -> Path:
    """Write the store, newest facts and all. Atomic: a half-written store is worse
    than none, and this file is the accumulated product of every dive ever flown."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    store["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store["cells"] = sorted(store["cells"], key=lambda c: (c["line"], c["cell"]))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(store, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _recompute(cell: dict) -> dict:
    """Roll a cell's per-dive contributions up into the numbers a map reads.

    Everything above per_dive is derived, and it is derived fresh every time, which
    is what makes re-running a dive idempotent instead of double-counting it.
    """
    per = cell["per_dive"]
    deepest_dive = max(per, key=lambda d: per[d][QUANTITY])
    cell[QUANTITY] = round(per[deepest_dive][QUANTITY], 2)
    cell["bound"] = "lower"
    cell["samples"] = sum(p["samples"] for p in per.values())
    cell["contacts"] = sum(p["contacts"] for p in per.values())
    cell["dives"] = sorted(per)
    cell["deepest_from"] = {
        "dive_id": deepest_dive,
        "t": per[deepest_dive]["t_deepest"],
        "confidence": per[deepest_dive]["confidence_at_deepest"],
    }
    mins = [p["confidence_min"] for p in per.values() if p["confidence_min"] is not None]
    cell["confidence_min"] = round(min(mins), 3) if mins else None
    wsum = sum(p["confidence_mean"] * p["samples"] for p in per.values() if p["confidence_mean"] is not None)
    wn = sum(p["samples"] for p in per.values() if p["confidence_mean"] is not None)
    cell["confidence_mean"] = round(wsum / wn, 3) if wn else None
    cell["offset_m_max"] = round(max(p["offset_m_max"] for p in per.values()), 1)
    return cell


def merge_dive(store: dict | None, ds: dict, cl: Centreline) -> tuple[dict | None, str | None, dict]:
    """Accumulate one dive's soundings into an area store. (store, why_refused, delta).

    RE-ADDING A DIVE REPLACES ITS OWN CONTRIBUTION rather than adding to it. The
    per-cell record is kept per dive precisely so that can be done: a tool that
    double-counts on a second run turns "how many samples back this cell" — the
    provenance a reader weighs the number by — into a count of how many times
    somebody ran the command.
    """
    if store is None:
        store = new_store(ds["area"], ds["cell_length_m"], cl)

    # Three ways an accumulation can be meaningless, all of them silent if unchecked.
    if store.get("area") != ds["area"]:
        return (
            None,
            (f"this store is for area '{store.get('area')}' and the dive was " f"binned against '{ds['area']}'"),
            {},
        )
    if abs(float(store.get("cell_length_m", 0)) - float(ds["cell_length_m"])) > 1e-9:
        return (
            None,
            (
                f"this store is binned in {store.get('cell_length_m')} m cells and "
                f"this run used {ds['cell_length_m']} m — cell 47 does not mean the "
                f"same stretch of canal in the two, and they cannot be added. Re-run "
                f"every dive at one cell size into a fresh store"
            ),
            {},
        )
    if store.get("centreline", {}).get("fingerprint") != ds["centreline"]["fingerprint"]:
        return (
            None,
            (
                f"this store was built against centreline "
                f"{store.get('centreline', {}).get('fingerprint')} and this run used "
                f"{ds['centreline']['fingerprint']}. Distance-along is measured from "
                f"the start of that geometry, so every cell index in the store means "
                f"a different place under the new one. Rebuild the store, or restore "
                f"the centreline the soundings were taken against"
            ),
            {},
        )

    by_key = {f"{c['line']}:{c['cell']}": c for c in store["cells"]}
    dive_id = ds["dive_id"]

    # Withdraw anything this dive contributed before, so a re-run is a replacement.
    # The keys are remembered because a cell only this dive had reached is deleted
    # here and re-created below, and reporting that as a NEW cell would tell an
    # operator the survey grew when it did not.
    withdrawn: set[str] = set()
    for key, c in list(by_key.items()):
        if dive_id in c.get("per_dive", {}):
            withdrawn.add(key)
            del c["per_dive"][dive_id]
            if not c["per_dive"]:
                del by_key[key]
            else:
                _recompute(c)

    added = deepened = unchanged = 0
    for key, nc in ds["cells"].items():
        contribution = {
            QUANTITY: nc[QUANTITY],
            "samples": nc["samples"],
            "contacts": nc["contacts"],
            "confidence_mean": nc["confidence_mean"],
            "confidence_min": nc["confidence_min"],
            "confidence_at_deepest": nc["confidence_at_deepest"],
            "offset_m_max": nc["offset_m_max"],
            "t_deepest": nc["t_deepest"],
        }
        cur = by_key.get(key)
        if cur is None:
            by_key[key] = _recompute(
                {
                    "line": nc["line"],
                    "cell": nc["cell"],
                    "from_m": nc["from_m"],
                    "to_m": nc["to_m"],
                    "lat": nc["lat"],
                    "lon": nc["lon"],
                    "geom": nc["geom"],
                    "per_dive": {dive_id: contribution},
                }
            )
            if key not in withdrawn:
                added += 1
            continue
        before = cur[QUANTITY]
        cur["per_dive"][dive_id] = contribution
        _recompute(cur)
        if key in withdrawn:
            continue  # this dive's own contribution, re-stated
        if cur[QUANTITY] > before:
            deepened += 1
        else:
            unchanged += 1

    store["cells"] = list(by_key.values())
    store["dives"][dive_id] = {
        "journal": ds["journal"],
        "started_at": ds["started_at"],
        "origin": ds["origin"],
        "adjustment": ds["adjustment"],
        "contacts": ds["contacts"],
        "contact_samples": ds["contact_samples"],
        "binned_samples": ds["binned_samples"],
        "cells": len(ds["cells"]),
        "confidence_mean": ds["confidence_mean"],
        "confidence_min": ds["confidence_min"],
        "confidence_recorded": ds["confidence_recorded"],
        "rejected": ds["rejected"],
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return (
        store,
        None,
        {
            "added": added,
            "deepened": deepened,
            "unchanged": unchanged,
            "restated": len(withdrawn),
            "total": len(store["cells"]),
        },
    )


# ==========================================================================
# Many journals in one call, and the layer a map draws
# ==========================================================================
def _as_centreline(centreline, name: str | None = None) -> tuple[Centreline | None, str | None]:
    """Accept a path, a parsed GeoJSON, or an already-loaded Centreline.

    A caller that has the file reads it from disk (the offline path); a caller that
    already parsed it should not have to write it out again to use this.
    """
    if isinstance(centreline, Centreline):
        return centreline, None
    if isinstance(centreline, (str, Path)):
        p = Path(centreline)
        return load_centreline(p, name or p.stem)
    if isinstance(centreline, dict):
        lines = _lines_from_geojson(centreline)
        if not lines:
            return None, (
                "the centreline given holds no LineString or MultiLineString — "
                "it carries no channel axis, so nothing can be binned along it"
            )
        # Written through the same loader so there is one fingerprint rule, one
        # local-metres conversion and one length: a second path here would drift
        # from the disk path and two stores would disagree about what cell 47 is.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / f"{name or 'centreline'}.geojson"
            p.write_text(json.dumps(centreline), encoding="utf-8")
            cl, why = load_centreline(p, name or "centreline")
        if cl is None:
            return None, why
        # The temp path is gone; say where it really came from.
        return (
            Centreline(
                name=cl.name,
                source="(parsed GeoJSON, not a file)",
                lines_lonlat=cl.lines_lonlat,
                lines_local=cl.lines_local,
                ref_lat=cl.ref_lat,
                ref_lon=cl.ref_lon,
                fingerprint=cl.fingerprint,
                n_points=cl.n_points,
                length_m=cl.length_m,
            ),
            None,
        )
    return None, f"a centreline cannot be read from a {type(centreline).__name__}"


def build_soundings(journals, centreline, *, cell_m: float = CELL_M_DEFAULT) -> dict:
    """Several dive journals -> one set of channel cells. The multi-dive entry point.

        build_soundings([p1, p2], "data/areas/cowley.geojson", cell_m=10.0)
        -> {"cells": [...], "cell_m": 10.0, "reason": None | "why there are none", …}

    Every cell is a LOWER BOUND on the depth of the bed over that stretch, built
    only from samples the journal shows the sub resting on something solid for —
    see this module's docstring for why a swim-past depth is not a sounding.

    THE REASON IS PART OF THE ANSWER, not an error path. An empty cell list with
    nothing attached is read by a console as "this water has been surveyed and
    there is nothing to say", which is the opposite of what an empty list here
    means. So `reason` is filled whenever `cells` is empty, and the reasons are
    different sentences for different findings: no journals at all is not the same
    fact as a dive flown with a dead sensor, and they send an operator to do
    different jobs.
    """
    cl, why = _as_centreline(centreline)
    result = {
        "quantity": QUANTITY,
        "bound": "lower",
        "means": MEANS,
        "unsurveyed": UNSURVEYED,
        "datum": DATUM,
        "cell_m": float(cell_m),
        "centreline": cl.meta() if cl else None,
        "cells": [],
        "dives": {},
        "reason": None,
    }
    if cl is None:
        result["reason"] = why
        return result

    journals = [Path(j) for j in journals]
    if not journals:
        result["reason"] = (
            "no dive journals were given, so nothing has been surveyed "
            "here yet — which is not a finding about the canal. Nobody "
            "has been down this stretch with a depth sensor running"
        )
        return result

    store, refusals = None, []
    for j in journals:
        ds, why_j = extract_dive(j, cl, cell_m=cell_m)
        if ds is None:
            refusals.append(f"{j.name}: {why_j}")
            continue
        store, why_m, _delta = merge_dive(store, ds, cl)
        if store is None:
            refusals.append(f"{j.name}: {why_m}")
            return dict(result, reason="; ".join(refusals))
        result["dives"][ds["dive_id"]] = store["dives"][ds["dive_id"]]

    if store is None or not store["cells"]:
        result["reason"] = (
            f"{len(journals)} journal(s) read and none of them left " f"evidence of the bottom — " + "; ".join(refusals)
        )
        return result
    result["cells"] = sorted(store["cells"], key=lambda c: (c["line"], c["cell"]))
    if refusals:
        # Reported even on success: the cells below come from SOME of the journals,
        # and which ones is a fact about how much of this canal has been surveyed.
        result["skipped"] = refusals
    return result


def write_geojson(result: dict, path: Path) -> Path:
    """Write the survey as GeoJSON — the form a map reads, offline, with no server.

    EVERY FEATURE CARRIES THE LABEL, not just the file. A file-level note does not
    travel with a feature that gets picked up and drawn on its own, and a depth with
    no qualifier beside it reads as the depth of the canal. It is the depth the SUB
    reached, which is a weaker claim, and the gap between the two is the whole point.

    A survey with no cells still writes a file, and the file says WHY it is empty.
    "Nothing has been surveyed here" and "this water has been surveyed and there is
    nothing in it" are the same empty list with opposite meanings, and only the
    second one is safe to draw as clear water.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    feats = []
    for c in result.get("cells", []):
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": c.get("geom") or [[c["lon"], c["lat"]]]},
                "properties": {
                    QUANTITY: c[QUANTITY],
                    "bound": "lower",
                    "what": (
                        f"LOWER BOUND: the bed here is AT LEAST {c[QUANTITY]:.2f} m below "
                        f"the surface — that is the deepest this sub reached while it was "
                        f"resting on something solid, not a measurement of the bed, which "
                        f"may be deeper."
                    ),
                    "from_m": c["from_m"],
                    "to_m": c["to_m"],
                    "line": c["line"],
                    "cell": c["cell"],
                    "samples": c["samples"],
                    "contacts": c["contacts"],
                    "dives": c["dives"],
                    "confidence_mean": c["confidence_mean"],
                    "confidence_min": c["confidence_min"],
                    "deepest_from": c["deepest_from"],
                },
            }
        )
    doc = {
        "type": "FeatureCollection",
        "quantity": QUANTITY,
        "bound": "lower",
        "means": MEANS,
        "unsurveyed": UNSURVEYED,
        "datum": DATUM,
        "cell_m": result.get("cell_m"),
        "surveyed": bool(feats),
        "reason": result.get("reason"),
        "features": feats,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


# ==========================================================================
# Report
# ==========================================================================
def report(
    journal: Path, cl: Centreline, store_path: Path, cell_m: float, dry_run: bool = False, as_json: bool = False
) -> int:
    ds, why = extract_dive(journal, cl, cell_m)
    if as_json:
        if ds is None:
            print(
                json.dumps(
                    {"dive": str(journal), "area": cl.name, "soundings": None, "quantity": QUANTITY, "refused": why},
                    indent=2,
                )
            )
            return 1
        print(json.dumps({**ds, "cells": list(ds["cells"].values())}, indent=2))
        return 0

    print(f"dive       : {Path(journal).name}")
    print(
        f"area       : {cl.name}  ({cl.n_points} centreline points, "
        f"{cl.length_m:.0f} m, fingerprint {cl.fingerprint})"
    )
    print(f"cells      : {cell_m:.0f} m along the channel axis")
    if ds is None:
        print("\n--- NO SOUNDINGS ---")
        print(f"  {why}")
        print("  Nothing was written. A depth map that always produces an answer is worse")
        print("  than none: an invented cell is read as 'the canal is this deep here'.")
        return 1

    t = ds["tally"]
    print("\n--- BOTTOM EVIDENCE ---")
    print(f"  {ds['contacts']} touchdown(s), {ds['contact_samples']} sample(s): the sub " f"stopped descending")
    print("  while the syringe was still filling, so something solid was holding it up.")
    print(
        f"  ({t['flat_runs']} flat stretch(es) examined: {t['too_short']} too brief, " f"{t['no_fill']} with no fill,"
    )
    print(f"   {t['too_shallow']} too shallow, {t['no_descent']} with no descent into them.)")
    # Above the soundings, like calibrate's gap section: everything below covers only
    # the stretch the instruments were alive for, and that is a different claim about
    # the canal from "this is what the dive found".
    partial = [k for k in ("depth_m", "ballast") if ds["columns"].get(k) == "partial"]
    if partial:
        print(f"  SENSOR GAPS: {', '.join(partial)} answered for part of this dive and then " f"stopped.")
        print("  The soundings below come only from the stretch where it was answering; the")
        print("  cells the sub crossed while it was quiet are UNSURVEYED, not shallow.")
    if not ds["confidence_recorded"]:
        print("  Confidence was NOT recorded in this journal. The cells built from it carry " "null")
        print("  rather than 1.0 — 'perfectly trusted' is the last thing an unrecorded fix is.")
    r = ds["rejected"]
    if any(r.values()):
        print(
            f"  DISCARDED for position: {r['held_position']} held (dead compass), "
            f"{r['off_channel']} off-channel, {r['no_position']} with none."
        )
    if ds["adjustment"]["note"] and not ds["adjustment"]["note"].startswith(("identity", "none")):
        print(f"  track adjustment applied: {ds['adjustment']['note']}")

    print("\n--- SOUNDINGS: LOWER BOUNDS on bed depth (the bed is AT LEAST this deep) ---")
    for key in sorted(ds["cells"], key=lambda k: (int(k.split(":")[0]), int(k.split(":")[1]))):
        c = ds["cells"][key]
        conf = c["confidence_at_deepest"]
        conf_s = "confidence not recorded" if conf is None else f"confidence {conf:.2f}"
        print(
            f"  line {c['line']} cell {c['cell']:>5}  "
            f"{c['from_m']:>8.0f}-{c['to_m']:<8.0f} m  "
            f">= {c[QUANTITY]:.2f} m   {c['samples']:>4} samples / "
            f"{c['contacts']} touchdown(s)  {conf_s}  off-axis <= {c['offset_m_max']:.0f} m"
        )
    print("  Depths are below the water surface ON THE DAY. There is no vertical datum.")

    store = load_store(store_path)
    merged, why_m, delta = merge_dive(store, ds, cl)
    print(f"\n--- STORE {store_path} ---")
    if merged is None:
        print(f"  NOT MERGED: {why_m}")
        return 1
    print(
        f"  {delta['added']} new cell(s), {delta['deepened']} deepened, "
        f"{delta['unchanged']} unchanged"
        + (
            f"; {delta['restated']} cell(s) this dive had already contributed to were " f"RE-STATED, not added again"
            if delta["restated"]
            else ""
        )
    )
    print(f"  {delta['total']} cell(s) surveyed in total, from " f"{len(merged['dives'])} dive(s).")
    if dry_run:
        print("  --dry-run: nothing written.")
        return 0
    save_store(store_path, merged)
    print("  written.")
    return 0


# ==========================================================================
# Selftest
# ==========================================================================
def _synthetic_centreline(tmp: Path, name="selftest-area", lat0=52.48, lon0=-1.9, length_m=400.0, step=10.0) -> Path:
    """A straight east-running canal, so distance-along is arithmetic we can check."""
    pts = []
    n = int(length_m / step) + 1
    for i in range(n):
        lat, lon = to_latlon(i * step, 0.0, lat0, lon0)
        pts.append([round(lon, 7), round(lat, 7)])
    p = tmp / f"{name}.geojson"
    p.write_text(
        json.dumps({"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": pts}}),
        encoding="utf-8",
    )
    return p


def _synthetic_dive(
    landings, lat0=52.48, lon0=-1.9, dt=0.1, depth_col=True, ballast_col=True, land=True, confidence=1.0
):
    """A dive that runs east along the canal and lands where told.

    `landings` is [(along_m, bed_depth_m), …]. Between landings the sub is at the
    surface; at each it descends, and (when `land`) stops dead on the bed while the
    syringe keeps filling — which is the only shape this file accepts as evidence.
    """
    rows, t, x = [], 0.0, 0.0

    def emit(n, depth, ballast, x_m):
        nonlocal t
        for _ in range(n):
            t += dt
            row = {
                "type": "s",
                "t": round(t, 3),
                "x": round(x_m, 3),
                "y": 0.0,
                "heading_deg": 90.0,
                "snapped": False,
                "confidence": confidence,
                "speed_ms": 0.0,
                "speed_src": "lut",
                "snagged": False,
                "gyro_only": False,
                "no_heading": False,
                "throttle": 0.0,
                "steer": 0.0,
                "left": 0.0,
                "right": 0.0,
                "ballast_tgt": 1.0,
                "armed": True,
                "mag_cal": 3,
                "encoder_m": 0.0,
            }
            if depth_col:
                row["depth_m"] = None if depth is None else round(depth, 3)
                row["psi"] = None if depth is None else round(14.7 + depth * 1.42, 2)
            if ballast_col:
                row["ballast"] = None if ballast is None else round(ballast, 3)
            rows.append(row)

    for along, bed in landings:
        x = along
        emit(40, 0.0, 0.0, x)  # afloat, syringe empty
        for i in range(1, 11):  # descending as it fills
            emit(2, bed * i / 10.0, 0.05 * i, x)
        if land:
            for i in range(60):  # ON THE BED, still filling
                emit(1, bed, min(1.0, 0.5 + 0.01 * i), x)
        else:
            for i in range(1, 21):  # keeps sinking: no bed
                emit(2, bed + 0.1 * i, min(1.0, 0.5 + 0.02 * i), x)
        emit(40, 0.0, 0.0, x)  # back up
    return rows


def _write_journal(tmp: Path, dive_id: str, rows, lat0=52.48, lon0=-1.9) -> Path:
    p = tmp / f"{dive_id}.jsonl"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "type": "header",
                    "dive_id": dive_id,
                    "started_at": "2026-08-06T10:00:00Z",
                    "origin": {
                        "lat": lat0,
                        "lon": lon0,
                        "accuracy": 4.0,
                        "heading_deg": 90.0,
                        "source": "phone",
                        "t": None,
                    },
                    "speed_lut_id": "default",
                    "auto": False,
                }
            )
            + "\n"
        )
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return p


def selftest(tmpdir: str | None = None) -> int:
    """Prove the maths recovers a bed it was not told, and refuses when it cannot."""
    import tempfile

    ok = True
    with tempfile.TemporaryDirectory(dir=tmpdir) as td:
        tmp = Path(td)
        cl, why = load_centreline(_synthetic_centreline(tmp))
        good = cl is not None
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  centreline read: "
            f"{cl and cl.n_points} points, {cl and round(cl.length_m)} m ({why or ''})"
        )
        if cl is None:
            return 1

        # ---- 1. a dive that lands twice on a known bed ------------------------
        # 44 m and 124 m are mid-cell on purpose. A landing exactly on a cell
        # boundary would make this test a coin toss on the last bit of a float,
        # which would say nothing about whether the binning is right.
        j1 = _write_journal(tmp, "dive-A", _synthetic_dive([(44.0, 1.40), (124.0, 2.10)]))
        ds, why = extract_dive(j1, cl, cell_m=8.0)
        got = ds and {c["cell"]: c[QUANTITY] for c in ds["cells"].values()}
        good = ds is not None and got == {5: 1.40, 15: 2.10}
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  two landings recovered at the right cells "
            f"and depths: {got} (truth {{5: 1.4, 15: 2.1}}) ({why or ''})"
        )

        # The label has to survive into the data, not just the print-out.
        good = ds is not None and all(c["bound"] == "lower" for c in ds["cells"].values()) and "lower_bound" in QUANTITY
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  every cell carries the lower-bound label in "
            f"its own field name ({QUANTITY}) and bound='lower'"
        )

        # ---- 2. two dives over one cell COMBINE, and take the max -------------
        store, why_m, _d = merge_dive(None, ds, cl)
        j2 = _write_journal(tmp, "dive-B", _synthetic_dive([(44.0, 1.85)]))
        ds2, why2 = extract_dive(j2, cl, cell_m=8.0)
        store, why_m2, delta = merge_dive(store, ds2, cl)
        cell5 = next((c for c in store["cells"] if c["cell"] == 5), None) if store else None
        good = (
            cell5 is not None
            and cell5[QUANTITY] == 1.85
            and cell5["dives"] == ["dive-A", "dive-B"]
            and cell5["deepest_from"]["dive_id"] == "dive-B"
        )
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  a second, deeper dive over the same cell "
            f"COMBINES: {cell5 and cell5[QUANTITY]} m from "
            f"{cell5 and cell5['dives']} ({why_m or why_m2 or why2 or ''})"
        )

        shallower = _write_journal(tmp, "dive-C", _synthetic_dive([(44.0, 0.90)]))
        ds3, _ = extract_dive(shallower, cl, cell_m=8.0)
        store, _, _ = merge_dive(store, ds3, cl)
        cell5 = next(c for c in store["cells"] if c["cell"] == 5)
        good = cell5[QUANTITY] == 1.85 and cell5["samples"] > 0 and len(cell5["dives"]) == 3
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  ...and a shallower one does not pull the "
            f"bound back up (still {cell5[QUANTITY]} m, {len(cell5['dives'])} dives, "
            f"{cell5['samples']} samples)"
        )

        # ---- 3. re-running a dive REPLACES, never double-counts ---------------
        n_before, cells_before = cell5["samples"], len(store["cells"])
        store, _, delta = merge_dive(store, ds2, cl)
        cell5 = next(c for c in store["cells"] if c["cell"] == 5)
        good = (
            cell5["samples"] == n_before
            and delta["restated"] >= 1
            and delta["added"] == 0
            and len(store["cells"]) == cells_before
        )
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  re-running the same dive replaces its own "
            f"contribution ({n_before} -> {cell5['samples']} samples)"
        )

        # ---- 4. provenance is per cell, per dive ------------------------------
        good = set(cell5["per_dive"]) == {"dive-A", "dive-B", "dive-C"} and all(
            "samples" in p and "confidence_mean" in p for p in cell5["per_dive"].values()
        )
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  each cell names its dives, their sample "
            f"counts and the confidence they carried ({sorted(cell5['per_dive'])})"
        )

        # ---- 5. the refusals --------------------------------------------------
        # (a) the sub never lands: it keeps sinking, so nothing stopped it.
        jn = _write_journal(tmp, "dive-nobed", _synthetic_dive([(44.0, 1.4)], land=False))
        dsn, whyn = extract_dive(jn, cl, cell_m=8.0)
        good = dsn is None and "still filling" in (whyn or "")
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  a dive that never stops descending yields "
            f"NOTHING and says why ({(whyn or '')[:72]}…)"
        )

        # (b) no syringe level: equilibrium and the bed are indistinguishable.
        jb = _write_journal(tmp, "dive-noball", _synthetic_dive([(44.0, 1.4)], ballast_col=False))
        dsb, whyb = extract_dive(jb, cl, cell_m=8.0)
        good = dsb is None and "ballast" in (whyb or "")
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  a dive with no ballast column yields NOTHING " f"({(whyb or '')[:72]}…)"
        )

        # (c) the MS5837 never answered — present, null throughout.
        rows = _synthetic_dive([(44.0, 1.4)])
        for r in rows:
            r["depth_m"], r["psi"] = None, None
        jd = _write_journal(tmp, "dive-nodepth", rows)
        dsd, whyd = extract_dive(jd, cl, cell_m=8.0)
        good = dsd is None and "never answered once" in (whyd or "")
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  a dive whose pressure sensor never answered "
            f"is refused as such, not as 'no soundings' ({(whyd or '')[:60]}…)"
        )

        # (d) floating at the surface with the syringe filling is NOT a landing.
        jf = _write_journal(tmp, "dive-float", _synthetic_dive([(44.0, 0.0)]))
        dsf, whyf = extract_dive(jf, cl, cell_m=8.0)
        good = dsf is None
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  a sub floating while it fills is not a "
            f"touchdown ({(whyf or '')[:64]}…)"
        )

        # (e) the compass died: the position is HELD, so the depth has no place.
        rows = _synthetic_dive([(44.0, 1.4)])
        for r in rows:
            r["no_heading"] = True
        jh = _write_journal(tmp, "dive-held", rows)
        dsh, whyh = extract_dive(jh, cl, cell_m=8.0)
        good = dsh is None and "HELD" in (whyh or "")
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  contact samples with a HELD position are "
            f"discarded and the refusal says the depths were real ({(whyh or '')[:56]}…)"
        )

        # (f) an absent centreline reports ABSENT — not an empty survey.
        _cl2, why2 = load_centreline(tmp / "no-such-area.geojson", "no-such-area")
        good = _cl2 is None and "ABSENT" in (why2 or "")
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  a missing centreline reports itself ABSENT "
            f"rather than surveying nothing ({(why2 or '')[:52]}…)"
        )

        # (g) a different centreline cannot be accumulated into the same store. Same
        # area NAME, different geometry — which is exactly what a re-downloaded area
        # looks like, and the case where silence would be most expensive.
        other = _synthetic_centreline(tmp, name="selftest-area-v2", lat0=52.60)
        cl2, _ = load_centreline(other, "selftest-area")
        bad, whyx, _ = merge_dive(store, {**ds, "centreline": cl2.meta()}, cl2)
        good = bad is None and "distance-along" in (whyx or "").lower()
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  a store will not accumulate soundings taken "
            f"against a different centreline ({(whyx or '')[:52]}…)"
        )

        # (h) a cell size mismatch is refused for the same reason.
        ds8 = dict(ds, cell_length_m=5.0)
        bad2, whyy, _ = merge_dive(store, ds8, cl)
        good = bad2 is None and "cell 47" in (whyy or "")
        ok &= good
        print(f"  {'pass' if good else 'FAIL'}  ...and will not mix cell sizes " f"({(whyy or '')[:52]}…)")

        # ---- 5b. THE CHIP THAT STOPPED MID-DIVE -------------------------------
        # Not the same case as a sensor that was never fitted. The column is PRESENT
        # and goes null halfway, which is the failure divelog.py writes as JSON null
        # rather than dropping the key. The first landing must survive with the truth
        # it was built from, the second must simply not exist, and the gap must be
        # REPORTED — a survey that quietly covers half the canal it appears to cover
        # is the same lie as a substituted zero in a better suit.
        rows = _synthetic_dive([(44.0, 1.40), (124.0, 2.10)])
        cut = len(rows) // 2
        for r in rows[cut:]:
            r["depth_m"], r["psi"] = None, None
        jp = _write_journal(tmp, "dive-died", rows)
        dsp, whyp = extract_dive(jp, cl, cell_m=8.0)
        gotp = dsp and {c["cell"]: c[QUANTITY] for c in dsp["cells"].values()}
        good = dsp is not None and gotp == {5: 1.40} and dsp["columns"]["depth_m"] == "partial"
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  a dive whose depth sensor died halfway keeps "
            f"the landing it measured and invents no second one: {gotp} "
            f"(depth column '{dsp and dsp['columns']['depth_m']}') ({whyp or ''})"
        )

        # ---- 5c. the multi-journal entry point and the layer it writes --------
        res = build_soundings([j1, j2, shallower], cl.source, cell_m=8.0)
        spans = {(c["from_m"], c["to_m"]): c[QUANTITY] for c in res["cells"]}
        good = (
            spans.get((40.0, 48.0)) == 1.85
            and spans.get((120.0, 128.0)) == 2.10
            and res["reason"] is None
            and res["cell_m"] == 8.0
        )
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  build_soundings() takes several journals at "
            f"once and combines them: {sorted(spans.items())}"
        )

        # An empty answer and an empty answer are not the same empty answer.
        r_none = build_soundings([], cl.source, cell_m=8.0)
        r_dead = build_soundings([jd], cl.source, cell_m=8.0)
        good = (
            r_none["cells"] == []
            and r_dead["cells"] == []
            and r_none["reason"]
            and r_dead["reason"]
            and r_none["reason"] != r_dead["reason"]
        )
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  'nobody has dived here' and 'a dive was flown "
            f"with a dead sensor' are DIFFERENT reasons, not one empty list"
        )

        gj = write_geojson(res, tmp / "soundings.geojson")
        doc = json.loads(Path(gj).read_text(encoding="utf-8"))
        bare = [f for f in doc["features"] if "lower" not in json.dumps(f["properties"]).lower()]
        good = (
            doc["surveyed"] is True and not bare and all(f["properties"]["bound"] == "lower" for f in doc["features"])
        )
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  the layer on disk labels EVERY feature a "
            f"lower bound, not just the file ({len(doc['features'])} features, "
            f"{len(bare)} bare)"
        )

        gj2 = write_geojson(r_dead, tmp / "empty.geojson")
        doc2 = json.loads(Path(gj2).read_text(encoding="utf-8"))
        good = doc2["surveyed"] is False and bool(doc2["reason"])
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  a survey with no cells still writes a file "
            f"that says it is UNSURVEYED and why, rather than empty clear water"
        )

        # ---- 6. round-trip on disk -------------------------------------------
        sp = tmp / "store.json"
        save_store(sp, store)
        back = load_store(sp)
        cell5b = next(c for c in back["cells"] if c["cell"] == 5)
        good = back["quantity"] == QUANTITY and cell5b[QUANTITY] == 1.85 and "UNSURVEYED" in back["unsurveyed"]
        ok &= good
        print(
            f"  {'pass' if good else 'FAIL'}  the store round-trips with the quantity, the "
            f"bound and the meaning of an absent cell written into the file"
        )

    print("\nselftest " + ("passed" if ok else "FAILED"))
    return 0 if ok else 1


# ==========================================================================
def main(argv=None) -> int:
    """CLI entry point. Same contract as calibrate.main: parses argv, returns a code."""
    ap = argparse.ArgumentParser(
        prog="nav.soundings", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("dive", nargs="?", help="path to a dive .jsonl journal")
    ap.add_argument("--area", help="area name; its centreline is data/areas/<area>.geojson")
    ap.add_argument("--centreline", help="explicit centreline GeoJSON (overrides --area)")
    ap.add_argument("--store", help="sounding store to accumulate into " "(default: data/soundings/<area>.json)")
    ap.add_argument(
        "--cell-m",
        type=float,
        default=CELL_M_DEFAULT,
        help=f"cell length along the channel, {CELL_M_MIN:.0f}-{CELL_M_MAX:.0f} m " f"(default {CELL_M_DEFAULT:.0f})",
    )
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--selftest", action="store_true", help="check the maths and the refusals")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not a.dive:
        ap.error("give a dive .jsonl, or --selftest")
    journal = Path(a.dive)
    if not journal.exists():
        ap.error(f"{journal} does not exist")
    if not (CELL_M_MIN <= a.cell_m <= CELL_M_MAX):
        # Not clamped silently. A 1 m cell would imply a longitudinal precision the
        # dead reckoner does not have, and a 100 m cell would average two pounds and
        # a lock together; either would still look like a perfectly good map.
        ap.error(
            f"--cell-m must be between {CELL_M_MIN:.0f} and {CELL_M_MAX:.0f} "
            f"(position error along the channel does not support finer, and coarser "
            f"stops being a survey)"
        )
    if not a.centreline and not a.area:
        ap.error("give --area <name> (uses data/areas/<name>.geojson) or --centreline <file>")

    name = a.area or Path(a.centreline).stem
    cl_path = Path(a.centreline) if a.centreline else settings.areas_dir / f"{name}.geojson"
    cl, why = load_centreline(cl_path, name)
    if cl is None:
        # Reported, not raised, and not silently skipped: the honest answer to "how
        # deep is it here" with no channel axis is that this system cannot tell.
        print(f"dive       : {journal.name}")
        print(f"area       : {name}")
        print(f"\n--- NO SOUNDINGS ---\n  {why}")
        return 1

    store_path = Path(a.store) if a.store else store_path_for(name)
    return report(journal, cl, store_path, a.cell_m, dry_run=a.dry_run, as_json=a.json)


if __name__ == "__main__":
    raise SystemExit(main())
