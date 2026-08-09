"""§6 — `api/blackbox/rovlog.py`, the blackbox reader, and the marks it owes its reader.

The vehicle and the console each keep their own log in their own monotonic clock. This
module is what puts them on one timebase afterwards, and everything it prints is only
worth reading if three claims hold:

  1. A CLIENT STAMP IS A TRANSLATION, NOT A MEASUREMENT. `onto_pi` moves a client
     timestamp onto the Pi's clock and leaves a Pi timestamp exactly where it was. The
     bug it guards against is correcting the side that did not need it, which pushes the
     two logs apart by precisely the offset and reads as a real lag.
  2. `~unrel` SAYS WHEN THAT TRANSLATION CANNOT BE TRUSTED — extrapolated far from any
     clock_sync, or interpolated off a jittery or thinly-sampled one. It has to appear
     exactly when it should and never when it should not, because a row without it is
     being offered as a stamp you can order events by. It was computed and thrown away
     until this round, so nothing has ever checked it.
  3. THE FOUR NUMBERS TRAVEL TOGETHER. `_spread` quotes p50/p95/max AND n, and n is not
     decoration: with no samples the percentiles come back None, and "nothing was slow"
     and "nothing was measured" are not the same report.

HOW THESE CHECKS EARN THEIR COVERAGE. Records go onto a real .jsonl and come back
through `rovlog.load_jsonl` — the same reader the CLI uses — and then through the same
`merge`, `diverge` and `timeline` the CLI calls; several go through `rovlog.main` from
argv to stdout, and one writes its Pi log with the real `BlackBox` recorder so the shape
the vehicle records is provably the shape the analyser reads. Nothing pokes at a merged
record to make an assertion true.

WHY THERE ARE NO FIXTURE FILES. Every log here is built in the test, a few records wide,
with the timestamps and offsets chosen so the right answer can be worked out on paper. A
committed fixture is a file somebody regenerates to make a failing check pass, and
regenerating it IS lowering the bar; there is nothing here to regenerate.

To see any of this by hand:
    python -m blackbox.rovlog timeline <session> --around <ms> --window 30
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from blackbox import rovlog
from blackbox.recorder import BlackBox

# The thresholds `Offset.reliable_at` enforces, named once so the boundary checks below
# read as "this is the limit" rather than as three magic numbers. They are deliberately
# NOT imported from the module: a constant read out of the code under test cannot
# disagree with it, which is the entire job of a boundary check.
MAX_MS_FROM_A_SYNC = 30_000  # beyond this the translation is extrapolation
MAX_JITTER_MS = 50  # a link this unsteady cannot pin an offset
MIN_SYNC_SAMPLES = 3  # an offset off fewer round trips than this is a guess

# Absence, spelled. A field left out of a record is not the same as a field carrying a
# value, and several checks below turn on exactly that difference.
OMIT = object()


def _jsonl(path: Path, records) -> Path:
    """Write records the way both recorders do: one compact JSON object per line."""
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    return path


def ev(t, name, d=None, c_id=None):
    """One log record, the shape `BlackBox.event` writes and `recorder.js` uploads."""
    rec = {"t": t, "e": name}
    if c_id:
        rec["c_id"] = c_id
    if d is not None:
        rec["d"] = d
    return rec


def sync(t, offset_ms, jitter_ms=2.0, samples=8):
    """One clock_sync record — the shape `REC.onPong` in client/js/recorder.js logs.

    `jitter_ms` or `samples` may be OMIT, which leaves the key out of the record
    entirely. That is not a hypothetical: it is what a log written by any recorder that
    did not measure that quantity looks like, and what the reader does with it is a
    verdict about a timestamp somebody will order events by.
    """
    d = {"rtt_ms": 30.0, "offset_ms": offset_ms}
    if samples is not OMIT:
        d["samples"] = samples
    if jitter_ms is not OMIT:
        d["jitter_ms"] = jitter_ms
    return {"t": t, "e": "clock_sync", "d": d}


class LogCase(unittest.TestCase):
    """A session on disk, read back through the shipped reader.

    Subclasses call `self.logs(nav=[...], client=[...])` and get exactly what
    `rovlog.main` gets: the output of `load_jsonl` over two real files.
    """

    SESSION = "20260101T000000Z"

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="neptune-rovlog-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def logs(self, nav=(), client=()):
        nav_p, cli_p = rovlog.session_files(self.SESSION, str(self.dir))
        _jsonl(nav_p, nav)
        _jsonl(cli_p, client)
        return rovlog.load_jsonl(nav_p), rovlog.load_jsonl(cli_p)

    def merged(self, nav=(), client=()):
        return rovlog.merge(*self.logs(nav=nav, client=client))

    def rendered(self, nav=(), client=(), around=0.0, window=30.0):
        nav_l, cli_l = self.logs(nav=nav, client=client)
        return rovlog.timeline(nav_l, cli_l, around, window)

    def only(self, merged, name):
        """The one merged record for event `name` — asserted to be one, not picked."""
        hits = [r for r in merged if r.get("e") == name]
        self.assertEqual(len(hits), 1, f"expected exactly one {name!r} record, got {len(hits)}")
        return hits[0]

    def line_for(self, lines, needle):
        hits = [ln for ln in lines if needle in ln]
        self.assertEqual(len(hits), 1, f"expected exactly one rendered row containing {needle!r}:\n" + "\n".join(lines))
        return hits[0]


# --------------------------------------------------------------------------- the reader
class TheReader(LogCase):
    """`load_jsonl` is the only door into this module. What it does with a file it
    cannot read decides whether everything downstream is counting the whole log."""

    def test_a_line_that_will_not_parse_is_reported_and_not_quietly_dropped(self):
        """A corrupt record is a record. Dropping it shrinks every count in every report
        by one and tells nobody, which is a log silently rewriting itself on read."""
        path = self.dir / "navigation_corrupt.jsonl"
        path.write_text('{"t":1,"e":"session_start"}\nhalf a line{"t":2\n\n{"t":3,"e":"cmd_recv"}\n', encoding="utf-8")
        recs = rovlog.load_jsonl(path)
        self.assertEqual(len(recs), 3, "the unparseable line vanished instead of being reported")
        self.assertIn("_parse_error", recs[1], "a line that would not parse came back as an ordinary record")
        self.assertIn("half a line", recs[1]["_parse_error"], "the offending text was not kept for the reader")
        self.assertEqual([recs[0]["e"], recs[2]["e"]], ["session_start", "cmd_recv"], "good lines were disturbed")

    def test_a_session_with_no_client_log_still_replays_the_vehicle_side(self):
        """The commonest real session: the sub recorded, nobody was connected. An
        absent client file is absent, not an error and not an empty Pi log."""
        nav_p, cli_p = rovlog.session_files(self.SESSION, str(self.dir))
        _jsonl(nav_p, [ev(10_000.0, "cmd_recv"), ev(10_050.0, "cmd_apply", {"name": "arm"})])
        self.assertFalse(cli_p.exists(), "this check is meaningless if the client file is there")
        lines = rovlog.timeline(rovlog.load_jsonl(nav_p), rovlog.load_jsonl(cli_p), 10_000.0, 5.0)
        self.assertEqual(len(lines), 2, "\n".join(lines))
        for line in lines:
            self.assertNotIn("~unrel", line, "a Pi row was marked as an untrustworthy translation")


# ------------------------------------------------------------------- onto_pi: the claim
class ClockTranslationOntoPi(LogCase):
    """§6/§12 — the correction applies to the client's clock and to nothing else."""

    def test_a_pi_stamp_comes_back_untouched_and_a_client_stamp_moves_by_the_offset(self):
        merged = self.merged(
            nav=[ev(1000.0, "cmd_recv")],
            client=[sync(1000.0, 250.0), ev(1000.0, "cmd_send")],
        )
        pi = self.only(merged, "cmd_recv")
        cl = self.only(merged, "cmd_send")
        self.assertEqual(pi["at"], 1000.0, "a Pi timestamp was moved; the Pi's clock IS the timebase")
        self.assertNotIn("raw_t", pi, "a Pi row carries an untranslated original, implying it was translated at all")
        self.assertEqual(cl["at"], 1250.0, "the client stamp was not carried onto the Pi timebase by its own offset")
        self.assertEqual(cl["raw_t"], 1000.0, "the untranslated client stamp did not survive beside the translation")
        self.assertEqual(cl["side"], "client")
        self.assertEqual(pi["side"], "pi")

    def test_a_client_clock_running_ahead_of_the_pi_is_moved_backwards(self):
        """The offset is signed and the sign is load-bearing. An implementation that
        took a magnitude would put this row 400 ms into the future instead of the past."""
        merged = self.merged(client=[sync(1000.0, -400.0), ev(1000.0, "cmd_send")])
        self.assertEqual(self.only(merged, "cmd_send")["at"], 600.0)

    def test_the_offset_is_interpolated_between_syncs_and_held_flat_outside_them(self):
        """Two syncs 10 s apart, +100 ms then +300 ms. Everything between them is worked
        out on paper; everything outside holds the nearest sync's value rather than
        running the trend off the end of the measurements."""
        client = [
            sync(10_000.0, 100.0),
            sync(20_000.0, 300.0),
            ev(5_000.0, "before"),
            ev(12_500.0, "quarter"),
            ev(15_000.0, "midpoint"),
            ev(25_000.0, "after"),
        ]
        merged = self.merged(client=client)
        self.assertEqual(self.only(merged, "before")["at"], 5_100.0, "the first sync's offset was not held backwards")
        self.assertEqual(self.only(merged, "quarter")["at"], 12_650.0, "the interpolation is not linear in time")
        self.assertEqual(self.only(merged, "midpoint")["at"], 15_200.0, "the midpoint did not land on the mean offset")
        self.assertEqual(self.only(merged, "after")["at"], 25_300.0, "the last sync's offset was not held forwards")

    def test_with_no_sync_at_all_the_stamp_is_left_where_it_is_and_said_to_be_unreliable(self):
        """Nothing is invented in place of a measurement that was never taken — and
        because nothing was invented, the row cannot be read as aligned either."""
        cl = self.only(self.merged(client=[ev(1000.0, "cmd_send")]), "cmd_send")
        self.assertEqual(cl["at"], cl["raw_t"], "an offset was conjured from a session that never synced")
        self.assertTrue(cl.get("align_unreliable"), "an unalignable stamp was presented as aligned")

    def test_the_cross_log_latency_is_the_real_delay_and_not_the_clock_offset(self):
        """The payoff, and the one bug `onto_pi` exists to prevent.

        The operator's intent is stamped at 1000 on a client clock 200 ms behind the
        Pi's; the Pi applies it at 1300 on its own. The delay is 100 ms. Correct the
        wrong side and you get 500; correct neither and you get 300 — and 300 ms of
        invented lag between a button and a thruster is a fault report about a link
        that was fine.
        """
        nav, client = self.logs(
            nav=[ev(1300.0, "cmd_apply", {"name": "arm"}, c_id="c-1")],
            client=[sync(0.0, 200.0), ev(1000.0, "cmd_intent", {"name": "arm"}, c_id="c-1")],
        )
        lat = rovlog.diverge(nav, client)["latency_ms"]["intent_to_apply"]
        self.assertEqual(lat["n"], 1, "the two sides were not correlated by c_id at all")
        self.assertEqual(
            lat["p50"],
            100.0,
            "intent→apply is not the true delay: 300 means the client's clock was never "
            "corrected, 500 means the correction landed on the Pi's side or with the sign "
            "flipped, and each of those is a lag this link never had",
        )

    def test_a_stage_that_never_leaves_the_pi_is_untouched_by_a_large_client_offset(self):
        """apply→ack is the Pi answering itself. A nine-second client offset is present
        in the same session and must not reach a figure neither end of which is the
        client's — that is the same bug in the other direction."""
        nav, client = self.logs(
            nav=[ev(1000.0, "cmd_apply", c_id="c-1"), ev(1040.0, "cmd_ack_send", c_id="c-1")],
            client=[sync(0.0, 9000.0)],
        )
        lat = rovlog.diverge(nav, client)["latency_ms"]["apply_to_ack"]
        self.assertEqual(lat["n"], 1)
        self.assertEqual(lat["p50"], 40.0, "a Pi-to-Pi stage was corrected by the client's clock offset")


