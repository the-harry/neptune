"""Soundings from dive journals (nav/soundings.py) — the epistemology, which is
where this one goes wrong quietly.

Run:  cd api && python -m unittest tests.test_soundings -v

WHAT A SOUNDING IS HERE, because everything below follows from it. This vehicle has
no echo sounder. The MS5837 measures the depth of the SUB, so a depth sample is
evidence about the BED only when the journal shows the sub was resting on it — and
even then the number is a LOWER BOUND, because the pressure port sits above the
keel and the hull may have landed on silt, weed or a sunken trolley. Every error
has the same sign: the bed is at least this deep and may be deeper.

Everything that can go wrong with this file goes wrong by forgetting one of those
sentences, and each check below is named for the one it protects:

  A FLAT DEPTH IS NOT A LANDING. A sub hanging at neutral buoyancy holds a depth
  perfectly, forever, at any depth at all. The only thing aboard that separates it
  from the bed is the syringe: a sub that stops descending WHILE STILL TAKING ON
  WATER is being held up by something that is not buoyancy. So the fixture flies a
  neutral hold as well as a landing, and the neutral one must produce nothing —
  because binning every depth sample would make a full, plausible, technically-true
  map that reads "the canal is 1.2 m here" over water that is 2.5 m.

  A JOURNAL WITH NO EVIDENCE IS NOT A SHALLOW CANAL. divelog.py logs a null depth
  as null, at length and on purpose. If that arrives here as 0.0 the layer paints a
  stretch as bottomless-shallow from a sensor nobody read. The right answer is no
  cells AND A REASON — and the reason is the deliverable, because "nothing has been
  surveyed here", "the pressure sensor never answered" and "this journal has no
  ballast column" send an operator to three different jobs.

  DEEPER WINS AND COMBINING IS NOT OVERWRITING. Two dives over one stretch are two
  lower bounds on the same number and the larger is the better bound. A builder
  that lets the newer dive overwrite the older LOSES depth — the one direction a
  lower bound may never move on new evidence.

  THE BINNING IS LONGITUDINAL. Position error here runs to metres and in places
  exceeds the canal's half-width, so cells are intervals of distance ALONG the
  centreline, never squares of a grid: cross-channel structure the navigation
  cannot support is false precision, and the prettier it looks the more it lies.
  The fixture lands one touchdown 12 m off the axis — more than a whole cell width
  sideways — and it must share a cell with one 3 m from it along the water.

WHY THERE ARE NO FIXTURE FILES. The journals are written HERE, by the real DiveLog
and the real SensorSample, so the input is the format the vehicle actually writes
rather than a hand-typed approximation that drifts the first time the journal gains
a field. test_replay.py makes the same argument at more length.

WHY THE PROFILES ARE NOISELESS. The holds sit at an exactly constant depth. A real
landing wobbles a centimetre or two and would still be inside the 0.05 m band the
module calls flat, but then the expected numbers below would have to be written as
tolerances — and a tolerance is where a fixture stops being able to tell "the
deepest sample won" from "some sample won".
"""

from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from nav import soundings
from nav.divelog import DiveLog
from nav.geo import to_latlon
from nav.models import NavState, Origin, SensorSample
from nav.soundings import QUANTITY, build_soundings, write_geojson

ORIGIN = Origin(lat=52.48, lon=-1.90, accuracy=6, heading_deg=90, source="map_tap")

# The channel: due east from the origin, 100 m, in four vertices. Collinear so that
# "distance along" is just x and the expected cells can be read off by eye — but with
# intermediate vertices, so the accumulation across segments is exercised rather than
# a single-segment special case.
CENTRELINE_LOCAL = [(0.0, 0.0), (30.0, 0.0), (60.0, 0.0), (100.0, 0.0)]
CELL_M = 10.0

DT = 0.5  # journal period; 10 samples then span 4.5 s
HOLD_N = 10  # >= soundings.MIN_CONTACT_SAMPLES (8)
FILL = 0.16  # >= soundings.MIN_FILL_RISE (0.10)

