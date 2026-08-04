"""rovlog — blackbox analysis CLI (spec §6).

Two independent logs of the same events (Pi + client) let you locate a fault that
neither pins down alone. This tool aligns them on a common timebase (using the
logged clock offsets, never by rewriting timestamps) and reports where they diverge.

    rovlog merge    nav.jsonl client.jsonl     one time-aligned stream
    rovlog diverge  <session>                  the payoff: what the two logs disagree on
    rovlog timeline <session> --around T --window S   side-by-side text around an incident
    rovlog bundle   <session>                  incident zip

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

    def reliable_at(self, ct: float) -> bool:
        """A sample near ct with acceptable jitter/sample-count."""
        if not self.samples:
            return False
        near = min(self.samples, key=lambda s: abs(s[0] - ct))
        if abs(near[0] - ct) > 30_000:          # >30 s from any sync → extrapolating
            return False
        meta = near[2]
        return meta.get("jitter_ms", 0) <= 50 and meta.get("samples", 0) >= 3


# ---- merge (§6) ------------------------------------------------------------
def merge(nav: list[dict], client: list[dict]) -> list[dict]:
    off = Offset(client)
    merged = []
    for r in nav:
        merged.append({**r, "side": "pi", "at": r.get("t", 0)})
    for r in client:
        ct = r.get("t", 0)
        rec = {**r, "side": "client", "at": ct + off.at(ct), "raw_t": ct}
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
        "lost_outbound": sorted(set(sent) - set(recv)),         # send, never received
        "lost_ack_inbound": sorted(set(ack_send) - set(ack_recv)),  # acked, ack never arrived
        "applied_not_confirmed": sorted(set(applied) - set(confirmed)),  # did it, no observed effect
    }

    # --- lost telemetry (§6) ---
    tx, _ = _seqset_from_ranges(nav, "tlm_tx")
    rx, rx_gaps = _seqset_from_ranges(client, "tlm_rx")
    missing = tx - rx
    worst = 0
    if missing:
        ms = sorted(missing); run = 1
        for i in range(1, len(ms)):
            run = run + 1 if ms[i] == ms[i - 1] + 1 else 1
            worst = max(worst, run)
        worst = max(worst, 1)
    rep["telemetry"] = {
        "sent": len(tx), "received_of_sent": len(tx & rx),
        "lost": len(missing), "loss_pct": round(100 * len(missing) / len(tx), 2) if tx else 0.0,
        "worst_contiguous_gap": worst, "client_reported_gaps": len(rx_gaps),
    }

    # --- latency per stage (§6), cross-side stages aligned via offset ---
    def stage(a_times, b_times, a_side, b_side):
        vals = []
        for cid, ta in a_times.items():
            tb = b_times.get(cid)
            if tb is None:
                continue
            aa = ta + (off.at(ta) if a_side == "client" else 0)
            bb = tb + (off.at(tb) if b_side == "client" else 0)
            vals.append(bb - aa)
        return vals
    intent = _cid_times(client, "cmd_intent")
    lat_ia = stage(intent, applied, "client", "pi")           # intent → apply
    lat_aa = stage(applied, ack_send, "pi", "pi")             # apply  → ack
    lat_ac = stage(applied, confirmed, "pi", "client")        # apply  → confirm
    rep["latency_ms"] = {
        "intent_to_apply": {"p50": _pct(lat_ia, 50), "p95": _pct(lat_ia, 95), "max": _pct(lat_ia, 100), "n": len(lat_ia)},
        "apply_to_ack":    {"p50": _pct(lat_aa, 50), "p95": _pct(lat_aa, 95), "max": _pct(lat_aa, 100), "n": len(lat_aa)},
        "apply_to_confirm":{"p50": _pct(lat_ac, 50), "p95": _pct(lat_ac, 95), "max": _pct(lat_ac, 100), "n": len(lat_ac)},
    }

    # --- staleness (§4) ---
    ages = [e["d"]["max_age_ms"] for e in _by_event(client, "tlm_rx")
            if isinstance(e.get("d"), dict) and "max_age_ms" in e["d"]]
    THRESH = 500
    rep["staleness_ms"] = {
        "p50": _pct(ages, 50), "p95": _pct(ages, 95), "max": _pct(ages, 100),
        "windows_over_%dms" % THRESH: sum(1 for a in ages if a > THRESH), "n": len(ages),
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
    def times(evs, aligned):
        return sorted((e["t"] + (off.at(e["t"]) if aligned else 0)) for e in evs if "t" in e)
    pi_t = times(nav, False)
    cl_t = times(client, True)
    if not pi_t or not cl_t:
        return {"note": "need both sides to detect one-sided outages"}
    out = []
    span = (min(pi_t[0], cl_t[0]), max(pi_t[-1], cl_t[-1]))

    def find_silence(ts, other, label):
        for i in range(1, len(ts)):
            gap = ts[i] - ts[i - 1]
            if gap > gap_s * 1000:
                lo, hi = ts[i - 1], ts[i]
                if any(lo < o < hi for o in other):     # the other side WAS active in that window
                    out.append({"side_silent": label, "from": round(lo, 1), "to": round(hi, 1),
                                "gap_ms": round(gap, 1)})
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
        if abs(d) > 100:                                 # >100 ms step between adjacent syncs
            jumps.append({"at": round(ts[i], 1), "delta_ms": round(d, 1)})
    span = ts[-1] - ts[0]
    drift = round((offs[-1] - offs[0]) / (span / 1000), 3) if span > 0 else 0.0  # ms per second
    sync_gaps = [round(ts[i] - ts[i - 1], 1) for i in range(1, len(s)) if ts[i] - ts[i - 1] > 15_000]
    return {"samples": len(s), "offset_first_ms": round(offs[0], 1), "offset_last_ms": round(offs[-1], 1),
            "drift_ms_per_s": drift, "jumps": jumps, "sync_gaps_ms": sync_gaps}


# ---- timeline (§6) ---------------------------------------------------------
def timeline(nav, client, around: float, window: float) -> list[str]:
    merged = merge(nav, client)
    lo, hi = around - window * 1000, around + window * 1000
    lines = []
    for r in merged:
        if not (lo <= r["at"] <= hi):
            continue
        side = r["side"]
        tag = "PI  " if side == "pi" else "  CL"
        flag = " ~unrel" if r.get("align_unreliable") else ""
        d = r.get("d", "")
        cid = (" [" + r["c_id"][:8] + "]") if r.get("c_id") else ""
        col = f"{r['at']:.1f}"
        row = f"{col:>14}  {tag}  {r.get('e','?'):<16}{cid} {json.dumps(d, separators=(',',':')) if d else ''}"
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
    m.add_argument("nav"); m.add_argument("client")

    dv = sub.add_parser("diverge", parents=[common], help="report where the two logs disagree")
    dv.add_argument("session")

    tl = sub.add_parser("timeline", parents=[common], help="side-by-side text timeline around an incident")
    tl.add_argument("session"); tl.add_argument("--around", type=float, required=True)
    tl.add_argument("--window", type=float, default=30.0)

    bd = sub.add_parser("bundle", parents=[common], help="incident zip: both logs + reports + config + track")
    bd.add_argument("session"); bd.add_argument("-o", "--out", default=None)

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