# ----------------------------------------------------------------- the ~unrel marker
class TheUnreliableMarker(LogCase):
    """The honesty claim of the whole file: which timestamps may be read as an ordering.

    `reliable_at` decides it, `merge` records it and `timeline` prints it. Every check
    here drives all three, because the verdict only means anything where a person sees
    it. Both halves are asserted every time — that the mark appears when it should, and
    that it stays away when it should not, since a mark on every row is as useless as a
    mark on none.
    """

    def marked(self, event_t, syncs=()):
        """Is the client row at `event_t` marked, in the record AND on the page?"""
        client = list(syncs) + [ev(event_t, "cmd_send")]
        row = self.only(self.merged(client=client), "cmd_send")
        lines = self.rendered(client=client, around=event_t, window=3600.0)
        printed = "~unrel" in self.line_for(lines, "cmd_send")
        self.assertEqual(
            bool(row.get("align_unreliable")),
            printed,
            "merge and timeline disagree about this row: the verdict was reached and "
            "then not shown, which is how the mark came to be computed and thrown away "
            "in the first place",
        )
        return printed

    def test_a_near_steady_well_sampled_sync_carries_no_mark(self):
        self.assertFalse(
            self.marked(1_000.0, [sync(0.0, 12.0, jitter_ms=5.0, samples=8)]),
            "a translation off a good sync one second old was disowned; a mark on every " "row tells a reader nothing",
        )

    def test_extrapolating_far_from_every_sync_is_marked(self):
        self.assertTrue(
            self.marked(45_000.0, [sync(0.0, 12.0)]),
            "a stamp translated by an offset measured 45 s earlier was offered as trustworthy",
        )

    def test_a_jittery_sync_cannot_certify_the_rows_around_it(self):
        self.assertTrue(
            self.marked(1_000.0, [sync(0.0, 12.0, jitter_ms=180.0)]),
            "an offset measured across a link swinging by 180 ms was treated as pinned",
        )

    def test_a_sync_resting_on_too_few_round_trips_cannot_certify_them_either(self):
        self.assertTrue(
            self.marked(1_000.0, [sync(0.0, 12.0, samples=1)]),
            "an offset from a single round trip was treated as a measurement",
        )

    def test_a_session_that_never_synced_marks_every_client_row(self):
        self.assertTrue(
            self.marked(1_000.0, []),
            "with no clock_sync anywhere the offset is zero by default, and a defaulted "
            "offset presented without the mark says the two clocks agreed",
        )

    def test_a_sync_that_never_measured_its_own_jitter_certifies_nothing(self):
        """ABSENT IS NOT ZERO, and here it decides whether a stamp looks measured.

        A clock_sync that carries an offset and a sample count but no `jitter_ms` never
        said how steady the link was. Defaulting that to 0 does not mean "no information";
        0 ms is the steadiest link anyone has ever measured, so the sync with the least
        to say about itself certifies more rows than any real one — §24.1's rule about a
        cannot-tell default that is itself a measurement, landing in the one function
        whose whole job is to withhold trust. The missing sample count is already handled
        the honest way, by defaulting to a value that fails; both halves of the same
        question must answer it the same way.
        """
        self.assertTrue(
            self.marked(1_000.0, [sync(0.0, 12.0, jitter_ms=OMIT, samples=8)]),
            "a sync that never reported its jitter was read as a perfectly steady one",
        )

    def test_a_sync_that_never_counted_its_round_trips_certifies_nothing(self):
        self.assertTrue(
            self.marked(1_000.0, [sync(0.0, 12.0, jitter_ms=5.0, samples=OMIT)]),
            "a sync that never said how many round trips it averaged was believed anyway",
        )

    def test_the_thresholds_sit_exactly_where_the_file_says_they_do(self):
        """On the limit is inside it; one step past is outside. A check that only ever
        probes the middle of a range cannot tell a `>` from a `>=`, and would pass just
        as happily against a rule that had drifted by a whole second."""
        good = sync(0.0, 12.0, jitter_ms=5.0, samples=8)
        self.assertFalse(self.marked(float(MAX_MS_FROM_A_SYNC), [good]), "exactly at the age limit is still inside it")
        self.assertTrue(self.marked(MAX_MS_FROM_A_SYNC + 1.0, [good]), "one millisecond past the age limit is outside")

        at_limit = sync(0.0, 12.0, jitter_ms=float(MAX_JITTER_MS), samples=8)
        over = sync(0.0, 12.0, jitter_ms=MAX_JITTER_MS + 0.1, samples=8)
        self.assertFalse(self.marked(1_000.0, [at_limit]), "exactly at the jitter limit is still inside it")
        self.assertTrue(self.marked(1_000.0, [over]), "a hair over the jitter limit is outside it")

        enough = sync(0.0, 12.0, jitter_ms=5.0, samples=MIN_SYNC_SAMPLES)
        thin = sync(0.0, 12.0, jitter_ms=5.0, samples=MIN_SYNC_SAMPLES - 1)
        self.assertFalse(self.marked(1_000.0, [enough]), "the minimum sample count is meant to be sufficient")
        self.assertTrue(self.marked(1_000.0, [thin]), "one sample short of the minimum was accepted")

    def test_the_verdict_is_taken_per_row_and_not_once_per_session(self):
        """One sync, two client events: one beside it, one a minute later. A per-session
        verdict would mark both or neither, and either way the reader loses the ability
        to tell which rows of a long dive can be ordered against the Pi's."""
        client = [sync(0.0, 12.0), ev(1_000.0, "near"), ev(60_000.0, "far")]
        merged = self.merged(client=client)
        self.assertFalse(self.only(merged, "near").get("align_unreliable"))
        self.assertTrue(self.only(merged, "far").get("align_unreliable"))

        lines = self.rendered(client=client, around=30_000.0, window=60.0)
        self.assertNotIn("~unrel", self.line_for(lines, "near"), "a well-aligned row was marked")
        self.assertIn("~unrel", self.line_for(lines, "far"), "a row translated off a minute-old sync was not marked")

    def test_the_nearest_sync_decides_not_the_worst_one_in_the_log(self):
        """A link that went bad late must not retroactively disown the rows a good
        earlier sync covered — nor may the good one vouch for the bad one's neighbours."""
        client = [
            sync(0.0, 12.0, jitter_ms=2.0, samples=10),
            sync(20_000.0, 12.0, jitter_ms=400.0, samples=10),
            ev(1_000.0, "beside_the_good_one"),
            ev(19_000.0, "beside_the_bad_one"),
        ]
        merged = self.merged(client=client)
        self.assertFalse(
            self.only(merged, "beside_the_good_one").get("align_unreliable"),
            "a later bad sync condemned a row a good one already covered",
        )
        self.assertTrue(
            self.only(merged, "beside_the_bad_one").get("align_unreliable"),
            "a good sync 19 s away vouched for a row sitting next to a jittery one",
        )

    def test_a_pi_row_is_never_marked_because_a_pi_stamp_was_never_translated(self):
        """The mark qualifies a translation. On the Pi's own rows there is nothing to
        qualify, and marking them would say the vehicle's clock needs alignment with
        itself — even in a session where nothing else can be aligned at all."""
        nav = [ev(1_000.0, "cmd_recv"), ev(2_000.0, "cmd_apply", {"name": "arm"})]
        client = [ev(1_500.0, "cmd_send")]  # no clock_sync anywhere in this session
        for row in self.merged(nav=nav, client=client):
            if row["side"] == "pi":
                self.assertNotIn("align_unreliable", row, f"a Pi row was marked as an untrusted translation: {row}")
            else:
                self.assertTrue(row.get("align_unreliable"), f"an unalignable client row went unmarked: {row}")

        lines = self.rendered(nav=nav, client=client, around=1_500.0, window=5.0)
        self.assertEqual(len(lines), 3, "\n".join(lines))
        self.assertNotIn("~unrel", self.line_for(lines, "cmd_recv"))
        self.assertNotIn("~unrel", self.line_for(lines, "cmd_apply"))
        self.assertIn("~unrel", self.line_for(lines, "cmd_send"))


