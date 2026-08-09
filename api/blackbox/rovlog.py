"""rovlog — blackbox analysis CLI (spec §6).

Two independent logs of the same events (Pi + client) let you locate a fault that
neither pins down alone. This tool aligns them on a common timebase (using the
logged clock offsets, never by rewriting timestamps) and reports where they diverge.

    rovlog merge     nav.jsonl client.jsonl    one time-aligned stream
    rovlog diverge   <session>                 the payoff: what the two logs disagree on
    rovlog telemetry <session>                 what the VEHICLE did: leak stages, ballast
                                               truth, speed source, snags, battery bands
    rovlog timeline  <session> --around T --window S   side-by-side text around an incident
    rovlog bundle    <session>                 incident zip

Run: python -m blackbox.rovlog <cmd> ...   (or via the installed console entry)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import zipfile
from pathlib import Path


# ---- io --------------------------------------------------------------------
def load_jsonl(path: Path) -> list[dict]:
    out = []
    if not path or not Path(path).exists():
        return out
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                out.append({"_parse_error": ln[:200]})
    return out


def _resolve_dir(log_dir: str | None) -> Path:
    if log_dir:
        return Path(log_dir)
    from .recorder import _resolve_log_dir

    return _resolve_log_dir()


def session_files(session: str, log_dir: str | None) -> tuple[Path, Path]:
    d = _resolve_dir(log_dir)
    return d / f"navigation_{session}.jsonl", d / f"client_{session}.jsonl"


# ---- clock alignment (§2) --------------------------------------------------
class Offset:
    """offset(client_t) → ms to ADD to a client timestamp to reach the Pi timebase.
    Built from the client's clock_sync samples; linearly interpolated between them,
    held flat outside the range. Never mutates the raw logs."""

    def __init__(self, client_events: list[dict]):
        self.samples: list[tuple[float, float, dict]] = []  # (client_t, offset_ms, meta)
        for r in client_events:
            if r.get("e") == "clock_sync":
                d = r.get("d", {})
                if "offset_ms" in d and "t" in r:
                    self.samples.append((r["t"], d["offset_ms"], d))
        self.samples.sort(key=lambda s: s[0])

    def has_data(self) -> bool:
        return bool(self.samples)

    def at(self, ct: float) -> float:
        s = self.samples
        if not s:
            return 0.0
        if ct <= s[0][0]:
            return s[0][1]
        if ct >= s[-1][0]:
            return s[-1][1]
        lo, hi = 0, len(s) - 1
        for i in range(len(s) - 1):
            if s[i][0] <= ct <= s[i + 1][0]:
                lo, hi = i, i + 1
                break
        (t0, o0, _), (t1, o1, _) = s[lo], s[hi]
        if t1 == t0:
            return o0
        return o0 + (o1 - o0) * (ct - t0) / (t1 - t0)

    def onto_pi(self, t: float, from_client: bool) -> float:
        """`t` on the Pi's timebase. A Pi timestamp is already there and comes back
        untouched — the correction only ever applies to the client's clock, and the
        one bug this guards is applying it to the side that did not need it, which
        moves the two logs APART by exactly the offset and looks like a real lag."""
        return t + self.at(t) if from_client else t

    def reliable_at(self, ct: float) -> bool:
        """A sample near ct with acceptable jitter/sample-count.

        ABSENT IS NOT ZERO, and here that rule decides whether a timestamp is allowed to
        look like a measurement. `jitter_ms` used to default to 0 when a clock_sync
        record did not carry it, and 0 ms of jitter is not a neutral placeholder — it is
        the steadiest link anyone has ever measured. So the sync with the least to say
        about itself vouched for more rows than any real one could, and every row it
        covered lost the ~unrel mark that says its time is a translation. The sample
        count already defaulted the honest way, to a value that fails the test below;
        both halves of the same question now answer it the same way.
        """
        if not self.samples:
            return False
        near = min(self.samples, key=lambda s: abs(s[0] - ct))
        if abs(near[0] - ct) > 30_000:  # >30 s from any sync → extrapolating
            return False
        meta = near[2]
        jitter = meta.get("jitter_ms")
        if not isinstance(jitter, (int, float)) or isinstance(jitter, bool):
            return False  # this sync never said how steady the link under it was
        return jitter <= 50 and meta.get("samples", 0) >= 3


# ---- merge (§6) ------------------------------------------------------------
def merge(nav: list[dict], client: list[dict]) -> list[dict]:
    off = Offset(client)
    merged = []
    for r in nav:
        merged.append({**r, "side": "pi", "at": r.get("t", 0)})
    for r in client:
        ct = r.get("t", 0)
        rec = {**r, "side": "client", "at": off.onto_pi(ct, from_client=True), "raw_t": ct}
        if not off.reliable_at(ct):
            rec["align_unreliable"] = True
        merged.append(rec)
    merged.sort(key=lambda x: x["at"])
    return merged


# ---- diverge (§6) ----------------------------------------------------------
def _by_event(events, name):
    return [e for e in events if e.get("e") == name]


def _cid_times(events, name):
    """{c_id: t} for the first occurrence of an event with a c_id."""
    out = {}
    for e in _by_event(events, name):
        cid = e.get("c_id")
        if cid and cid not in out:
            out[cid] = e.get("t")
    return out


def _pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, int(round((p / 100) * (len(vals) - 1)))))
    return round(vals[k], 1)


def _spread(vals) -> dict:
    """The four numbers every millisecond figure in this report is quoted as.

    `n` travels with them and is not decoration: a p95 taken from three samples is not
    a p95, and _pct returns None from no samples at all — which is the difference
    between "nothing was slow" and "nothing was measured", and the reader cannot tell
    them apart without the count.
    """
    return {"p50": _pct(vals, 50), "p95": _pct(vals, 95), "max": _pct(vals, 100), "n": len(vals)}


def _seqset_from_ranges(events, name):
    """Expand compact seq ranges (tlm_tx / tlm_rx) into a set of seq numbers + gaps."""
    seqs: set[int] = set()
    gaps: list[list[int]] = []
    for e in _by_event(events, name):
        d = e.get("d", {})
        a, b = d.get("seq_from"), d.get("seq_to")
        if a is not None and b is not None:
            seqs.update(range(int(a), int(b) + 1))
        for g in d.get("gaps", []) or []:
            gaps.append(list(g))
    return seqs, gaps


def diverge(nav: list[dict], client: list[dict]) -> dict:
    off = Offset(client)
    rep: dict = {}

    # --- lost commands (§6) ---
    sent = _cid_times(client, "cmd_send")
    recv = _cid_times(nav, "cmd_recv")
    ack_send = _cid_times(nav, "cmd_ack_send")
    ack_recv = _cid_times(client, "cmd_ack_recv")
    applied = _cid_times(nav, "cmd_apply")
    confirmed = _cid_times(client, "cmd_confirm")
    rep["commands"] = {
        "sent": len(sent),
        "lost_outbound": sorted(set(sent) - set(recv)),  # send, never received
        "lost_ack_inbound": sorted(set(ack_send) - set(ack_recv)),  # acked, ack never arrived
        "applied_not_confirmed": sorted(set(applied) - set(confirmed)),  # did it, no observed effect
    }

    # --- lost telemetry (§6) ---
    tx, _ = _seqset_from_ranges(nav, "tlm_tx")
    rx, rx_gaps = _seqset_from_ranges(client, "tlm_rx")
    missing = tx - rx
    worst = 0
    if missing:
        ms = sorted(missing)
        run = 1
        for i in range(1, len(ms)):
            run = run + 1 if ms[i] == ms[i - 1] + 1 else 1
            worst = max(worst, run)
        worst = max(worst, 1)
    rep["telemetry"] = {
        "sent": len(tx),
        "received_of_sent": len(tx & rx),
        "lost": len(missing),
        "loss_pct": round(100 * len(missing) / len(tx), 2) if tx else 0.0,
        "worst_contiguous_gap": worst,
        "client_reported_gaps": len(rx_gaps),
    }

    # --- latency per stage (§6), cross-side stages aligned via offset ---
    def stage(a_times, b_times, a_side, b_side):
        vals = []
        for cid, ta in a_times.items():
            tb = b_times.get(cid)
            if tb is None:
                continue
            began = off.onto_pi(ta, a_side == "client")
            ended = off.onto_pi(tb, b_side == "client")
            vals.append(ended - began)
        return vals

    # One name per stage, spelled out: apply→ack is the Pi answering itself and
    # apply→confirm is the whole round trip, and abbreviated they differ by a letter.
    intent = _cid_times(client, "cmd_intent")
    intent_to_apply = stage(intent, applied, "client", "pi")
    apply_to_ack = stage(applied, ack_send, "pi", "pi")
    apply_to_confirm = stage(applied, confirmed, "pi", "client")
    rep["latency_ms"] = {
        "intent_to_apply": _spread(intent_to_apply),
        "apply_to_ack": _spread(apply_to_ack),
        "apply_to_confirm": _spread(apply_to_confirm),
    }

    # --- staleness (§4) ---
    ages = [
        e["d"]["max_age_ms"]
        for e in _by_event(client, "tlm_rx")
        if isinstance(e.get("d"), dict) and "max_age_ms" in e["d"]
    ]
    THRESH = 500
    rep["staleness_ms"] = {
        **_spread(ages),
        "windows_over_%dms" % THRESH: sum(1 for a in ages if a > THRESH),
    }

    # --- video divergence (§4.2) — consumer stats; producer side not logged here ---
    wr = _by_event(client, "webrtc_stats")
    if wr:
        last = wr[-1].get("d", {})
        rep["video"] = {
            "frames_decoded": last.get("framesDecoded"),
            "frames_dropped": last.get("framesDropped"),
            "freeze_count": last.get("freezeCount"),
            "total_freeze_s": last.get("totalFreezesDuration"),
            "packets_lost": last.get("packetsLost"),
            "note": "consumer-side only; a healthy sender with these non-zero = tether, not camera",
        }
    else:
        rep["video"] = {"note": "no webrtc_stats logged"}

    # --- one-sided outages (§6): >3 s where one side is silent but the other isn't ---
    rep["one_sided_outages"] = _one_sided(nav, client, off, gap_s=3.0)

    # --- clock anomalies (§6) ---
    rep["clock"] = _clock_anomalies(off)
    return rep


def _one_sided(nav, client, off, gap_s):
    def times(evs, from_client):
        return sorted(off.onto_pi(e["t"], from_client) for e in evs if "t" in e)

    pi_t = times(nav, False)
    cl_t = times(client, True)
    if not pi_t or not cl_t:
        return {"note": "need both sides to detect one-sided outages"}
    out = []

    def find_silence(ts, other, label):
        for i in range(1, len(ts)):
            gap = ts[i] - ts[i - 1]
            if gap > gap_s * 1000:
                lo, hi = ts[i - 1], ts[i]
                if any(lo < o < hi for o in other):  # the other side WAS active in that window
                    out.append(
                        {"side_silent": label, "from": round(lo, 1), "to": round(hi, 1), "gap_ms": round(gap, 1)}
                    )

    find_silence(pi_t, cl_t, "pi")
    find_silence(cl_t, pi_t, "client")
    return out


def _clock_anomalies(off: Offset):
    s = off.samples
    if len(s) < 2:
        return {"note": "insufficient clock_sync samples", "samples": len(s)}
    offs = [o for _, o, _ in s]
    ts = [t for t, _, _ in s]
    jumps = []
    for i in range(1, len(s)):
        d = offs[i] - offs[i - 1]
        if abs(d) > 100:  # >100 ms step between adjacent syncs
            jumps.append({"at": round(ts[i], 1), "delta_ms": round(d, 1)})
    span = ts[-1] - ts[0]
    drift = round((offs[-1] - offs[0]) / (span / 1000), 3) if span > 0 else 0.0  # ms per second
    sync_gaps = [round(ts[i] - ts[i - 1], 1) for i in range(1, len(s)) if ts[i] - ts[i - 1] > 15_000]
    return {
        "samples": len(s),
        "offset_first_ms": round(offs[0], 1),
        "offset_last_ms": round(offs[-1], 1),
        "drift_ms_per_s": drift,
        "jumps": jumps,
        "sync_gaps_ms": sync_gaps,
    }


# ---- telemetry replay (§5) -------------------------------------------------
# Every field the control loop journals. Listed here so the report can say which
# ones a log does NOT contain: a session recorded by an older build is missing
# them entirely, and "0 snag events" out of a log that never knew what a snag was
# is a lie of exactly the kind this tool exists to catch.
TLM_FIELDS = (
    "armed",
    "depth",
    "heading",
    "left",
    "right",
    "pressure",
    "ballast_level",
    "ballast_target",
    "ballast_homed",
    "ballast_needs_rehome",
    "leak",
    "leak_state",
    "leak_probe_fault",
    "battery_v",
    "battery_band",
    "current_a",
    "speed_ms",
    "speed_src",
    "snagged",
    "gyro_only",
    "mag_cal",
    # THE INERTIAL READINGS, which main.py started journalling when they reached the
    # wire and this list did not follow. An unlisted-but-journalled field is the one
    # case the "missing" report cannot catch: it is present in the log and absent from
    # the question, so nobody is ever told it went unrecorded on an older recorder.
    "gyro_z_dps",
    "accel_fwd_ms2",
    "pitch_deg",
    "roll_deg",
    "magnet",
    "light_green",
    "light_white",
    "signal",
    # WHICH CHIP STOPPED, and WHETHER NAVIGATION WAS ANSWERING. Both were being
    # journalled and neither was listed, so a report could not distinguish "no
    # sensor ever faulted" from "this recorder had no idea a sensor could fault" —
    # and that is the precise confusion the missing-field list exists to prevent.
    # It matters more here than anywhere else on the list: a dive where the compass
    # died and a dive where the compass was fine look identical in every other
    # column, because the whole point of the null is that no number is left behind.
    "sensor_faults",
    "nav_loop",
    "nav_answering",
    "nav_used",
    "nav_reads_vehicle",
    "nav_faults",
)


def _tlm_rows(nav: list[dict]) -> list[tuple[float, float, dict]]:
    """Journalled telemetry as (t, dt_it_stood_for, fields), in time order.

    The recorder writes on state CHANGE and on a slow heartbeat, so records are
    deliberately irregular. Anything phrased as "% of the dive" must therefore weight
    each record by how long it stood, not count lines — otherwise one bad second that
    produced twenty change records outvotes ten calm minutes of heartbeats, and the
    report says the sub was snagged for most of the dive when it was snagged for 2 s.

    THE FINAL RECORD IS NOT WORTH 0 ms. Each row is weighted by the gap to the NEXT one,
    and the last row has no next — so the obvious code gives it zero, and every state the
    dive ENDED in then contributes 0% of every percentage in this report. A dive that
    ended flooded, snagged and unhomed reads back as clean. That is exactly backwards:
    the last record is the one the report is opened to find out about. So the tail stands
    for the MEDIAN inter-record gap of this same log — the median rather than the mean
    because a heartbeat stream punctuated by change bursts has outliers by construction,
    and a real gap from this log rather than a constant because the recorder's cadence is
    a per-session thing. It is a slightly-wrong duration in place of a catastrophically
    wrong one: the percentages can be a fraction out, but they can no longer hide the
    ending.

    A log holding a SINGLE record has no gap to copy, and none is invented here. The
    weights are then all zero, `total` is zero and every share comes back empty — which
    reads as cannot-tell, not as a quiet dive.
    """
    recs = [e for e in nav if e.get("e") in ("tlm", "tlm_state") and isinstance(e.get("d"), dict)]
    recs.sort(key=lambda e: e.get("t", 0.0))
    ts = [float(e.get("t", 0.0)) for e in recs]
    gaps = [max(0.0, ts[i + 1] - ts[i]) for i in range(len(ts) - 1)]
    tail = statistics.median(gaps) if gaps else 0.0
    return [(ts[i], gaps[i] if i < len(gaps) else tail, e["d"]) for i, e in enumerate(recs)]


def _key(v) -> str:
    """JSON dict keys must be strings, and None must stay distinguishable from the
    string 'None' — an absent speed source is not a source called None."""
    return "null" if v is None else str(v)


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0, "note": "never reported — treat as not fitted, not as zero"}
    return {
        "n": len(vals),
        "min": round(min(vals), 3),
        "max": round(max(vals), 3),
        "mean": round(statistics.fmean(vals), 3),
    }


def _transitions(rows, key: str) -> list[dict]:
    out, prev = [], "\x00sentinel"
    for t, _dt, d in rows:
        cur = d.get(key)
        if cur != prev:
            if prev != "\x00sentinel":
                out.append({"at": round(t, 1), "from": prev, "to": cur})
            prev = cur
    return out


def _episodes(rows, key: str) -> list[dict]:
    """Contiguous runs where d[key] is truthy — [{from, to, ms}]."""
    return _episodes_where(rows, lambda d: bool(d.get(key)))


def _episodes_where(rows, pred) -> list[dict]:
    """Contiguous runs where `pred(record)` holds — [{from, to, ms}].

    Duration matters more than count for these: a snag flag that appeared once for
    2 s and a snag flag that stayed up for the last four minutes of the dive are
    completely different stories about how the dive ended.

    Takes a predicate as well as the plain-key form above because the two signals
    added this round are not single truthy keys. A navigation outage is nav_used
    being FALSE (and the inverse of a key is not a key), and a chip outage is one
    name's presence inside a list. Both are episodes in exactly the same sense as a
    snag, and they are worth the same "…and it was still going when the log
    stopped" mark at the end.
    """
    out, start = [], None
    for t, dt, d in rows:
        on = bool(pred(d))
        if on and start is None:
            start = t
        elif not on and start is not None:
            out.append({"from": round(start, 1), "to": round(t, 1), "ms": round(t - start, 1)})
            start = None
    if start is not None and rows:
        end = rows[-1][0] + rows[-1][1]
        out.append(
            {"from": round(start, 1), "to": round(end, 1), "ms": round(end - start, 1), "open_at_end": True}
        )  # still asserted when the log stopped
    return out


def telemetry_report(nav: list[dict]) -> dict:
    """Replay the recorded vehicle state (§5): what the sub reported, and when it changed."""
    rows = _tlm_rows(nav)
    alarms = [{"at": round(e.get("t", 0.0), 1), **(e.get("d") or {})} for e in _by_event(nav, "alarm")]
    if not rows:
        return {
            "note": "no tlm/tlm_state records — this log cannot be replayed",
            "hint": "telemetry journalling is written by the control loop in api/main.py",
            "alarms": alarms,
        }
    total = sum(dt for _, dt, _ in rows)
    seen: set[str] = set()
    for _t, _dt, d in rows:
        seen.update(d.keys())

    def share(key: str) -> dict:
        """% of elapsed time spent at each value of `key` (time-weighted, see _tlm_rows)."""
        if total <= 0:
            return {}
        acc: dict[str, float] = {}
        for _t, dt, d in rows:
            k = _key(d.get(key))
            acc[k] = acc.get(k, 0.0) + dt
        return {k: round(100 * v / total, 1) for k, v in sorted(acc.items(), key=lambda kv: -kv[1])}

    def pct_where(pred) -> float | None:
        if total <= 0:
            return None
        return round(100 * sum(dt for _t, dt, d in rows if pred(d)) / total, 1)

    def values(key: str, pred=None) -> list[float]:
        return [
            float(d[key])
            for _t, _dt, d in rows
            if isinstance(d.get(key), (int, float)) and not isinstance(d.get(key), bool) and (pred is None or pred(d))
        ]

    homed = next((t for t, _dt, d in rows if d.get("ballast_homed")), None)
    faults = _transitions(rows, "leak_probe_fault")
    mags = [d.get("mag_cal") for _t, _dt, d in rows]
    # Every chip this log ever named as silent, so the per-chip episodes below can
    # be built without a hard-coded list of part numbers — a hull that grows a
    # fourth sensor must not need this file edited before its outage is reportable.
    chips = sorted({c for _t, _dt, d in rows for c in (d.get("sensor_faults") or [])})

    rep: dict = {
        "records": len(rows),
        "span_ms": round(rows[-1][0] - rows[0][0], 1),
        # What the last record was weighted as. It has no successor to measure against, so
        # it stands for the median inter-record gap (_tlm_rows) — said out loud because it
        # is why every percentage below is over span_ms + tail_ms, and why an episode still
        # open at the end can run past the last timestamp. Zero here means a single-record
        # log: nothing was invented, and the shares come back empty rather than clean.
        "tail_ms": round(rows[-1][1], 1),
        "mock": sorted({_key(d.get("mock")) for _t, _dt, d in rows}),
        "alarms": alarms,
        # LEAK — the stage, not the bit. Both are logged; if they ever disagree the
        # bug is upstream of the log and this is where it shows up.
        "leak": {
            "time_pct": share("leak_state"),
            "transitions": _transitions(rows, "leak_state"),
            "probe_faults": [f for f in faults if f["to"]],
        },
        # BALLAST — unknown is a first-class outcome here. Time spent with no idea
        # where the syringe was is a number worth reading after a dive.
        "ballast": {
            "unknown_pct": pct_where(lambda d: d.get("ballast_level") is None),
            "homed_at_ms": round(homed, 1) if homed is not None else None,
            "never_homed": homed is None,
            "needs_rehome_episodes": _episodes(rows, "ballast_needs_rehome"),
            "level": _stats(values("ballast_level")),
        },
        # SPEED — measured and estimated are reported SEPARATELY on purpose. Averaging
        # a paddlewheel reading together with a LUT guess produces a number with no
        # meaning, and an estimate never dresses as a measurement (§5).
        "speed": {
            "source_pct": share("speed_src"),
            "measured": _stats(values("speed_ms", lambda d: d.get("speed_src") in ("paddle", "kf-paddle"))),
            "estimated": _stats(values("speed_ms", lambda d: d.get("speed_src") in ("lut", "kf-lut"))),
            "no_source_pct": pct_where(lambda d: d.get("speed_src") is None),
        },
        "snag": {
            "events": _episodes(rows, "snagged"),
            "time_pct": pct_where(lambda d: bool(d.get("snagged"))),
        },
        # HEADING TRUST — gyro_only is deliberate coasting, mag_cal < 2 is a suspect
        # compass. A dive that spent most of its time in either is a dive whose track
        # should be believed less, and that judgement needs the percentages.
        "heading_trust": {
            "gyro_only_pct": pct_where(lambda d: bool(d.get("gyro_only"))),
            "gyro_only_episodes": _episodes(rows, "gyro_only"),
            "mag_cal_pct": share("mag_cal"),
            "mag_suspect_pct": pct_where(lambda d: isinstance(d.get("mag_cal"), int) and d["mag_cal"] < 2),
            "no_imu_pct": pct_where(lambda d: d.get("mag_cal") is None),
        },
        "battery": {
            "band_pct": share("battery_band"),
            "volts": _stats(values("battery_v")),
            "first_warn_ms": next((round(t, 1) for t, _dt, d in rows if d.get("battery_band") == "warn"), None),
            "first_critical_ms": next((round(t, 1) for t, _dt, d in rows if d.get("battery_band") == "critical"), None),
            # Time with NO pack voltage at all — band "unknown". Kept apart from
            # the volts stats above, which simply skip the nulls: a dive whose
            # INA219 died at minute two has a perfectly healthy mean voltage and a
            # battery nobody was watching, and only this number says so.
            "unknown_pct": pct_where(lambda d: d.get("battery_v") is None),
            "current_a": _stats(values("current_a")),
        },
        # WHICH CHIP WENT SILENT, AND FOR HOW LONG. The individual gauges only ever
        # go null, and a null is the same shape whatever caused it, so without this
        # a replay of a dive that lost its depth sensor at 4.33 m reads as a dive
        # that stopped reporting depth. Per chip and as episodes, because the
        # question after a dive is "when did the MS5837 drop off the bus, and was
        # it back before the sub was" — not "did anything ever fault".
        "sensors": {
            "any_fault_pct": pct_where(lambda d: bool(d.get("sensor_faults"))),
            "chips_faulted": chips,
            "outages": {c: _episodes_where(rows, lambda d, c=c: c in (d.get("sensor_faults") or [])) for c in chips},
        },
        # NAVIGATION'S OWN STATE, replayed. nav_answering is what navigation
        # claimed; nav_used is what the frame actually took from it, and they part
        # company exactly when navigation is answering about something that is not
        # this hull. An outage here explains, in one line, every nav field in the
        # log going null at the same instant — which otherwise looks like the dive
        # simply ending.
        "navigation": {
            "loop_pct": share("nav_loop"),
            "used_pct": pct_where(lambda d: bool(d.get("nav_used"))),
            "outages": _episodes_where(rows, lambda d: "nav_used" in d and not d["nav_used"]),
            "loop_transitions": _transitions(rows, "nav_loop"),
            "not_this_hull_pct": pct_where(lambda d: d.get("nav_reads_vehicle") is False),
            "tick_faults": max(
                (d["nav_faults"] for _t, _dt, d in rows if isinstance(d.get("nav_faults"), int)), default=None
            ),
        },
        "armed_pct": pct_where(lambda d: bool(d.get("armed"))),
    }
    # A PERCENTAGE OF A COLUMN THAT WAS NEVER WRITTEN IS NOT A ZERO. Both blocks
    # above are built with d.get(), so a log recorded before these keys existed —
    # or by a process with no navigation in it — produces "0% faulted, 0% used",
    # which reads as a clean dive with dead navigation and is neither. The shares
    # are replaced by the reason there are none, in the same spirit as
    # fields_never_logged below.
    if "sensor_faults" not in seen:
        rep["sensors"] = {
            "note": "this log never carried sensor_faults — treat it as "
            "no information about the chips, not as none faulted"
        }
    if "nav_used" not in seen:
        rep["navigation"] = {
            "note": "this log never carried nav_* records — navigation was "
            "not running in that process, or the recorder predates "
            "it. Not the same as navigation never answering"
        }
    missing = [f for f in TLM_FIELDS if f not in seen]
    if missing:
        # Say it out loud rather than reporting confident zeros for signals the log
        # never carried — an older recorder is a plausible explanation for "no snags"
        # and the reader must not have to guess which one they are looking at.
        rep["fields_never_logged"] = missing
    if all(m is None for m in mags):
        rep["heading_trust"]["note"] = "mag_cal was null throughout — no IMU answered, distinct from cal 0"
    return rep


# ---- timeline (§6) ---------------------------------------------------------
def _tlm_brief(d: dict) -> str:
    """One scannable line for a journalled telemetry record.

    A tlm record carries ~25 fields; dumped whole it turns the incident timeline
    into a wall of JSON and the operator stops reading it, which defeats the point
    of having a timeline at all. These are the fields you actually scan for while
    working out what killed a dive; the full record is in the raw log next to it.
    """

    # "?" for every reading that was not taken, so a scan down the column shows
    # cannot-tell as one recognisable shape rather than as the word None appearing
    # in six different spellings. It is NOT interchangeable with a number: `depth=?`
    # is the sensor saying nothing, and it is exactly what the eye must catch.
    def q(key):
        v = d.get(key)
        return "?" if v is None else v

    parts = [
        "ARMED" if d.get("armed") else "safe",
        f"depth={q('depth')}",
        f"hdg={q('heading')}",
        # The leak stage and the battery band go through q() like every other reading.
        # They used to be interpolated raw, so an unanswered probe printed `leak=None`
        # and an unwatched pack `batt=?(None)` — the word None in the middle of a column
        # the eye is scanning for `?`, which is the one spelling of cannot-tell this
        # helper exists to enforce. It matters most on these two: `leak=NORMAL` is a
        # positive claim that the hull is dry (§24.1), and the absence of that claim must
        # not be readable as any kind of value.
        f"leak={q('leak_state')}",
        f"batt={q('battery_v')}({q('battery_band')})",
        f"ball={q('ballast_level')}",
        f"spd={q('speed_ms')}/{q('speed_src')}",
    ]
    for flag, tag in (("snagged", "SNAGGED"), ("gyro_only", "gyro-only"), ("ballast_needs_rehome", "REHOME")):
        if d.get(flag):
            parts.append(tag)
    if d.get("leak_probe_fault"):
        parts.append("probe-fault=" + str(d["leak_probe_fault"]))
    # The named chips ride on the same line as the blanks they caused, so a reader
    # scanning an incident does not have to correlate "depth=?" with a separate
    # record to find out that the MS5837 had stopped answering.
    if d.get("sensor_faults"):
        parts.append("DEAD=" + ",".join(str(c) for c in d["sensor_faults"]))
    if "nav_used" in d and not d["nav_used"]:
        parts.append("nav-quiet")
    if d.get("n_changes"):
        parts.append(f"(+{d['n_changes']} coalesced)")
    return " ".join(parts)


def timeline(nav, client, around: float, window: float) -> list[str]:
    merged = merge(nav, client)
    lo, hi = around - window * 1000, around + window * 1000
    lines = []
    for r in merged:
        if not (lo <= r["at"] <= hi):
            continue
        side = r["side"]
        tag = "PI  " if side == "pi" else "  CL"
        # A client row's time is the client's clock TRANSLATED onto the Pi's, and merge()
        # marks the translations it could not stand behind — extrapolated more than 30 s
        # from any clock_sync, or interpolated off a jittery one. The mark is printed
        # beside the time it qualifies, because the whole use of this view is reading an
        # ordering out of that column, and an estimate that looks exactly like a
        # measurement is how "the client saw it first" gets concluded from arithmetic.
        flag = "~unrel" if r.get("align_unreliable") else ""
        d = r.get("d", "")
        cid = (" [" + r["c_id"][:8] + "]") if r.get("c_id") else ""
        col = f"{r['at']:.1f}"
        if r.get("e") in ("tlm", "tlm_state") and isinstance(d, dict):
            body = _tlm_brief(d)
        else:
            body = json.dumps(d, separators=(",", ":")) if d else ""
        row = f"{col:>14} {flag:<6} {tag}  {r.get('e', '?'):<16}{cid} {body}"
        lines.append(row)
    return lines


# ---- cli -------------------------------------------------------------------
def _fmt(obj) -> str:
    return json.dumps(obj, indent=2)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="rovlog", description="Blackbox two-sided log analysis (§6)")
    # --log-dir lives on each subcommand (after the verb): `rovlog diverge S1 --log-dir DIR`
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--log-dir", default=None, help="override the log directory")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("merge", parents=[common], help="time-align both logs into one stream")
    m.add_argument("nav")
    m.add_argument("client")

    dv = sub.add_parser("diverge", parents=[common], help="report where the two logs disagree")
    dv.add_argument("session")

    tm = sub.add_parser(
        "telemetry",
        parents=[common],
        help="replay recorded vehicle state: leak stages, ballast truth, "
        "speed source, snags, heading trust, battery bands",
    )
    tm.add_argument("session")

    tl = sub.add_parser("timeline", parents=[common], help="side-by-side text timeline around an incident")
    tl.add_argument("session")
    tl.add_argument("--around", type=float, required=True)
    tl.add_argument("--window", type=float, default=30.0)

    bd = sub.add_parser("bundle", parents=[common], help="incident zip: both logs + reports + config + track")
    bd.add_argument("session")
    bd.add_argument("-o", "--out", default=None)

    args = p.parse_args(argv)

    if args.cmd == "merge":
        for r in merge(load_jsonl(Path(args.nav)), load_jsonl(Path(args.client))):
            print(json.dumps(r, separators=(",", ":")))
        return 0

    if args.cmd == "diverge":
        nav_p, cli_p = session_files(args.session, args.log_dir)
        rep = diverge(load_jsonl(nav_p), load_jsonl(cli_p))
        print(_fmt(rep))
        return 0

    if args.cmd == "telemetry":
        # One-sided on purpose: this is what the VEHICLE reported, so it needs the Pi
        # log only. It answers even when no client ever connected — which is exactly
        # the session the recorder was changed to keep.
        nav_p, _cli_p = session_files(args.session, args.log_dir)
        print(_fmt(telemetry_report(load_jsonl(nav_p))))
        return 0

    if args.cmd == "timeline":
        nav_p, cli_p = session_files(args.session, args.log_dir)
        for line in timeline(load_jsonl(nav_p), load_jsonl(cli_p), args.around, args.window):
            print(line)
        return 0

    if args.cmd == "bundle":
        nav_p, cli_p = session_files(args.session, args.log_dir)
        out = Path(args.out) if args.out else _resolve_dir(args.log_dir) / f"incident_{args.session}.zip"
        nav, cli = load_jsonl(nav_p), load_jsonl(cli_p)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            if nav_p.exists():
                z.write(nav_p, nav_p.name)
            if cli_p.exists():
                z.write(cli_p, cli_p.name)
            z.writestr("merge.jsonl", "\n".join(json.dumps(r, separators=(",", ":")) for r in merge(nav, cli)))
            z.writestr("diverge.json", _fmt(diverge(nav, cli)))
            # The vehicle's own story goes in the incident zip too: "the link was
            # fine and the sub was snagged with a flooding hull" is one answer, and
            # diverge.json alone can only ever tell you about the link.
            z.writestr("telemetry.json", _fmt(telemetry_report(nav)))
            # config + dive track, best-effort
            for extra in ("config.py",):
                cp = Path(__file__).resolve().parent.parent / extra
                if cp.exists():
                    z.write(cp, "config/" + cp.name)
        print(str(out))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