# What the fixture means, written out independently of the code that computes it.
# Every chainage is deliberately away from a 10 m boundary, so the float noise of a
# lat/lon round trip cannot move a sample between cells and make this flaky.
EXPECTED_A = {(0.0, 10.0): 2.00, (20.0, 30.0): 3.00}
EXPECTED_AB = {(0.0, 10.0): 2.60, (20.0, 30.0): 3.00, (60.0, 70.0): 1.20}
DIVES_PER_CELL = {(0.0, 10.0): ["dive-A", "dive-B"], (20.0, 30.0): ["dive-A", "dive-B"], (60.0, 70.0): ["dive-B"]}


# ---------------------------------------------------------------------------
# Flying a dive
# ---------------------------------------------------------------------------
def touchdown(t, x, y, depth, *, ballast=0.30, fill=FILL, n=HOLD_N):
    """A descent onto something solid, then the hold. -> (samples, next_t)

    Three descent samples at a quarter, a half and three quarters of the landing
    depth: far enough apart that no two of them look flat to a 0.05 m band, and
    shallow enough that the run is unambiguously descended INTO (the module wants
    0.3 m of descent inside the previous 20 s, which is what separates a landing
    from a sub floating at the surface with the syringe running).

    Then the hold: the depth stops dead and the syringe keeps filling. That pair is
    the whole definition of bottom contact, and neither half alone is evidence.
    """
    out = []
    for i in (1, 2, 3):
        out.append((t, x, y, round(depth * 0.25 * i, 3), round(ballast - 0.02 * (4 - i), 3)))
        t += DT
    for i in range(n):
        out.append((t, x, y, depth, round(ballast + fill * i / (n - 1), 3)))
        t += DT
    return out, t


def neutral_hold(t, x, y, depth, *, ballast=0.30, n=HOLD_N):
    """The awkward twin: the sub stops, and the syringe does NOT keep filling.

    A perfectly flat depth held for as long as you like, at any depth at all. This
    is the settled hold nav/calibrate.py fits the ballast curve against, and it is
    equilibrium rather than the bed. It must leave no sounding whatsoever.
    """
    out = []
    for i in (1, 2, 3):
        out.append((t, x, y, round(depth * 0.25 * i, 3), ballast))
        t += DT
    for _ in range(n):
        out.append((t, x, y, depth, ballast))
        t += DT
    return out, t


def blind(t, x, y, *, n=4, ballast=0.30):
    """Samples the pressure sensor did not answer for. Null, exactly as logged."""
    out = []
    for _ in range(n):
        out.append((t, x, y, None, ballast))
        t += DT
    return out, t


def _profile(*legs):
    """Concatenate legs, each a callable taking (t, ...) -> (samples, next_t)."""
    t, out = 0.0, []
    for leg in legs:
        got, t = leg(t)
        out.extend(got)
    return out


# DIVE A — two landings in ONE cell (the deeper one must win, and both must count as
# touchdowns), one landing 12 m off the axis in another cell, one NEUTRAL hold that
# must produce nothing, and a stretch the depth sensor was silent for.
def dive_a_samples():
    return _profile(
        lambda t: touchdown(t, 24.0, -1.0, 3.00),
        lambda t: touchdown(t, 26.0, 1.0, 1.50),
        # 12 m off the centreline: more than a whole cell sideways, 5 m along.
        lambda t: touchdown(t, 5.0, 12.0, 2.00),
        # The temptation. Flat, deep, long — and no water going in.
        lambda t: neutral_hold(t, 47.0, 0.0, 1.80),
        lambda t: blind(t, 70.0, 0.0),
    )


# DIVE B — overlaps A in both cells and disagrees in both directions, plus one cell
# nothing else has ever reached.
def dive_b_samples():
    return _profile(
        lambda t: touchdown(t, 7.0, -2.0, 2.60),  # deeper than A's 2.00 here
        lambda t: touchdown(t, 25.0, 0.0, 2.10),  # shallower than A's 3.00 here
        lambda t: touchdown(t, 63.0, 0.0, 1.20),  # only this dive has been here
    )