# ------------------------------------------------------------------ the four numbers
class TheSpreadOfMilliseconds(LogCase):
    """p50 / p95 / max / n, reached the way the report reaches them.

    Every figure below comes out of `diverge`, from command pairs correlated by c_id —
    not from calling the helper — so what is checked is the number an operator reads.
    """

    def spread_of(self, latencies):
        """apply→ack for one command per latency. Pi-to-Pi, so no clock is involved."""
        nav = []
        for i, lat in enumerate(latencies):
            cid = "c-%04d" % i
            began = 1_000.0 + i * 1_000.0
            nav.append(ev(began, "cmd_apply", {"name": "arm"}, c_id=cid))
            nav.append(ev(began + lat, "cmd_ack_send", {"ok": True}, c_id=cid))
        nav_l, cli_l = self.logs(nav=nav)
        return rovlog.diverge(nav_l, cli_l)["latency_ms"]["apply_to_ack"]

    def test_the_percentiles_land_where_a_known_distribution_puts_them(self):
        """101 commands, delays 0..100 ms, one per millisecond. The median is 50, the
        95th is 95 and the worst is 100 — arithmetic anyone can redo on paper, which is
        the only kind of expectation that can catch an off-by-one in the ranking."""
        got = self.spread_of([float(ms) for ms in range(101)])
        self.assertEqual(got, {"p50": 50.0, "p95": 95.0, "max": 100.0, "n": 101})

    def test_one_measurement_is_reported_as_one_measurement(self):
        got = self.spread_of([42.5])
        self.assertEqual(got["p50"], 42.5)
        self.assertEqual(got["p95"], 42.5)
        self.assertEqual(got["max"], 42.5)
        self.assertEqual(got["n"], 1, "a p95 quoted off a single command must say it is off a single command")

    def test_identical_measurements_collapse_to_that_value_and_keep_their_count(self):
        got = self.spread_of([7.5] * 9)
        self.assertEqual((got["p50"], got["p95"], got["max"]), (7.5, 7.5, 7.5))
        self.assertEqual(got["n"], 9)

    def test_nothing_measured_is_not_nothing_slow(self):
        """The whole reason n travels with the other three.

        A session where no command was ever correlated has no latency to quote. Zeros
        here would read as an instant link; None plus a count of nothing reads as what
        it is, which is that nobody timed anything.
        """
        got = self.spread_of([])
        self.assertIsNone(got["p50"], "an unmeasured p50 came back as a number")
        self.assertIsNone(got["p95"], "an unmeasured p95 came back as a number")
        self.assertIsNone(got["max"], "an unmeasured maximum came back as a number")
        self.assertEqual(got["n"], 0, "the count that distinguishes 'nothing slow' from 'nothing measured' is missing")

    def test_a_thin_sample_still_carries_the_count_that_discounts_it(self):
        """Three commands cannot produce a 95th percentile, and this one is simply the
        worst of the three. Nothing stops the figure being printed; what stops it being
        believed is the n beside it."""
        got = self.spread_of([10.0, 20.0, 900.0])
        self.assertEqual(got["n"], 3)
        self.assertEqual(got["p95"], got["max"], "with three samples the p95 is the maximum, by construction")

    def test_the_staleness_block_quotes_the_same_four_numbers_and_counts_bad_windows(self):
        """Frame age is the other figure quoted this way, and it arrives from the client
        side, so it also proves `_spread` is fed by more than one path."""
        client = [
            ev(1_000.0 + i, "tlm_rx", {"seq_from": i, "seq_to": i, "max_age_ms": age})
            for i, age in enumerate([100, 200, 300, 600, 900])
        ]
        nav_l, cli_l = self.logs(client=client)
        stale = rovlog.diverge(nav_l, cli_l)["staleness_ms"]
        self.assertEqual(stale["p50"], 300.0)
        self.assertEqual(stale["p95"], 900.0)
        self.assertEqual(stale["max"], 900.0)
        self.assertEqual(stale["n"], 5)
        self.assertEqual(stale["windows_over_500ms"], 2, "the count of windows over the threshold is wrong")

    def test_no_stale_windows_and_no_windows_at_all_are_told_apart(self):
        """`windows_over_500ms: 0` is the same character either way. Only n says whether
        the client reported healthy frame ages or never reported any."""
        nav_l, cli_l = self.logs()
        stale = rovlog.diverge(nav_l, cli_l)["staleness_ms"]
        self.assertEqual(stale["windows_over_500ms"], 0)
        self.assertEqual(stale["n"], 0)
        self.assertIsNone(stale["p50"], "a staleness median was quoted for a session that reported no frame ages")

    def test_every_millisecond_figure_in_the_report_carries_its_count(self):
        """Structural, on purpose: a stage added later that quotes percentiles without
        an n has reintroduced exactly the ambiguity `_spread` exists to remove."""
        nav_l, cli_l = self.logs(
            nav=[ev(1000.0, "cmd_apply", c_id="c-1"), ev(1040.0, "cmd_ack_send", c_id="c-1")],
        )
        rep = rovlog.diverge(nav_l, cli_l)
        blocks = dict(rep["latency_ms"])
        blocks["staleness_ms"] = rep["staleness_ms"]
        for name, block in blocks.items():
            self.assertIn("n", block, f"{name} quotes percentiles with no sample count beside them")
            for key in ("p50", "p95", "max"):
                self.assertIn(key, block, f"{name} is missing {key}")


