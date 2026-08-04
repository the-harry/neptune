"""Blackbox flight recorder — Pi side (spec §1).

Two JSONL files per session under the log dir, kept SEPARATE (client events arrive
late, batched, out of order; interleaving would corrupt the Pi log's ordering — we
merge at analysis time, never write time):

    navigation_<session>.jsonl   ← this process writes, one event per line
    client_<session>.jsonl       ← uploaded verbatim from the topside client (§5)
    current.jsonl -> navigation_<session>.jsonl   (convenience symlink)

Session identity (§1): session_id is the UTC filename stamp; pi_boot_id is unique
per process so a client can detect a Pi reboot and start a fresh client file.

Timestamps are the Pi's own MONOTONIC clock in milliseconds (never wall-clock —
monotonic can't jump backwards). The client keeps its own monotonic time; the two
are reconciled in analysis using the logged clock_sync offsets, never rewritten.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger("neptune.blackbox")


def _resolve_log_dir() -> Path:
    """Prefer $ROV_LOG_DIR, else /var/log/rov, else a writable dev fallback."""
    for cand in (os.environ.get("ROV_LOG_DIR"), "/var/log/rov"):
        if not cand:
            continue
        p = Path(cand)
        try:
            p.mkdir(parents=True, exist_ok=True)
            testf = p / ".wtest"
            testf.write_text("x"); testf.unlink()
            return p
        except Exception:  # noqa: BLE001 — not writable (not root, read-only fs)
            continue
    fallback = Path(__file__).resolve().parent.parent.parent / "data" / "log"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


class BlackBox:
    """Append-only event recorder. Thread/async-safe (a lock guards each write);
    every write flushes so an abrupt power loss keeps all-but-the-last line."""

    def __init__(self, log_dir: Path | None = None) -> None:
        self.log_dir = Path(log_dir) if log_dir else _resolve_log_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        self.pi_boot_id = uuid.uuid4().hex
        self._t0_wall = time.time()
        self.nav_path = self.log_dir / f"navigation_{self.session_id}.jsonl"
        self._lock = threading.Lock()
        self._f = self.nav_path.open("a", encoding="utf-8")
        self._link_current()
        self.event("session_start", {
            "session_id": self.session_id, "pi_boot_id": self.pi_boot_id,
            "wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        log.info("blackbox recording → %s", self.nav_path)

    def _link_current(self) -> None:
        cur = self.log_dir / "current.jsonl"
        try:
            if cur.exists() or cur.is_symlink():
                cur.unlink()
            cur.symlink_to(self.nav_path.name)
        except Exception:  # noqa: BLE001 — symlinks may be unsupported; not fatal
            pass

    def now_ms(self) -> float:
        """The Pi's monotonic clock in ms — the timebase for every logged event."""
        return time.monotonic() * 1000.0

    def session_info(self) -> dict:
        return {"session_id": self.session_id, "pi_boot_id": self.pi_boot_id,
                "pi_t_mono": round(self.now_ms(), 3)}

    def event(self, e: str, d: dict | None = None, c_id: str | None = None,
              t: float | None = None) -> None:
        rec: dict = {"t": round(t if t is not None else self.now_ms(), 3), "e": e}
        if c_id:
            rec["c_id"] = c_id
        if d is not None:
            rec["d"] = d
        line = json.dumps(rec, separators=(",", ":"))
        with self._lock:
            self._f.write(line + "\n")
            self._f.flush()

    def client_append(self, session_id: str, records: list[dict]) -> int:
        """Append client-uploaded records VERBATIM to client_<session>.jsonl (§5).
        Keyed by the client's session_id so a reboot lands in a fresh file."""
        safe = "".join(c for c in (session_id or "") if c.isalnum() or c in "TZ-_") or self.session_id
        path = self.log_dir / f"client_{safe}.jsonl"
        n = 0
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r, separators=(",", ":")) + "\n")
                    n += 1
                f.flush()
        return n

    def close(self) -> None:
        with self._lock:
            try:
                self._f.flush(); self._f.close()
            except Exception:  # noqa: BLE001
                pass
