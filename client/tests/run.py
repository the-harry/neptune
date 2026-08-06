#!/usr/bin/env python3
"""NEPTUNE client test runner — browser checks against the REAL dashboard.

No framework, no dependencies, no build step: the same rule the client itself
follows. Python standard library plus a Chrome that is already installed.

WHAT IT DOES
    Serves client/ over loopback, injects one suite from suites/ into index.html
    as an extra <script>, opens it in headless Chrome, and waits for the suite to
    POST its results back. The page under test is the shipping dashboard byte for
    byte plus that one tag — nothing is stubbed, mocked or rebuilt.

USAGE
    python client/tests/run.py                 # every suite
    python client/tests/run.py tether          # one suite (substring match)
    python client/tests/run.py --headed        # watch it happen
    python client/tests/run.py --list

    Exit status is 0 only if every check in every suite passed, so this is
    usable as a pre-push gate.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIENT = HERE.parent
SUITES = HERE / "suites"
SHOTS = HERE / "screenshots"      # what this run saw (gitignored)
BASELINE = HERE / "baseline"      # what it is supposed to look like (committed)

# Fraction of pixels that may differ before a layout is called a regression.
#
# MEASURED, not guessed. With the live surfaces hidden the layout portrait is almost
# deterministic; the residue is a few digits of live telemetry (heading, tether) and
# antialiasing. Measure the floor with --shot-noise and set this just above it. Set it
# "safely high" instead and the check stops working: at 2% a 28 px button growing to
# 44 px went completely unnoticed, because it is only 0.13% of the screen.
SHOT_TOLERANCE = 0.001   # measured floor is 0.000-0.016%; this is ~6x it
VERBOSE_SHOTS = False

# The one suite whose picture is ABOUT the map. Satellite tiles arrive from the network
# and the vehicle is moving underneath them, so its portrait is RECORDED and never
# compared - a check that cannot be stable should not pretend to be. Every other suite
# is photographed with the map hidden, which is what makes them comparable at all.
NO_COMPARE = {"map-zoom-and-rov"}

# Chrome is the only browser the ROG Ally actually runs this on, so it is the one
# the tests use. Edge works too — same engine.
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


# Prepended to every suite. A suite that throws must REPORT, not sit there until the
# timeout expires — twenty silent seconds tells you nothing and costs a minute across a
# full run. Deliberately scoped to errors raised BY THE SUITE (checked on filename):
# errors raised by the dashboard are the suite's own business, and several suites assert
# on exactly that, so swallowing them here would turn a precise failure into a vague one.
PREAMBLE = b"""/* injected by client/tests/run.py */
(function(){
  var sent=false;
  function bail(what){
    if(sent) return; sent=true;
    try{ fetch('/__result',{method:'POST',body:'SUITE CRASHED: '+what}); }catch(e){}
  }
  window.addEventListener('error', function(e){
    if(e && e.filename && e.filename.indexOf('__suite.js')<0) return;   // page error: not ours
    bail((e && e.error && e.error.stack) || (e && e.message) || 'error');
  });
  window.addEventListener('unhandledrejection', function(e){
    // Rejections carry no filename, so the ONLY safe attribution is a stack that
    // names the suite. Anything else belongs to the dashboard and is not ours to
    // abort on - claiming those turns "the product has a loose promise" into
    // "the test crashed", which points at the wrong file entirely.
    var r = e && e.reason, st = r && r.stack;
    if(!st || st.indexOf('__suite.js') < 0) return;
    bail('unhandled rejection: ' + st);
  });
})();
"""


def find_chrome(explicit: str | None) -> str:
    if explicit:
        if not Path(explicit).exists():
            sys.exit(f"--chrome {explicit} does not exist")
        return explicit
    env = os.environ.get("NEPTUNE_CHROME")
    if env and Path(env).exists():
        return env
    for p in CHROME_CANDIDATES:
        if p and Path(p).exists():
            return p
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    sys.exit("No Chrome/Edge found. Set NEPTUNE_CHROME=<path to chrome.exe> or pass --chrome.")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def make_handler(suite_path: Path, result_box: dict, done: threading.Event):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(CLIENT), **kw)

        def _send(self, body: bytes, ctype: str):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            # Match on the PATH, not the whole request line: a suite may ask for
            # ?sim=1, and an exact-string check silently stops injecting the suite
            # at all — which shows up as a timeout with no other clue.
            path = self.path.split("?", 1)[0]
            if path.startswith("/__suite.js"):
                self._send(PREAMBLE + suite_path.read_bytes(), "application/javascript")
                return
            if path in ("/", "/index.html"):
                # The ONLY modification to the page under test: one extra script tag.
                html = (CLIENT / "index.html").read_text(encoding="utf-8")
                html = html.replace("</body>", '<script src="/__suite.js"></script></body>')
                self._send(html.encode("utf-8"), "text/html; charset=utf-8")
                return
            return super().do_GET()

        def do_POST(self):
            if self.path.startswith("/__result"):
                n = int(self.headers.get("Content-Length", 0))
                result_box["raw"] = self.rfile.read(n).decode("utf-8", "replace")
                self.send_response(204)
                self.end_headers()
                done.set()
                return
            # The dashboard POSTs to the Pi for all sorts of things it cannot reach
            # here (origin mirror, blackbox upload). Answer politely; a 404 storm in
            # the console makes real failures harder to spot.
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    return H


def suite_url_suffix(suite: Path) -> str:
    """A suite may ask for a specific URL, e.g. `// @url ?sim=1` on any early line.

    Some behaviour IS the URL — demo mode is a query parameter — and there is no way
    to test that from inside a page already loaded without one.
    """
    head = suite.read_text(encoding="utf-8", errors="replace")[:600]
    for line in head.splitlines():
        if "@url" in line:
            return line.split("@url", 1)[1].strip()
    return ""


def run_suite(suite: Path, chrome: str, timeout: float, headed: bool, keep: bool,
              shots: bool = True):
    port = free_port()
    cdp_port = free_port()
    result_box: dict = {}
    done = threading.Event()
    srv = Server(("127.0.0.1", port), make_handler(suite, result_box, done))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    profile = tempfile.mkdtemp(prefix="neptune-test-")
    args = [chrome, f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--disable-background-networking",
            f"--remote-debugging-port={cdp_port}",
            "--window-size=1280,800",
            f"http://127.0.0.1:{port}/index.html{suite_url_suffix(suite)}"]
    if not headed:
        args[1:1] = ["--headless=new", "--disable-gpu"]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    shot_err = None
    try:
        finished = done.wait(timeout=timeout)
        # Photograph the page in the state the suite left it — before anything is torn
        # down. A numeric check says a rule held; the picture is the only thing that
        # catches "technically correct and visibly wrong".
        if shots:
            try:
                from cdp import capture_png
                SHOTS.mkdir(parents=True, exist_ok=True)
                # TWO shots, on purpose:
                #   <suite>.png         the real thing, kept to be LOOKED at
                #   <suite>.layout.png  the same page with the live map and video
                #                       hidden — the only version stable enough to
                #                       compare, and where layout regressions show
                (SHOTS / f"{suite.stem}.png").write_bytes(capture_png(cdp_port))
                (SHOTS / f"{suite.stem}.layout.png").write_bytes(
                    capture_png(cdp_port, hide_live=True))
            except Exception as exc:   # noqa: BLE001 — never fail a suite over its portrait
                shot_err = str(exc)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        srv.shutdown()
        if not keep:
            shutil.rmtree(profile, ignore_errors=True)

    if not finished:
        return None, f"timed out after {timeout:.0f}s - the suite never reported back", shot_err
    raw = result_box.get("raw", "")
    try:
        return json.loads(raw), None, shot_err
    except json.JSONDecodeError:
        # A suite that throws posts its stack instead of JSON. That IS the result.
        return None, raw.strip()[:800], shot_err


def check_shot(name: str, bless: bool):
    """Compare this run's screenshot with the committed baseline.

    Returns (note or None, drifted). A missing baseline is NOT a failure — the
    first run of a new suite has nothing to compare against, and inventing a
    verdict there would just teach everyone to ignore the output.
    """
    cur = SHOTS / f"{name}.layout.png"
    base = BASELINE / f"{name}.layout.png"
    if not cur.exists():
        return None, False
    if name in NO_COMPARE and not bless:
        return "map suite - portrait recorded, not compared (imagery moves)", False
    if bless:
        BASELINE.mkdir(parents=True, exist_ok=True)
        base.write_bytes(cur.read_bytes())
        return f"baseline updated ({cur.stat().st_size // 1024} KB)", False
    if not base.exists():
        return f"no baseline yet - run with --bless to accept {cur.name}", False
    try:
        from png import diff
        frac, note = diff(base.read_bytes(), cur.read_bytes())
    except Exception as exc:   # noqa: BLE001
        return f"screenshot compare failed: {exc}", False
    if frac is None:
        return f"VISUAL: cannot compare - {note}", True
    if frac > SHOT_TOLERANCE:
        return (f"VISUAL DRIFT {frac*100:.2f}% of pixels ({note}, tolerance "
                f"{SHOT_TOLERANCE*100:.2f}%) - see {cur}, --bless if intended"), True
    return (f"visual ok ({frac*100:.3f}% differ)" if VERBOSE_SHOTS else None), False


def main():
    ap = argparse.ArgumentParser(description="Run the Neptune client browser tests.")
    ap.add_argument("suites", nargs="*", help="substring(s) of suite names; default: all")
    ap.add_argument("--chrome", help="path to chrome.exe (else NEPTUNE_CHROME, else auto)")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds per suite")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway Chrome profiles")
    ap.add_argument("--list", action="store_true", help="list the suites and exit")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every check, not just failures")
    ap.add_argument("--no-shots", action="store_true", help="skip the screenshots")
    ap.add_argument("--bless", action="store_true",
                    help="accept the current screenshots as the new baseline")
    ap.add_argument("--shot-noise", action="store_true",
                    help="print the drift percentage even when it passes (to set the tolerance)")
    ap.add_argument("--strict-visual", action="store_true",
                    help="let visual drift fail the run (off by default: see check_shot)")
    args = ap.parse_args()

    all_suites = sorted(SUITES.glob("*.js"))
    if args.list:
        for s in all_suites:
            print(s.stem)
        return 0
    if args.suites:
        wanted = [s for s in all_suites if any(w.lower() in s.stem.lower() for w in args.suites)]
        if not wanted:
            sys.exit(f"no suite matches {args.suites}; try --list")
    else:
        wanted = all_suites
    if not wanted:
        sys.exit(f"no suites found in {SUITES}")

    global VERBOSE_SHOTS
    VERBOSE_SHOTS = args.shot_noise

    chrome = find_chrome(args.chrome)
    print(f"chrome : {chrome}")
    print(f"client : {CLIENT}")
    print(f"suites : {len(wanted)}\n")

    total = failed = 0
    broken: list[str] = []
    t0 = time.time()

    shot_notes: list[str] = []
    for suite in wanted:
        checks, err, shot_err = run_suite(suite, chrome, args.timeout, args.headed,
                                          args.keep, shots=not args.no_shots)
        if checks is None:
            print(f"  {suite.stem:<24} ERROR")
            for line in (err or "unknown").splitlines()[:6]:
                print(f"      {line}")
            broken.append(suite.stem)
            failed += 1
            continue
        bad = [c for c in checks if not c.get("pass")]
        total += len(checks)
        failed += len(bad)
        mark = "ok " if not bad else "FAIL"
        print(f"  {suite.stem:<24} {mark} {len(checks)-len(bad):>3}/{len(checks)}")
        for c in checks:
            if args.verbose and c.get("pass"):
                print(f"      pass  {c['name']} -- {c.get('detail','')}")
        for c in bad:
            print(f"      FAIL  {c['name']}")
            print(f"            {c.get('detail','')}")
        if not args.no_shots:
            note, drifted = check_shot(suite.stem, args.bless)
            if note:
                print(f"      {note}")
                shot_notes.append(f"{suite.stem}: {note}")
            # Drift REPORTS by default and only fails with --strict-visual. The
            # dashboard is a live instrument photographed mid-flight: the heading, the
            # tether reading and the satellite imagery (which needs internet) all move
            # between runs. A gate that cries wolf on those gets muted inside a week,
            # and then it is protecting nothing at all. The picture is here to be
            # LOOKED at; the numeric checks are the gate.
            if drifted and args.strict_visual:
                failed += 1
        if shot_err:
            print(f"      (no screenshot: {shot_err})")

    dt = time.time() - t0
    print(f"\n{total - (failed if not broken else failed - len(broken))}/{total} checks passed "
          f"in {dt:.0f}s across {len(wanted)} suites")
    if broken:
        print(f"suites that did not report: {', '.join(broken)}")
    if shot_notes:
        print("\nvisual:")
        for n in shot_notes:
            print(f"  {n}")
    if not args.no_shots:
        print(f"screenshots: {SHOTS}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