# ------------------------------------------------------- the clock, and a silent side
class TheClockReportAndTheSilences(LogCase):
    """The other two things `diverge` says about time.

    `~unrel` is a verdict on one row. These are the session-wide companions: what the
    offset itself did over the dive, and which side stopped talking. Both are places
    where a confident-looking zero would be a lie about a measurement nobody took.
    """

    def clock_from(self, syncs):
        nav_l, cli_l = self.logs(nav=[ev(0.0, "session_start")], client=list(syncs))
        return rovlog.diverge(nav_l, cli_l)["clock"]

    def test_a_single_sync_cannot_report_drift_and_says_so_instead_of_reporting_none(self):
        """Drift is a difference between two measurements. With one there is no
        difference to take, and `0.0 ms/s` would be a claim that the two clocks kept
        perfect pace — the strongest thing this report can say, from one sample."""
        rep = self.clock_from([sync(0.0, 100.0)])
        self.assertEqual(rep["samples"], 1)
        self.assertNotIn("drift_ms_per_s", rep, "a drift figure was quoted from a single clock sample")
        self.assertIn("insufficient", rep["note"])

    def test_drift_is_quoted_per_second_over_the_span_that_was_measured(self):
        """Three syncs 10 s apart, the offset walking 100 → 120 → 140 ms: 40 ms of
        walk over 20 s is 2 ms per second. Steady walk is drift, not a jump."""
        rep = self.clock_from([sync(0.0, 100.0), sync(10_000.0, 120.0), sync(20_000.0, 140.0)])
        self.assertEqual(rep["samples"], 3)
        self.assertEqual(rep["offset_first_ms"], 100.0)
        self.assertEqual(rep["offset_last_ms"], 140.0)
        self.assertEqual(rep["drift_ms_per_s"], 2.0, "drift is not the offset's walk divided by the span in seconds")
        self.assertEqual(rep["jumps"], [], "a steady walk was reported as a clock jump")
        self.assertEqual(rep["sync_gaps_ms"], [], "syncs ten seconds apart were reported as a gap in syncing")

    def test_a_step_between_adjacent_syncs_is_named_with_its_time_and_its_size(self):
        """A clock that stepped is the one thing that invalidates the alignment either
        side of it, so the step gets a timestamp an operator can go and look at."""
        rep = self.clock_from([sync(0.0, 100.0), sync(10_000.0, 100.0), sync(30_000.0, 450.0)])
        self.assertEqual(rep["jumps"], [{"at": 30_000.0, "delta_ms": 350.0}])
        self.assertEqual(rep["sync_gaps_ms"], [20_000.0], "twenty seconds without a sync went unreported")

    def test_a_side_that_went_quiet_while_the_other_talked_is_named(self):
        """The vehicle logged nothing for eight seconds while the console logged three
        events — which is a Pi that stopped, not a link that dropped, and the two are
        told apart only by asking what the OTHER side was doing at the time."""
        nav = [ev(t, "tlm_tx", {"seq_from": 1, "seq_to": 2}) for t in (0.0, 1_000.0, 2_000.0)]
        nav.append(ev(10_000.0, "tlm_tx", {"seq_from": 3, "seq_to": 4}))
        client = [sync(0.0, 0.0), ev(3_000.0, "tlm_rx"), ev(5_000.0, "tlm_rx"), ev(7_000.0, "tlm_rx")]
        nav_l, cli_l = self.logs(nav=nav, client=client)
        outages = rovlog.diverge(nav_l, cli_l)["one_sided_outages"]
        self.assertEqual(
            outages,
            [{"side_silent": "pi", "from": 2_000.0, "to": 10_000.0, "gap_ms": 8_000.0}],
            "the eight seconds of Pi silence the console talked through was not reported",
        )

    def test_one_sided_outages_need_both_sides_and_say_so_rather_than_reporting_none(self):
        """With nothing from the console there is no window to compare against, and an
        empty list here would read as a dive where neither end ever went quiet."""
        nav_l, cli_l = self.logs(nav=[ev(0.0, "tlm_tx"), ev(60_000.0, "tlm_tx")])
        outages = rovlog.diverge(nav_l, cli_l)["one_sided_outages"]
        self.assertIsInstance(outages, dict, "a one-sided session reported a list of outages it could not have found")
        self.assertIn("need both sides", outages["note"])


