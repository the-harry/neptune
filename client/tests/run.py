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
            if self.path.startswith("/__suite.js"):
                self._send(PREAMBLE + suite_path.read_bytes(), "application/javascript")
                return
            if self.path in ("/", "/index.html"):
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


def run_suite(suite: Path, chrome: str, timeout: float, headed: bool, keep: bool):
    port = free_port()
    result_box: dict = {}
    done = threading.Event()
    srv = Server(("127.0.0.1", port), make_handler(suite, result_box, done))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    profile = tempfile.mkdtemp(prefix="neptune-test-")
    args = [chrome, f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--disable-background-networking",
            "--window-size=1280,800", f"http://127.0.0.1:{port}/index.html"]
    if not headed:
        args[1:1] = ["--headless=new", "--disable-gpu"]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        finished = done.wait(timeout=timeout)
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
        return None, f"timed out after {timeout:.0f}s - the suite never reported back"
    raw = result_box.get("raw", "")
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        # A suite that throws posts its stack instead of JSON. That IS the result.
        return None, raw.strip()[:800]


def main():
    ap = argparse.ArgumentParser(description="Run the Neptune client browser tests.")
    ap.add_argument("suites", nargs="*", help="substring(s) of suite names; default: all")
    ap.add_argument("--chrome", help="path to chrome.exe (else NEPTUNE_CHROME, else auto)")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds per suite")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway Chrome profiles")
    ap.add_argument("--list", action="store_true", help="list the suites and exit")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every check, not just failures")
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

    chrome = find_chrome(args.chrome)
    print(f"chrome : {chrome}")
    print(f"client : {CLIENT}")
    print(f"suites : {len(wanted)}\n")

    total = failed = 0
    broken: list[str] = []
    t0 = time.time()

    for suite in wanted:
        checks, err = run_suite(suite, chrome, args.timeout, args.headed, args.keep)
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

    dt = time.time() - t0
    print(f"\n{total - (failed if not broken else failed - len(broken))}/{total} checks passed "
          f"in {dt:.0f}s across {len(wanted)} suites")
    if broken:
        print(f"suites that did not report: {', '.join(broken)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
