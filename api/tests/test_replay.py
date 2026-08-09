"""§4e — THE ACCEPTANCE GATE for the heading/speed filter.

Two tests decide whether NAV_FILTER is allowed to move off "dr":

  1. through a magnetic disturbance, "filtered" MUST beat "dr" on track error
     against the simulator's ground truth. This is the exact scenario the filter was
     built for — the thrusters swing the fused compass by up to 22 deg while the gyro
     stays honest — and a filter that cannot win here is not a filter, it is a
     decoration.
  2. on a clean log, "filtered" MUST NOT be meaningfully worse than "dr". A filter
     that helps in the bad case and quietly costs you metres in the ordinary one has
     not been paid for.

WHY THERE ARE NO FIXTURE FILES. Both logs are flown here, from api/nav/sim.py, which
is seeded and has no wall clock — so the inputs are reproducible without committing a
megabyte of JSON that nobody can read and everybody would eventually regenerate to
make a failing test pass. Regenerating a fixture IS lowering the bar; there is nothing
here to regenerate.

They go through the SHIPPED path on purpose: the same _fly() that `nav.cli sim` uses
writes the journal, and the same load_replay_log()/replay()/score() that
`nav.cli replay` uses reads it back. A gate that scores the filter through a private
harness proves the harness works, not the product.

To see the numbers rather than just the verdict:
    python -m nav.cli sim
    python -m nav.cli replay data/dives/sim-*.jsonl --filter both
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from nav import cli
from nav.divelog import DiveLog
from nav.models import Origin
from nav.sim import Simulator

ORIGIN = Origin(lat=52.48, lon=-1.90, accuracy=6, heading_deg=90, source="map_tap")

# The clean-log allowance (test 2). Tied to DISTANCE TRAVELLED, not to whichever
# number the current code happens to produce: dead reckoning's own error is 5-15 % of
# distance (deadreckoning.py's docstring), so a difference of 1 % of distance between
# two estimators is far inside the band where neither can claim to be better. Anything
# expressed as "a fraction of the error we measured today" would be a tolerance fitted
# to the answer, which is how a gate stops being one.
CLEAN_TOLERANCE_FRACTION = 0.01

# The disturbed-log margin (test 1). "Strictly less" is the letter of §4e, but on a
# single seeded path a win of a few centimetres is indistinguishable from luck, so the
# filter is also required to at least HALVE the error. Measured on this path it cuts
# the mean track error by ~85 %, so this is a floor with room under it, not a target.
DISTURBED_MAX_ERROR_RATIO = 0.5


def _fly_journal(directory: Path, dive_id: str, mag_gain_deg: float):
    """Write one replayable dive journal, exactly the way `nav.cli sim` does.

    Recorded under "dr" deliberately: the journal's compass column must be the
    COMPASS, and recording under "filtered" would bake one estimator's opinion into
    the inputs of the experiment that is supposed to compare it with the other.
    """
    sim = Simulator(mag_gain_deg=mag_gain_deg)
    log = DiveLog(dive_id, "2026-01-01T00:00:00Z", ORIGIN, directory=directory)
    cli._fly(sim, ORIGIN, log, backend="dr")
    log.close()
    return directory / f"{dive_id}.jsonl", sim.path_len


class ReplayAcceptance(unittest.TestCase):
    """The gate. Do not weaken anything in this class to make it green — a filter that
    loses to dead reckoning on the scenario it was written for is a finding, and the
    only honest response is to report the numbers and fix the filter."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="neptune-replay-"))
        cls.disturbed, cls.disturbed_len = _fly_journal(cls.tmp, "sim-disturbed", 22.0)
        # "Clean" means the thrusters are not poisoning the magnetometer. Everything
        # else stays: the constant 1.5 deg IMU yaw bias, the heading and gyro noise,
        # the encoder slack. A log with no sensor error at all would not be a clean
        # dive, it would be a different planet.
        cls.clean, cls.clean_len = _fly_journal(cls.tmp, "sim-clean", 0.0)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _ab(self, path: Path) -> tuple[dict, dict, cli.ReplayLog]:
        log = cli.load_replay_log(path)
        self.assertTrue(
            log.has_truth, "the simulator's ground truth did not reach the journal, " "so nothing below can be scored"
        )
        return (cli.score(cli.replay(log, "dr"), log), cli.score(cli.replay(log, "filtered"), log), log)

    # ---- ACCEPTANCE TEST 1 ------------------------------------------------
    def test_filtered_beats_dr_through_the_magnetic_disturbance(self):
        dr, ft, _log = self._ab(self.disturbed)
        detail = (
            f"\n  mean track error : dr {dr['truth_mean']:.2f} m   "
            f"filtered {ft['truth_mean']:.2f} m"
            f"\n  final position   : dr {dr['truth_final']:.2f} m   "
            f"filtered {ft['truth_final']:.2f} m"
            f"\n  worst            : dr {dr['truth_worst']:.2f} m   "
            f"filtered {ft['truth_worst']:.2f} m"
            f"\n  gyro-only        : filtered {ft['gyro_only_pct']:.1f}% of samples"
            f"\n  path flown       : {self.disturbed_len:.1f} m"
        )

        self.assertLess(
            ft["truth_mean"],
            dr["truth_mean"],
            "filtered did NOT beat dead reckoning on mean track error through the "
            "mag disturbance — this is the scenario the filter exists for." + detail,
        )
        self.assertLessEqual(
            ft["truth_mean"],
            DISTURBED_MAX_ERROR_RATIO * dr["truth_mean"],
            f"filtered won, but by less than the {1 - DISTURBED_MAX_ERROR_RATIO:.0%} margin a "
            f"real improvement should show on this path — that is within luck on one seed." + detail,
        )
        self.assertLess(
            ft["truth_final"], dr["truth_final"], "filtered ended FURTHER from truth than dead reckoning did." + detail
        )
        # The filter can only have won by ignoring the poisoned compass, so if it never
        # went gyro-only it won for some other reason and this test is not measuring
        # what it claims to.
        self.assertGreater(
            ft["gyro_only_pct"],
            10.0,
            "filtered never coasted on the gyro, so whatever it beat dr with, it "
            "was not the trust gate this test is supposed to exercise." + detail,
        )

    # ---- ACCEPTANCE TEST 2 ------------------------------------------------
    def test_filtered_is_not_worse_on_a_clean_log(self):
        dr, ft, _log = self._ab(self.clean)
        allowance = CLEAN_TOLERANCE_FRACTION * self.clean_len
        detail = (
            f"\n  mean track error : dr {dr['truth_mean']:.2f} m   "
            f"filtered {ft['truth_mean']:.2f} m"
            f"\n  final position   : dr {dr['truth_final']:.2f} m   "
            f"filtered {ft['truth_final']:.2f} m"
            f"\n  allowance        : {allowance:.2f} m "
            f"({CLEAN_TOLERANCE_FRACTION:.0%} of {self.clean_len:.1f} m flown)"
        )

        self.assertLessEqual(
            ft["truth_mean"],
            dr["truth_mean"] + allowance,
            "on a clean log filtered is worse than dead reckoning by more than "
            "the allowance — the filter is costing metres in the ordinary "
            "case." + detail,
        )
        self.assertLessEqual(
            ft["truth_final"],
            dr["truth_final"] + allowance,
            "on a clean log filtered ends further from truth than dead "
            "reckoning by more than the allowance." + detail,
        )