# --------------------------------------------------------------------- the timeline
class TheTimeline(LogCase):
    """What an operator actually reads after an incident."""

    def test_only_the_rows_inside_the_window_are_rendered_and_the_edges_are_included(self):
        nav = [ev(float(t), "mark_%d" % t) for t in (0, 5_000, 10_000, 15_000, 20_000)]
        lines = self.rendered(nav=nav, around=10_000.0, window=5.0)
        self.assertEqual(len(lines), 3, "\n".join(lines))
        for inside in ("mark_5000", "mark_10000", "mark_15000"):
            self.line_for(lines, inside)
        for outside in ("mark_0 ", "mark_20000"):
            self.assertNotIn(outside, "\n".join(lines), f"{outside} is outside the window and was rendered anyway")

    def test_each_row_names_its_side_its_event_and_its_correlation_id(self):
        nav = [ev(10_000.0, "cmd_apply", {"name": "arm"}, c_id="0123456789abcdef")]
        client = [sync(9_900.0, 0.0), ev(9_900.0, "cmd_send", c_id="0123456789abcdef")]
        lines = self.rendered(nav=nav, client=client, around=10_000.0, window=5.0)
        pi_row = self.line_for(lines, "cmd_apply")
        cl_row = self.line_for(lines, "cmd_send")
        self.assertIn("PI", pi_row)
        self.assertIn("CL", cl_row)
        self.assertNotIn("CL", pi_row, "a Pi row was tagged as the client's")
        self.assertIn("[01234567]", pi_row, "the correlation id that ties the two sides together is not shown")
        self.assertIn("[01234567]", cl_row)
        self.assertIn('{"name":"arm"}', pi_row, "the record's own payload is missing from its row")

    def test_rows_are_ordered_on_the_pi_timebase_and_not_on_the_clock_they_were_written_on(self):
        """The exact wrong conclusion this view invites.

        The console stamped its send at 1000 and the Pi logged its receipt at 3000, so
        raw the client looks 2 s early — but the client's clock is 5 s behind, and on the
        Pi's timebase the send is at 6000, AFTER the receipt. Ordering the rows on the
        raw column reads "the client saw it first" out of arithmetic nobody did.
        """
        nav = [ev(3_000.0, "cmd_recv")]
        client = [sync(0.0, 5_000.0), ev(1_000.0, "cmd_send")]
        lines = self.rendered(nav=nav, client=client, around=4_500.0, window=5.0)
        pi_row = self.line_for(lines, "cmd_recv")
        cl_row = self.line_for(lines, "cmd_send")
        self.assertLess(
            lines.index(pi_row),
            lines.index(cl_row),
            "the rows were ordered on the client's own clock, so the console appears to "
            "have sent a command two seconds before the Pi received it:\n" + "\n".join(lines),
        )

        self.assertIn("6000.0", cl_row, "the translated stamp is not the one printed")
        self.assertNotIn("1000.0", cl_row, "the raw client clock was printed in the column meant to be comparable")

    def test_the_mark_sits_between_the_time_it_qualifies_and_the_side_it_came_from(self):
        """Placement is the point. The mark is a caveat on the timestamp, and a caveat
        that has drifted to the end of the line is a caveat attached to the payload."""
        client = [ev(1_000.0, "cmd_send")]  # no sync: the translation cannot be stood behind
        row = self.line_for(self.rendered(client=client, around=1_000.0, window=5.0), "cmd_send")
        self.assertLess(row.index("1000.0"), row.index("~unrel"), "the mark was printed before the time it qualifies")
        self.assertLess(row.index("~unrel"), row.index("CL"), "the mark drifted past the column it belongs beside")

    def test_a_telemetry_record_is_summarised_rather_than_dumped(self):
        """Twenty-five fields of JSON per row is a wall nobody reads, and a timeline
        nobody reads is not a timeline."""
        frame = {
            "armed": True,
            "depth": 3.2,
            "heading": 271.5,
            "leak_state": "NORMAL",
            "battery_v": 11.8,
            "battery_band": "ok",
            "ballast_level": 0.42,
            "ballast_target": 0.5,
            "speed_ms": 0.31,
            "speed_src": "paddle",
            "pressure": 16.4,
            "left": 0.2,
            "right": 0.2,
        }
        row = self.line_for(self.rendered(nav=[ev(10_000.0, "tlm", frame)], around=10_000.0, window=5.0), "tlm")
        for expected in (
            "ARMED",
            "depth=3.2",
            "hdg=271.5",
            "leak=NORMAL",
            "batt=11.8(ok)",
            "ball=0.42",
            "spd=0.31/paddle",
        ):
            self.assertIn(expected, row, f"{expected} is not on the summarised row:\n{row}")
        self.assertNotIn("ballast_target", row, "the whole record was dumped instead of summarised")
        self.assertNotIn("pressure", row)

    def test_the_flags_worth_stopping_on_are_shouted_and_the_dead_chips_are_named(self):
        frame = {
            "armed": False,
            "snagged": True,
            "gyro_only": True,
            "ballast_needs_rehome": True,
            "leak_probe_fault": "stuck-wet",
            "sensor_faults": ["ms5837", "bno085"],
            "nav_used": False,
            "n_changes": 4,
        }
        row = self.line_for(self.rendered(nav=[ev(10_000.0, "tlm_state", frame)], around=10_000.0, window=5.0), "tlm")
        for expected in ("safe", "SNAGGED", "gyro-only", "REHOME", "probe-fault=stuck-wet"):
            self.assertIn(expected, row, f"{expected} is not on the row:\n{row}")
        self.assertIn(
            "DEAD=ms5837,bno085",
            row,
            "the chips that stopped answering are not named beside the blanks they caused",
        )
        self.assertIn("nav-quiet", row, "a frame that took nothing from navigation did not say so")
        self.assertIn("(+4 coalesced)", row, "a coalesced record did not say how many changes it stands for")

    def test_a_reading_that_was_not_taken_is_a_question_mark_and_never_the_word_none(self):
        """§24.1 in the reader. Cannot-tell has one shape here, and the shape is `?`.

        The word None is what a value looks like when it falls through a formatter that
        was not asked the question, and a column that spells absence three different ways
        is a column an eye cannot scan. Every reading on this row is absent, so nothing
        on it may read as a value.
        """
        frame = {
            "armed": False,
            "depth": None,
            "heading": None,
            "leak_state": None,
            "battery_v": None,
            "battery_band": None,
            "ballast_level": None,
            "speed_ms": None,
            "speed_src": None,
        }
        row = self.line_for(self.rendered(nav=[ev(10_000.0, "tlm", frame)], around=10_000.0, window=5.0), "tlm")
        for expected in ("depth=?", "hdg=?", "ball=?", "spd=?/?", "leak=?", "batt=?(?)"):
            self.assertIn(expected, row, f"an unanswered reading is not shown as cannot-tell ({expected}):\n{row}")
        self.assertNotIn("None", row, "absence is spelled as the word None on a row the eye scans for '?'")

    def test_a_zero_is_a_measurement_and_keeps_its_number(self):
        """The other half of the same rule. 0.0 depth is the surface and 0.0 heading is
        due north; both are readings a sensor took, and a reader that blanked them would
        be hiding a measurement exactly as badly as one that invented it."""
        frame = {
            "armed": True,
            "depth": 0.0,
            "heading": 0.0,
            "ballast_level": 0.0,
            "leak_state": "NORMAL",
            "battery_v": 11.8,
            "battery_band": "ok",
            "speed_ms": 0.0,
            "speed_src": "paddle",
        }
        row = self.line_for(self.rendered(nav=[ev(10_000.0, "tlm", frame)], around=10_000.0, window=5.0), "tlm")
        self.assertIn("depth=0.0", row, "a real surface reading was blanked")
        self.assertIn("hdg=0.0", row, "a real due-north bearing was blanked")
        self.assertIn("ball=0.0", row, "an empty-but-known syringe was blanked")
        self.assertIn("spd=0.0/paddle", row, "a real stationary reading was blanked")
        # Every reading on this row was taken, so nothing on it may wear the shape the
        # row above uses for readings that were not.
        self.assertNotIn("=?", row, f"a measurement was shown as cannot-tell:\n{row}")