# DIVE C — a real dive flown with a dead or unfitted MS5837. Journalled correctly:
# every depth null, which is what divelog.py exists to preserve, and evidence of
# nothing whatsoever.
def dive_c_samples():
    return _profile(lambda t: blind(t, 10.0, 0.0, n=12), lambda t: blind(t, 30.0, 0.0, n=12))


# DIVE E — one neutral hold and nothing else, so the refusal sentence is about the
# syringe rather than about the sensor.
def dive_e_samples():
    return _profile(lambda t: neutral_hold(t, 33.0, 0.0, 1.90))


# ---------------------------------------------------------------------------
class SoundingsTestCase(unittest.TestCase):
    """Journals written by the real DiveLog against a real centreline file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="neptune-soundings-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.centreline = self._write_centreline()

    def _write_centreline(self) -> Path:
        coords = [[lon, lat] for lat, lon in (to_latlon(x, y, ORIGIN.lat, ORIGIN.lon) for x, y in CENTRELINE_LOCAL)]
        gj = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"waterway": "canal"},
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
            ],
        }
        p = self.tmp / "test-cut.geojson"
        p.write_text(json.dumps(gj), encoding="utf-8")
        return p

    def journal(self, dive_id: str, samples, *, with_ballast: bool = True) -> Path:
        """One dive journal, written by the class the vehicle writes with.

        `with_ballast=False` writes the NavState alone, with no SensorSample beside
        it — which is exactly what divelog.py does when there is no raw sample, and
        exactly the journal that has real depths in it and nothing that can tell a
        landing from a hover.
        """
        # DiveLog OPENS ITS JOURNAL IN APPEND MODE — correctly, because the file is a
        # safety record and a re-opened dive must not truncate what is already in it.
        # The consequence here is that flying the same dive_id twice inside one test
        # doubles every sample silently, and every count below would then be exactly
        # twice what this file says it is while still looking internally consistent.
        # Caught while checking the fixture against the module; it cost half an hour,
        # so it fails loudly now.
        path = self.tmp / f"{dive_id}.jsonl"
        self.assertFalse(
            path.exists(),
            f"{dive_id} has already been flown in this test. DiveLog "
            f"appends, so a second call doubles the journal instead of "
            f"replacing it. Use a different dive id.",
        )
        log = DiveLog(dive_id, "2026-01-01T00:00:00Z", ORIGIN, directory=self.tmp)
        for t, x, y, depth, ballast in samples:
            lat, lon = to_latlon(x, y, ORIGIN.lat, ORIGIN.lon)
            ns = NavState(
                t=t,
                lat=lat,
                lon=lon,
                depth_m=depth,
                heading_deg=90.0,
                x_m=x,
                y_m=y,
                raw_lat=lat,
                raw_lon=lon,
                snapped=False,
                range_m=math.hypot(x, y),
                payout_m=math.hypot(x, y) * 1.2,
            )
            raw = None
            if with_ballast:
                raw = SensorSample(
                    t=t,
                    heading_deg=90.0,
                    depth_m=depth,
                    throttle=0.0,
                    ballast_level=ballast,
                    ballast_target=ballast,
                    pressure_psi=None if depth is None else 14.7 + depth * 1.42,
                )
            log.add(ns, raw)
        # save() writes the sibling .geojson (which soundings reads for the operator's
        # track adjustment) and closes the journal.
        log.save(self.tmp)
        return self.tmp / f"{dive_id}.jsonl"

    # -- driving it ---------------------------------------------------------
    def build(self, journals, cell_m=CELL_M):
        return build_soundings(journals, self.centreline, cell_m=cell_m)

    def spans(self, result) -> dict:
        """{(from_m, to_m): cell}, with the duplicate-cell check done once."""
        out = {}
        for c in result["cells"]:
            key = (float(c["from_m"]), float(c["to_m"]))
            self.assertNotIn(key, out, f"two cells claim the same stretch {key}: " f"{result['cells']}")
            out[key] = c
        return out

    def depths(self, result) -> dict:
        return {k: c[QUANTITY] for k, c in self.spans(result).items()}


# ===========================================================================
class KnownTouchesProduceKnownCells(SoundingsTestCase):

    def test_one_journal_produces_exactly_the_expected_cells(self):
        res = self.build([self.journal("dive-A", dive_a_samples())])
        got = self.depths(res)
        self.assertEqual(
            sorted(got),
            sorted(EXPECTED_A),
            f"expected cells {sorted(EXPECTED_A)} and got {sorted(got)}.\n"
            f"  (40.0, 50.0) appearing means the NEUTRAL hold at 47 m was read as a "
            f"landing — a flat depth with no water going in is buoyancy, and it "
            f"happens at any depth at all.\n"
            f"  (70.0, 80.0) appearing means a null depth became a number.\n"
            f"  reason={res.get('reason')!r}",
        )
        for span, want in EXPECTED_A.items():
            self.assertAlmostEqual(
                got[span],
                want,
                places=2,
                msg=f"cell {span} bounds the bed at {got[span]} m; the deepest the sub "
                f"reached while resting on something there is {want} m",
            )

    def test_two_landings_in_one_cell_keep_the_deeper_and_count_as_two(self):
        # 3.00 m then 1.50 m, two metres apart along the same 10 m stretch. The mean
        # would be 2.25 and the last would be 1.50; only the deepest is a bound the
        # cell already holds, and averaging throws it away.
        res = self.build([self.journal("dive-A", dive_a_samples())])
        cell = self.spans(res)[(20.0, 30.0)]
        self.assertAlmostEqual(cell[QUANTITY], 3.00, places=2, msg=f"kept {cell[QUANTITY]} m of 3.00 and 1.50: {cell}")
        self.assertEqual(
            cell["contacts"], 2, f"two separate landings in this cell were counted as " f"{cell['contacts']}: {cell}"
        )
        self.assertEqual(cell["samples"], 2 * HOLD_N, f"{cell['samples']} samples from two {HOLD_N}-sample holds")

    def test_a_flat_depth_with_no_fill_is_not_a_landing(self):
        # On its own, so the refusal is the whole answer and cannot hide behind cells
        # another leg produced.
        res = self.build([self.journal("dive-E", dive_e_samples())])
        self.assertEqual(res["cells"], [], f"a neutral-buoyancy hold produced soundings: {res['cells']}")
        self.assertRegex(
            str(res["reason"]),
            r"still filling|neutral buoyancy|syringe",
            f"it was refused, but not for the right reason: {res['reason']!r}. A depth "
            f"that simply goes flat is equilibrium; what makes it the bed is that it "
            f"stopped while more water was going in.",
        )

    def test_a_silent_stretch_inside_a_good_dive_creates_no_cell(self):
        res = self.build([self.journal("dive-A", dive_a_samples())])
        self.assertNotIn(
            (70.0, 80.0),
            self.spans(res),
            "the sensor was null for the stretch at 70 m and a cell " "appeared there anyway",
        )


# ===========================================================================
class NoEvidenceSaysWhy(SoundingsTestCase):
    """The empty list is the easy half. The reason is the deliverable."""

    def test_a_journal_whose_depth_sensor_never_answered_produces_nothing(self):
        res = self.build([self.journal("dive-C", dive_c_samples())])
        self.assertEqual(res["cells"], [], f"a dive with no depth at all produced cells: {res['cells']}")

    def test_and_it_says_the_sensor_never_answered(self):
        res = self.build([self.journal("dive-C", dive_c_samples())])
        reason = str(res["reason"] or "")
        self.assertGreaterEqual(len(reason), 40, f"the reason is barely a sentence: {reason!r}")
        self.assertRegex(
            reason, r"null on every sample|never answered", f"the reason does not name what was missing: {reason!r}"
        )
        self.assertRegex(reason, r"depth|pressure", f"...or which sensor: {reason!r}")

    def test_a_journal_with_no_ballast_column_gets_a_different_reason(self):
        # THE DISTINCTION THAT MATTERS. This dive has perfectly good depths in it. It
        # simply has nothing that can tell the sub landing on the bed from the sub
        # hanging at neutral buoyancy — and "we have no depths" and "we cannot tell
        # what the depths mean" send an operator to two different jobs.
        no_ballast = self.build([self.journal("dive-D", dive_a_samples(), with_ballast=False)])
        silent = self.build([self.journal("dive-C", dive_c_samples())])
        self.assertEqual(
            no_ballast["cells"], [], f"a journal with no ballast column produced soundings: " f"{no_ballast['cells']}"
        )
        self.assertRegex(
            str(no_ballast["reason"]),
            r"ballast",
            f"the reason does not name the missing column: " f"{no_ballast['reason']!r}",
        )
        self.assertNotEqual(
            str(no_ballast["reason"]),
            str(silent["reason"]),
            "a dive with no depths and a dive whose depths cannot be "
            "interpreted were refused with the same sentence",
        )

    def test_no_journals_at_all_is_a_third_reason_again(self):
        # THE WHOLE DOCTRINE IN ONE CHECK. "Nobody has ever been down here" is a fact
        # about the survey; "somebody went down with a dead sensor" is a fact about
        # the vehicle. One reason for both is one of them being reported as the other.
        none = self.build([])
        silent = self.build([self.journal("dive-C", dive_c_samples())])
        self.assertEqual(none["cells"], [])
        self.assertTrue(none["reason"], "an empty journal list produced no reason")
        self.assertRegex(str(none["reason"]), r"no dive journals|nobody|never been", f"{none['reason']!r}")
        self.assertNotEqual(
            str(none["reason"]).strip().lower(),
            str(silent["reason"]).strip().lower(),
            "no survey and a failed survey read identically",
        )

    def test_an_absent_centreline_is_absent_and_says_so(self):
        # Canal-side there is nothing to fetch it with. Without a channel axis there
        # is no distance-along, so there are no cells — and that is a statement about
        # the MAP, not about the water.
        res = build_soundings(
            [self.journal("dive-A", dive_a_samples())], self.tmp / "no-such-area.geojson", cell_m=CELL_M
        )
        self.assertEqual(res["cells"], [])
        self.assertRegex(
            str(res["reason"]),
            r"ABSENT|does not exist",
            f"a missing centreline was not reported as absent: " f"{res['reason']!r}",
        )


# ===========================================================================
class BinningIsLongitudinal(SoundingsTestCase):
    """A canal is 4 m wide and kilometres long. The cells follow the water."""

    def test_a_landing_far_off_the_axis_bins_by_distance_along_it(self):
        # The touchdown at (5, 12) is 12 m across the channel from the axis — more
        # than a whole cell width sideways — and 5 m along it. On a grid that is a
        # different square; along the water it is the same stretch, and it is the
        # ONLY landing in that cell for this dive, so if it were misplaced the cell
        # would vanish rather than merely shift.
        res = self.build([self.journal("dive-A", dive_a_samples())])
        spans = self.spans(res)
        self.assertIn(
            (0.0, 10.0), spans, f"the landing 12 m off the axis did not bin at 5 m along: " f"{sorted(spans)}"
        )
        self.assertAlmostEqual(spans[(0.0, 10.0)][QUANTITY], 2.00, places=2)
        self.assertGreaterEqual(
            spans[(0.0, 10.0)]["offset_m_max"],
            11.0,
            f"the cross-track distance was not kept as provenance: "
            f"{spans[(0.0, 10.0)]['offset_m_max']} m. Throwing it away is right; "
            f"forgetting it was thrown away is not.",
        )

    def test_cells_are_intervals_of_the_axis_on_cell_boundaries(self):
        res = self.build([self.journal("dive-A", dive_a_samples()), self.journal("dive-B", dive_b_samples())])
        self.assertTrue(res["cells"], f"no cells: {res.get('reason')}")
        for c in res["cells"]:
            lo, hi = float(c["from_m"]), float(c["to_m"])
            self.assertAlmostEqual(hi - lo, CELL_M, places=3, msg=f"cell {(lo, hi)} is {hi - lo} m long: {c}")
            self.assertAlmostEqual(
                lo % CELL_M,
                0.0,
                places=3,
                msg=f"cell {(lo, hi)} does not start on a boundary, "
                f"so two dives cannot share a cell by "
                f"construction: {c}",
            )
            self.assertEqual(c["cell"], int(round(lo / CELL_M)), f"the cell index disagrees with its own interval: {c}")

    def test_nothing_is_indexed_across_the_channel(self):
        # A second axis would have to be named somewhere. `line` is which disjoint
        # waterway, not which side of it, so it is not one.
        res = self.build([self.journal("dive-A", dive_a_samples())])
        for c in res["cells"]:
            across = [
                k
                for k in c
                if re.fullmatch(r"row|col|column|j|iy|ix|grid_[xy]|across_m|" r"lateral_m|y_m", str(k), re.I)
            ]
            self.assertFalse(
                across, f"a cell is indexed across the water as well as " f"along it ({across}): {sorted(c)}"
            )


# ===========================================================================
class TwoDivesCombine(SoundingsTestCase):

    def setUp(self):
        super().setUp()
        self.a = self.journal("dive-A", dive_a_samples())
        self.b = self.journal("dive-B", dive_b_samples())
        self.res = self.build([self.a, self.b])

    def test_the_second_dive_adds_to_the_first_rather_than_replacing_it(self):
        got = self.depths(self.res)
        self.assertEqual(
            sorted(got),
            sorted(EXPECTED_AB),
            f"two dives over overlapping water produced {sorted(got)}; the union is "
            f"{sorted(EXPECTED_AB)}. Losing (60.0, 70.0) means only one journal was "
            f"read; losing one of the others means the second dive replaced the first.",
        )

    def test_the_deeper_reading_wins_in_both_directions(self):
        # Deliberately both ways round: in (0,10) the LATER dive is deeper (2.60 beats
        # 2.00) and in (20,30) the EARLIER one is (3.00 beats 2.10). "Keep the last"
        # passes half of this and "keep the first" passes the other half; only "the
        # deeper bound wins" passes both.
        got = self.depths(self.res)
        for span, want in EXPECTED_AB.items():
            self.assertAlmostEqual(
                got[span],
                want,
                places=2,
                msg=f"cell {span} bounds the bed at {got[span]} m; the deepest anything "
                f"ever reached there is {want} m. A lower bound may only move DOWN "
                f"on new evidence, never up.",
            )

    def test_every_cell_names_the_dives_it_was_made_from(self):
        spans = self.spans(self.res)
        for span, want in DIVES_PER_CELL.items():
            cell = spans[span]
            self.assertEqual(
                sorted(cell["dives"]),
                want,
                f"cell {span} says it came from {cell.get('dives')}; {want} each left "
                f"a sounding in it. A cell that keeps only the winning dive loses the "
                f"fact that two of them agreed.",
            )
            self.assertIn(
                cell["deepest_from"]["dive_id"],
                want,
                f"cell {span} does not say which dive the number itself came " f"from: {cell['deepest_from']}",
            )

    def test_the_per_dive_contributions_survive_the_merge(self):
        cell = self.spans(self.res)[(20.0, 30.0)]
        per = cell["per_dive"]
        self.assertEqual(sorted(per), ["dive-A", "dive-B"], f"{per}")
        self.assertAlmostEqual(per["dive-A"][QUANTITY], 3.00, places=2)
        self.assertAlmostEqual(per["dive-B"][QUANTITY], 2.10, places=2)
        self.assertEqual(
            cell["samples"],
            sum(p["samples"] for p in per.values()),
            f"the rolled-up sample count does not match its parts: {cell}",
        )

    def test_re_reading_the_same_dive_does_not_double_count_it(self):
        # The provenance a reader weighs a cell by is "how many samples back this",
        # and a tool that adds a dive to itself turns that into "how many times
        # somebody ran the command".
        twice = self.build([self.a, self.b, self.a])
        self.assertEqual(self.depths(twice), self.depths(self.res), "re-reading a journal changed the depths")
        for span, cell in self.spans(twice).items():
            self.assertEqual(
                cell["samples"], self.spans(self.res)[span]["samples"], f"cell {span} counted dive-A twice: {cell}"
            )


# ===========================================================================
class TheLabelTravels(SoundingsTestCase):
    """A lower bound that stops saying so has become a measurement by omission."""

    def setUp(self):
        super().setUp()
        self.res = self.build([self.journal("dive-A", dive_a_samples()), self.journal("dive-B", dive_b_samples())])

    def test_every_cell_carries_the_label_in_its_own_right(self):
        self.assertTrue(self.res["cells"], "no cells to check")
        for c in self.res["cells"]:
            self.assertEqual(c.get("bound"), "lower", f"a cell does not say what kind of number it is: {c}")
            self.assertIn(
                QUANTITY, c, f"the depth is not under the name that says what it is " f"({QUANTITY}): {sorted(c)}"
            )

    def test_the_result_as_a_whole_explains_what_that_means(self):
        self.assertEqual(self.res.get("quantity"), QUANTITY, f"{self.res.get('quantity')}")
        self.assertEqual(self.res.get("bound"), "lower")
        for key, pattern in (
            ("means", r"AT LEAST"),
            ("unsurveyed", r"[Aa]bsent is not shallow"),
            ("datum", r"no vertical datum|surface"),
        ):
            self.assertRegex(
                str(self.res.get(key, "")), pattern, f"result[{key!r}] does not say it: {self.res.get(key)!r}"
            )

    def test_the_label_reaches_the_file_the_console_reads(self):
        out = write_geojson(self.res, self.tmp / "soundings.geojson")
        doc = json.loads(Path(out).read_text(encoding="utf-8"))
        self.assertEqual(doc.get("bound"), "lower")
        self.assertTrue(doc.get("surveyed"), f"cells were written and surveyed is " f"{doc.get('surveyed')!r}")
        self.assertRegex(
            str(doc.get("unsurveyed", "")),
            r"[Aa]bsent is not shallow",
            "the file does not tell a renderer what a MISSING cell means, "
            "which is the half of this that gets drawn as clear water",
        )
        self.assertTrue(doc["features"], "no features written")
        for f in doc["features"]:
            p = f["properties"]
            self.assertEqual(p.get("bound"), "lower", f"{p}")
            self.assertRegex(
                str(p.get("what", "")),
                r"AT LEAST",
                f"a feature carries a depth with no qualifier beside it. A "
                f"file-level note does not travel with a feature that gets "
                f"picked up and drawn on its own: {p}",
            )
            self.assertIn(QUANTITY, p, f"{sorted(p)}")
            self.assertEqual(
                f["geometry"]["type"],
                "LineString",
                "a cell is a stretch of channel, not a dot somebody has " "to guess the extent of",
            )

    def test_an_empty_survey_still_writes_a_file_that_says_why(self):
        empty = self.build([self.journal("dive-C", dive_c_samples())])
        out = write_geojson(empty, self.tmp / "empty.geojson")
        doc = json.loads(Path(out).read_text(encoding="utf-8"))
        self.assertEqual(doc["features"], [])
        self.assertFalse(
            doc.get("surveyed"),
            "an empty survey is flagged as surveyed, which is the one "
            "reading of an empty feature list that is safe to draw as "
            "clear water — and it is the wrong one here",
        )
        self.assertTrue(
            doc.get("reason"),
            f"an empty layer with nothing attached: a console reads that as " f"'surveyed, nothing to say'. {doc}",
        )

    def test_the_quantity_is_spelled_one_way_everywhere(self):
        # The store, the file and the console all read this key. A second spelling of
        # the same figure is how two files start disagreeing about what they show.
        out = write_geojson(self.res, self.tmp / "soundings.geojson")
        doc = json.loads(Path(out).read_text(encoding="utf-8"))
        self.assertEqual(soundings.QUANTITY, "lower_bound_m", f"the quantity is named {soundings.QUANTITY!r}")
        self.assertEqual(doc.get("quantity"), soundings.QUANTITY)
        self.assertEqual(self.res.get("quantity"), soundings.QUANTITY)
        self.assertTrue(all(soundings.QUANTITY in f["properties"] for f in doc["features"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