class ReplayHarness(unittest.TestCase):
    """The gate above is only worth as much as the harness under it. These check that
    the journal really carries what the sensors said, that reading it back reproduces
    the dive it recorded, and that the summary refuses to pick a winner when there is
    no truth to pick one against."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="neptune-replay-h-"))
        cls.path, _len = _fly_journal(cls.tmp, "sim-harness", 22.0)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _rows(self):
        return [
            json.loads(ln)
            for ln in self.path.read_text(encoding="utf-8").splitlines()
            if json.loads(ln).get("type") == "s"
        ]

    def test_journal_carries_the_measured_channels_and_truth(self):
        rows = self._rows()
        self.assertGreater(len(rows), 100)
        for key in ("raw_heading_deg", "gyro_z_dps", "accel_fwd_ms2", "encoder_m", "true_x", "true_y"):
            self.assertIn(
                key,
                rows[0],
                f"{key} never reached the journal — a dive missing it " f"cannot be replayed through the filter",
            )
        # The paddlewheel is the whole reason the speed filter and the snag detector
        # exist. A log where it is null everywhere is a log where neither was tested.
        self.assertTrue(
            any(r.get("speed_ms_measured") is not None for r in rows),
            "the paddlewheel column is null for the entire log",
        )
        # ...and it must still be null somewhere: below ~0.1 m/s the wheel stalls, and
        # a log that never shows that has quietly stopped modelling the stall.
        self.assertTrue(
            any(r.get("speed_ms_measured") is None for r in rows),
            "the paddlewheel never went stale, so the stale branches of the " "speed filter were never exercised",
        )

    def test_replaying_dr_reproduces_the_track_it_logged(self):
        """The reader rebuilds the samples faithfully — or every score above is fiction.

        Same inputs through the same estimator must land on the same metre. If this
        drifts, the A/B is comparing two different dives and nobody would notice.
        """
        log = cli.load_replay_log(self.path)
        sc = cli.score(cli.replay(log, "dr"), log)
        self.assertIsNotNone(sc["logged_mean"])
        self.assertLessEqual(
            sc["logged_mean"],
            0.01,
            f"replaying the recording backend did not reproduce its own track: "
            f"mean {sc['logged_mean']:.3f} m, final {sc['logged_final']:.3f} m",
        )

    def test_a_geojson_on_its_own_is_refused(self):
        """A GeoJSON stores conclusions, not sensor readings. Refusing is the only
        honest answer; running half an estimator off it and printing a number is not."""
        lonely = self.tmp / "conclusions-only.geojson"
        lonely.write_text("{}", encoding="utf-8")
        with self.assertRaises(FileNotFoundError):
            cli.resolve_log(lonely)
        # But when the journal IS beside it, the operator who typed the wrong extension
        # gets the replay they meant, with a note saying what happened.
        beside = self.path.with_suffix(".geojson")
        beside.write_text("{}", encoding="utf-8")
        resolved, note = cli.resolve_log(beside)
        self.assertEqual(resolved, self.path)
        self.assertIn("instead", note or "")

    def test_no_truth_means_no_winner(self):
        """Two estimates disagreeing says they disagree, and nothing about which is
        right. The summary must say so rather than crowning the one that moved less."""
        stripped = self.tmp / "no-truth.jsonl"
        with open(stripped, "w", encoding="utf-8") as fh:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                rec.pop("true_x", None)
                rec.pop("true_y", None)
                fh.write(json.dumps(rec) + "\n")
        log = cli.load_replay_log(stripped)
        self.assertFalse(log.has_truth)
        sc_dr = cli.score(cli.replay(log, "dr"), log)
        sc_ft = cli.score(cli.replay(log, "filtered"), log)
        self.assertIsNone(sc_dr["truth_mean"])

        out = io.StringIO()
        with redirect_stdout(out):
            cli._print_side_by_side(sc_dr, sc_ft)
        text = out.getvalue()
        self.assertNotIn("VERDICT", text, "a winner was declared with no ground truth to declare it against")
        self.assertIn("no ground truth", text)

    def test_the_replay_command_runs_end_to_end(self):
        """argparse to summary, the way an operator actually invokes it."""
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.main(["replay", str(self.path), "--filter", "both"])
        text = out.getvalue()
        self.assertEqual(rc, 0, text)
        for expected in ("--- dr ---", "--- filtered ---", "gyro-only", "speed source", "snag", "VERDICT"):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