# ------------------------------------------------------------- recorder → reader → CLI
class ThroughTheRealRecorderAndTheCli(LogCase):
    """The seam, end to end: what the vehicle writes, read by the command an operator
    types. Both halves are the shipped ones — `BlackBox` writes the Pi log and appends
    the client's, `rovlog.main` parses argv and prints."""

    def session(self):
        box = BlackBox(log_dir=self.dir)
        self.addCleanup(box.close)
        cid = "0123456789abcdef"
        box.event("cmd_recv", None, cid, t=10_000.0)
        box.event("cmd_apply", {"name": "arm"}, cid, t=10_020.0)
        box.event("tlm", {"armed": True, "depth": None, "sensor_faults": ["ms5837"]}, t=10_030.0)
        # Uploaded verbatim, the way the console's ring uploads it — and with no
        # clock_sync in it, which is what a session whose sync never made it up the
        # tether looks like on disk.
        box.client_append(box.session_id, [ev(9_900.0, "cmd_send", c_id=cid)])
        return box

    def run_cli(self, argv):
        out = StringIO()
        with redirect_stdout(out):
            rc = rovlog.main(argv)
        return rc, out.getvalue()

    def test_the_timeline_command_reads_the_recorder_and_prints_the_mark(self):
        box = self.session()
        rc, text = self.run_cli(
            ["timeline", box.session_id, "--log-dir", str(self.dir), "--around", "10000", "--window", "5"]
        )
        self.assertEqual(rc, 0, text)
        for expected in ("cmd_recv", "cmd_apply", "cmd_send", "depth=?", "DEAD=ms5837"):
            self.assertIn(expected, text, f"{expected} did not survive the recorder → reader → CLI path:\n{text}")
        client_row = [ln for ln in text.splitlines() if "cmd_send" in ln]
        self.assertEqual(len(client_row), 1, text)
        self.assertIn(
            "~unrel",
            client_row[0],
            "a client stamp translated with no clock_sync in the session reached the "
            "operator's terminal looking like a measurement",
        )
        for line in text.splitlines():
            if "cmd_recv" in line or "cmd_apply" in line:
                self.assertNotIn("~unrel", line, f"a Pi row was marked as an untrusted translation:\n{line}")

    def test_the_merge_command_carries_the_side_the_original_stamp_and_the_verdict(self):
        box = self.session()
        nav_p, cli_p = rovlog.session_files(box.session_id, str(self.dir))
        rc, text = self.run_cli(["merge", str(nav_p), str(cli_p)])
        self.assertEqual(rc, 0, text)
        recs = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        client = [r for r in recs if r["side"] == "client"]
        pi = [r for r in recs if r["side"] == "pi"]
        self.assertEqual(len(client), 1, text)
        self.assertTrue(pi, "the recorder's own log did not reach the merged stream")
        self.assertEqual(client[0]["raw_t"], 9_900.0, "the untranslated client stamp is missing from the merge output")
        self.assertTrue(client[0].get("align_unreliable"), "the verdict did not reach the merge output")
        for row in pi:
            self.assertNotIn("align_unreliable", row)
        self.assertEqual([r["at"] for r in recs], sorted(r["at"] for r in recs), "the merged stream is not in order")

    def test_the_diverge_command_reports_a_session_nobody_timed_without_inventing_figures(self):
        box = self.session()
        rc, text = self.run_cli(["diverge", box.session_id, "--log-dir", str(self.dir)])
        self.assertEqual(rc, 0, text)
        rep = json.loads(text)
        self.assertEqual(rep["commands"]["sent"], 1, text)
        self.assertEqual(rep["commands"]["lost_outbound"], [], "a command the Pi logged as received was reported lost")
        for name, block in rep["latency_ms"].items():
            self.assertEqual(block["n"], 0, f"{name} counted a stage this session never completed")
            self.assertIsNone(block["p50"], f"{name} quoted a median with nothing to take a median of")
        self.assertEqual(rep["clock"]["samples"], 0, text)
        self.assertIn("insufficient clock_sync", rep["clock"]["note"], "a session that never synced did not say so")


if __name__ == "__main__":
    unittest.main()
