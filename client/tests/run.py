#!/usr/bin/env python3
"""NEPTUNE client test runner — browser checks against the REAL dashboard.

No framework, no dependencies, no build step: the same rule the client itself
follows. Python standard library plus a Chrome that is already installed.

WHAT IT DOES
    Serves client/ over loopback, injects one suite from suites/ into index.html
    as an extra <script>, opens it in headless Chrome, and waits for the suite to
    POST its results back. The page under test is the shipping dashboard byte for
    byte plus that one tag — nothing is stubbed, mocked or rebuilt.

A SUITE THAT NEVER RAN IS NOT A SUITE THAT PASSED
    Deliberately the same vocabulary as api/tests/run.py, which learned it first: a
    failed check is a FINDING — the code was exercised and came out the wrong shape.
    A suite that never loaded is an ABSENCE of findings: nothing about that code was
    exercised at all, and no number of green checks elsewhere makes up for it.
    This runner used to have no word for that. A suite that timed out was counted as
    one failure, then SUBTRACTED again from the headline, so a run with a dead suite
    printed "320/320 checks passed ... across 12 suites" — a full pass, twenty checks
    short, with the truth on a separate line underneath that nobody pastes into a
    README. Those suites are now marked, counted separately, named in the verdict,
    and cannot be read as a pass:

        DEPS   no browser on this machine at all — nothing ran
        NONE   the browser never fetched the suite: it never loaded
        ?      it loaded, and never said what it verified (hung, or reported nothing)
        CRASH  it threw; the checks after the throw never happened

    The last two are the reason the page is served by us: the server KNOWS whether
    index.html and __suite.js were ever asked for, so "the browser never started" and
    "the suite hung on check 14" are told apart by evidence rather than guessed at.

USAGE
    python client/tests/run.py                 # every suite
    python client/tests/run.py tether          # one suite (substring match)
    python client/tests/run.py --headed        # watch it happen
    python client/tests/run.py --list

    Exit status:
        0   every suite loaded, reported, and every check passed
        1   a check failed, or a suite crashed (both are findings)
        2   nothing failed, but something could not be RUN — no browser, a suite
            that never loaded, a suite that never reported. Not success. Non-zero
            on purpose, so a pre-push gate stops either way.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import platform as platform_mod
import re
import shutil
import signal
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

# Throwaway Chrome profiles are named with this prefix so a leaked one can be recognised
# as ours and swept. On a handheld with a 512 GB eMMC and a suite that opens 13 profiles
# a run, "it will get cleaned up eventually" is how a disk fills.
PROFILE_PREFIX = "neptune-test-"

# How each browser is found, per platform. Kept as three separate lists rather than one
# long one because the list is also DIAGNOSTIC: when nothing is found the runner prints
# where it looked, and printing Windows paths to someone on a Pi wastes the one line
# that was supposed to help them.
#
# Chrome is what the ROG Ally runs this on; Edge is the same engine and is already on
# every Windows box. On Raspberry Pi OS the browser is Chromium and its name has moved:
# Bookworm ships /usr/bin/chromium, Bullseye and older ship chromium-browser, and the
# real binary can sit in /usr/lib/chromium-browser/ with only a wrapper script on PATH.
# All of them are listed because `shutil.which` alone finds none of it when PATH is the
# stripped-down one a systemd unit or an ssh command gets.
_CANDIDATES = {
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        os.path.expanduser("~/Applications/Chromium.app/Contents/MacOS/Chromium"),
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ],
    "linux": [
        "/usr/bin/chromium",                              # Raspberry Pi OS Bookworm
        "/usr/bin/chromium-browser",                      # Bullseye and older
        "/usr/lib/chromium-browser/chromium-browser",     # the real binary behind the wrapper
        "/usr/lib/chromium/chromium",
        "/snap/bin/chromium",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/opt/google/chrome/chrome",
        "/usr/bin/microsoft-edge",
    ],
}
_ON_PATH = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
            "chrome", "msedge", "brave-browser")


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


def chrome_candidates() -> list[str]:
    """The install paths worth stat-ing on THIS platform, in order of preference."""
    key = "darwin" if sys.platform == "darwin" else ("win32" if os.name == "nt" else "linux")
    return [p for p in _CANDIDATES[key] if p]


def find_chrome(explicit: str | None) -> str | None:
    """The browser to drive, or None if this machine has none.

    None, and not sys.exit: a missing browser is the same class of thing as a missing
    pydantic in api/tests/run.py — an environment that cannot run the checks, which has
    to be REPORTED as such per suite and end in exit 2, not vanish the run.
    """
    if explicit:
        if not Path(explicit).exists():
            # Exit 2, not 1. This runner's own vocabulary reserves 1 for "a check did
            # not pass" and 2 for "something could not be run", and a browser that is
            # not where you said it is belongs squarely in the second - the product has
            # not been judged at all. sys.exit(str) would have used 1.
            print(f"--chrome {explicit} does not exist", file=sys.stderr)
            raise SystemExit(2)
        return explicit
    env = os.environ.get("NEPTUNE_CHROME")
    if env:
        if Path(env).exists():
            return env
        # Said out loud. Silently falling through to auto-detection meant the operator
        # who set the variable read a result from a browser they did not choose, and
        # the typo in their path was never mentioned by anything.
        # ...and then STOP. Warning and carrying on meant the operator who set the
        # variable read a green result from a browser they had not chosen, with their
        # typo mentioned once in a line scrolled off the top. An explicit choice that
        # cannot be honoured is a could-not-run, not a detail.
        print(f"NEPTUNE_CHROME={env} does not exist - refusing to silently use another "
              f"browser. Fix the path or unset it to auto-detect.", file=sys.stderr)
        raise SystemExit(2)
    for p in chrome_candidates():
        if Path(p).exists():
            return p
    for name in _ON_PATH:
        found = shutil.which(name)
        if found:
            return found
    return None


_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_VERSION_DIR = re.compile(r"\d+(?:\.\d+)+")


def browser_version(path: str) -> str:
    """What the browser calls itself, e.g. "chrome 151.0.7922.76", or "".

    PRINTED, so a "339/339 passed" pasted into a README can be traced to a machine and
    an engine, and USED: --headless=new does not exist before Chromium 109, which is
    still what an unpatched Pi OS image can be carrying.
    """
    if os.name != "nt":
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True,
                                 timeout=20).stdout.strip()
        except Exception:  # noqa: BLE001 — a browser that will not answer is not a failure
            out = ""
        if out:
            return out
    # NOT asked on Windows, and the answer would be empty anyway: chrome.exe is a GUI
    # binary, so --version writes to a console it does not have and prints nothing here.
    # The version is on disk instead — every Chrome and Edge install keeps its payload
    # in a sibling directory named for it.
    try:
        vers = [d.name for d in Path(path).resolve().parent.iterdir()
                if d.is_dir() and _VERSION_DIR.fullmatch(d.name)]
        if vers:
            newest = max(vers, key=lambda v: tuple(int(x) for x in v.split(".")))
            return f"{Path(path).stem} {newest}"
    except OSError:
        pass
    return ""


def _major(version: str) -> int | None:
    m = _VERSION_RE.search(version or "")
    return int(m.group(1)) if m else None


def _no_sandbox_reason() -> str | None:
    """Why Chrome's sandbox cannot start on this Linux box, or None if it can.

    Two conditions, both routine on a Raspberry Pi and neither of them something the
    person running the tests can be expected to know about:

      * running as root — anything that touches GPIO on a Pi does, and Chrome refuses
        to start at all as root ("Running as root without --no-sandbox is not supported")
      * unprivileged user namespaces switched off in the kernel, which is how several
        Pi and container images ship; the zygote then dies with "Failed to move to a
        new namespace" before a single byte is served

    From up here both look identical: the browser exits instantly, nothing is fetched,
    and the suite reports "never loaded" with no clue why. Scoped to Linux and to these
    two facts on purpose — passing --no-sandbox unconditionally would switch off a real
    protection on every machine in order to fix a Pi.
    """
    if os.name == "nt" or sys.platform == "darwin":
        return None
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return "running as root"
    for probe in ("/proc/sys/kernel/unprivileged_userns_clone",
                  "/proc/sys/user/max_user_namespaces"):
        try:
            if Path(probe).read_text().strip() == "0":
                return f"user namespaces disabled ({probe} is 0)"
        except OSError:
            pass
    return None


def launch_args(chrome: str, version: str, profile: str, url: str,
                headed: bool) -> list[str]:
    """The full command line, assembled where it can be read and reasoned about.

    A pure function on purpose: the Pi-only flags below cannot be exercised from a
    Windows handheld any other way, and a flag list built inline inside run_suite is a
    flag list nobody can check without a Raspberry Pi in front of them.
    """
    args = [chrome, f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--disable-background-networking",
            # The suites are TIMING code — `await sleep(2500)`, 100 ms telemetry feeds.
            # A headless window counts as occluded, and an occluded window gets its
            # timers throttled to once a minute, which turns a passing suite into a
            # twenty-minute timeout on a machine slow enough to be backgrounded.
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            # Port 0 = "pick one and write it down" (see cdp.devtools_port). Asking for
            # a specific port means racing every other process on the box for it.
            "--remote-debugging-port=0",
            "--window-size=1280,800", url]
    if not headed:
        # --headless=new is what every current build wants; Chromium before 109 has no
        # such mode and reads the whole switch as a plain --headless, but an image old
        # enough to matter is exactly the image nobody can test on, so ask rather than
        # assume. --disable-gpu is not a Pi flag: headless has no display to hand the
        # GPU process, on any of the three platforms.
        mode = "--headless" if (_major(version) or 999) < 109 else "--headless=new"
        args[1:1] = [mode, "--disable-gpu"]
    if os.name != "nt" and sys.platform != "darwin":
        # /dev/shm is where Chrome puts its shared memory, and on a memory-constrained
        # Pi (or anything in a container) it is small enough that the renderer dies
        # mid-suite with "tab crashed" and no other explanation. Writing those buffers
        # to the profile directory instead is slower and never runs out.
        args[1:1] = ["--disable-dev-shm-usage"]
        if _no_sandbox_reason():
            args[1:1] = ["--no-sandbox"]
    return args


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # NOT allow_reuse_address on Windows. There SO_REUSEADDR does not mean what it means
    # on POSIX: it lets a second socket bind a port another process is already listening
    # on, and then the requests go to whichever the kernel feels like. On a machine that
    # is also running the real client on a nearby port, that is a test run silently
    # served by something else. POSIX keeps it, where it only forgives TIME_WAIT.
    allow_reuse_address = os.name != "nt"


def make_handler(suite_path: Path, result_box: dict, done: threading.Event, seen: dict):
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
                # Recorded because it is EVIDENCE. This one fetch is the difference
                # between "the browser never got here" and "the suite ran and hung",
                # which need completely different reactions from whoever reads the
                # report - and every other way of telling them apart is a guess.
                seen["suite"] = True
                self._send(PREAMBLE + suite_path.read_bytes(), "application/javascript")
                return
            if path in ("/", "/index.html"):
                seen["index"] = True
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


# The four ways a suite can end without a usable result, and the one where it works.
# Named rather than passed around as bare strings so that the difference between them
# survives being read six months later.
REPORTED = "reported"       # it posted checks; they may pass or fail, that is a finding
CRASHED = "crashed"         # it threw: the checks after the throw never happened
INCOMPLETE = "incomplete"   # it loaded and never said what it verified
NEVER = "never"             # the browser never even fetched it


class Outcome:
    __slots__ = ("status", "checks", "note", "evidence", "shot_err", "leaked")

    def __init__(self, status: str, checks=None, note: str = "", evidence=(),
                 shot_err=None, leaked: str | None = None):
        self.status = status
        self.checks = checks
        self.note = note              # ONE line: what happened
        self.evidence = list(evidence)  # supporting lines, which are not the same thing
        self.shot_err = shot_err
        self.leaked = leaked          # a profile directory that would not delete


def kill_browser(proc: subprocess.Popen) -> None:
    """Stop the browser and everything it started, on all three platforms.

    Chrome is a process TREE — a browser process, a zygote, one renderer per frame, a
    GPU process. proc.terminate() only ever addressed the first of them, so on Windows
    the renderers outlived the run (and kept a handle on the profile directory, which
    is why the profile then would not delete), and a run interrupted halfway left them
    behind for good. Graceful first, because a Chrome that is allowed to exit releases
    those handles itself and the profile then disappears in one attempt.
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            # Its own session (see start_new_session), so the whole group goes at once:
            # SIGTERM to the browser alone leaves an orphaned zygote on a Pi.
            os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, PermissionError):
        pass
    try:
        proc.wait(timeout=10)
        # It went on its own, so its children went with it. Nothing may be killed by
        # PID from here on: the number belongs to whatever the OS hands it to next,
        # and taskkill /T on a recycled PID would take down an innocent process tree.
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "nt":
        # /T for the tree, /F because a wedged renderer ignores anything politer.
        # taskkill ships with every Windows since XP, so this needs nothing installed.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _rmtree_stubborn(path: str, tries: int = 5) -> bool:
    """Delete a profile directory, and say whether it actually went.

    ignore_errors=True was hiding the failure it was papering over: on Windows a
    renderer that has not finished exiting still holds files under the profile, the
    delete fails, nothing is printed, and the handheld quietly accumulates a 40 MB
    directory per suite per run. Retry briefly (the handles go within a second of the
    process actually exiting), then say so out loud.
    """
    def _chmod_retry(func, target, _exc):
        # A read-only file on Windows refuses unlink until the flag is cleared.
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass

    for attempt in range(tries):
        try:
            # onexc since 3.12, onerror before it: the runner has to work on the
            # python a Pi image ships with, not only the newest one.
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=_chmod_retry)
            else:
                shutil.rmtree(path, onerror=_chmod_retry)
        except OSError:
            pass
        if not os.path.exists(path):
            return True
        time.sleep(0.3 * (attempt + 1))
    return not os.path.exists(path)


def sweep_stale_profiles(older_than_s: float = 7200.0) -> int:
    """Remove profile directories a previous run left behind. Returns how many.

    Only ones older than two hours, so a second runner working in the same temp
    directory at the same time is never robbed of its profile mid-suite.
    """
    swept = 0
    now = time.time()
    try:
        for d in Path(tempfile.gettempdir()).glob(PROFILE_PREFIX + "*"):
            try:
                if d.is_dir() and now - d.stat().st_mtime > older_than_s:
                    if _rmtree_stubborn(str(d), tries=1):
                        swept += 1
            except OSError:
                pass
    except OSError:
        pass
    return swept


def _tail(path: Path, n: int = 4) -> list[str]:
    """The last few non-empty lines a dying browser wrote to stderr.

    Kept for exactly one case: the browser exited before serving anything. That is
    where the sandbox and namespace refusals on a Pi land, and the message is always
    perfectly clear — it was just being written to /dev/null.
    """
    try:
        lines = [ln.strip() for ln in path.read_text(errors="replace").splitlines()
                 if ln.strip()]
    except OSError:
        return []
    return lines[-n:]


def _read_checks(raw: str):
    """(checks, why-not). A result is only a result if it is a list of checks.

    A suite that posts `[]` has told us nothing, and printing "ok 0/0" for it would be
    the same false green this runner exists to stop: no assertion ran, and the line
    reads exactly like one that verified everything.
    """
    text = (raw or "").strip()
    if not text:
        return None, "posted an empty body"
    if text.startswith("SUITE CRASHED") or text.startswith("THREW"):
        return None, text            # a crash, recognised by the caller
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, text[:800]
    if not isinstance(parsed, list):
        return None, f"posted {type(parsed).__name__}, not a list of checks"
    if not parsed:
        return None, "reported 0 checks - nothing was verified"
    for c in parsed:
        if not isinstance(c, dict) or "pass" not in c:
            return None, f"posted something that is not a check: {str(c)[:120]}"
    return parsed, None


def run_suite(suite: Path, chrome: str, version: str, timeout: float, headed: bool,
              keep: bool, shots: bool = True) -> Outcome:
    result_box: dict = {}
    seen: dict = {}
    done = threading.Event()
    # Bind port 0 and ask the socket which port it got. free_port() picked one, closed
    # it, and hoped it was still free a moment later — a race that on Windows loses
    # silently, because SO_REUSEADDR there will happily bind a port somebody else holds.
    srv = Server(("127.0.0.1", 0), make_handler(suite, result_box, done, seen))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    profile = tempfile.mkdtemp(prefix=PROFILE_PREFIX)
    errlog = Path(profile) / "browser-stderr.log"
    url = f"http://127.0.0.1:{port}/index.html{suite_url_suffix(suite)}"
    args = launch_args(chrome, version, profile, url, headed)
    # start_new_session so kill_browser can take the whole process group; ignored on
    # Windows, which has no such thing and uses taskkill /T instead.
    with open(errlog, "wb") as elog:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=elog,
                                start_new_session=(os.name != "nt"))

    shot_err = None
    browser_rc = None
    try:
        # Poll instead of one long wait(). A browser that refuses to start — no
        # sandbox on a Pi, a bad flag, killed from outside — used to cost the FULL
        # timeout per suite before anyone was told, which on a 13-suite run is 26
        # minutes of watching nothing happen and then a wall of "timed out".
        deadline = time.time() + timeout
        while not done.wait(0.25):
            if proc.poll() is not None:
                browser_rc = proc.returncode
                done.wait(1.0)      # a result already in flight still counts
                break
            if time.time() > deadline:
                break
        # Photograph the page in the state the suite left it — before anything is torn
        # down. A numeric check says a rule held; the picture is the only thing that
        # catches "technically correct and visibly wrong".
        # Photographed even when the suite never reported, as long as the browser is
        # still up: a picture of a hung dashboard is the only evidence that survives a
        # timeout, and it costs nothing here. check_shot never looks at it — only
        # suites that reported are compared against a baseline.
        if shots and proc.poll() is None:
            try:
                from cdp import capture_png, devtools_port
                cdp_port = devtools_port(profile)
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
        kill_browser(proc)
        srv.shutdown()
        srv.server_close()      # the listening socket, not just the serve loop: 13
                                # suites leaked 13 sockets for the life of the run
        stderr_tail = _tail(errlog)
        leaked = None
        if not keep and not _rmtree_stubborn(profile):
            leaked = profile

    if not done.is_set():
        evidence = []
        if browser_rc is not None:
            why = f"the browser exited ({browser_rc}) before the suite reported"
            # Offered as EVIDENCE, never as the cause. Chrome writes plenty to stderr
            # that has nothing to do with why it died — a killed browser's last line
            # here was a Google Cloud Messaging registration error — and quoting the
            # last line as the reason would send the reader after the wrong thing. On
            # a Pi the sandbox and namespace refusals do land here, which is the whole
            # point of keeping them instead of /dev/null.
            if stderr_tail:
                evidence = ["the browser's last words on stderr (the exit code is the "
                            "fact; these may be noise):"]
                evidence += [f"  {line[:200]}" for line in stderr_tail[-3:]]
        else:
            why = f"timed out after {timeout:.0f}s"
        if not seen.get("suite"):
            where = ("the page was served, but the injected suite was never fetched"
                     if seen.get("index") else "nothing was ever fetched from the server")
            return Outcome(NEVER, note=f"{why} - {where}", evidence=evidence,
                           shot_err=shot_err, leaked=leaked)
        return Outcome(INCOMPLETE, note=f"loaded, then {why}", evidence=evidence,
                       shot_err=shot_err, leaked=leaked)

    checks, why = _read_checks(result_box.get("raw", ""))
    if checks is None:
        why = why or ""
        status = CRASHED if why.startswith(("SUITE CRASHED", "THREW")) else INCOMPLETE
        lines = why[:800].splitlines() or [""]
        return Outcome(status, note=lines[0], evidence=lines[1:6], shot_err=shot_err,
                       leaked=leaked)
    return Outcome(REPORTED, checks=checks, shot_err=shot_err, leaked=leaked)


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


def print_header(chrome: str | None, version: str, n_suites: int) -> None:
    """Say which machine and which engine this run is about, before it runs.

    A result is only evidence if it can be traced back to what produced it: this suite
    is quoted in four documents and none of them could say whether the number came off
    the handheld, a laptop or a Pi, or out of Chrome or Chromium.
    """
    print(f"machine : {platform_mod.platform()}  ({sys.platform}, "
          f"{platform_mod.machine() or 'unknown arch'})")
    print(f"python  : {sys.version.split()[0]}  {sys.executable}")
    if chrome:
        print(f"browser : {chrome}" + (f"  ({version})" if version else "  (version unknown)"))
    else:
        print("browser : NONE FOUND - no checks can run on this machine")
    why = _no_sandbox_reason()
    if why and chrome:
        # Announced, never quietly done. Turning the browser sandbox off is a real
        # change to what the run is; the operator gets to see that it happened and why.
        print(f"sandbox : OFF - {why} (Chrome cannot start with it on here)")
    print(f"client  : {CLIENT}")
    print(f"suites  : {n_suites}\n")


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
    ap.add_argument("--loose-visual", action="store_true",
                    help="report visual drift without failing the run (see check_shot)")
    # Kept so anything that already passes it still works; it is now the default.
    ap.add_argument("--strict-visual", action="store_true", help=argparse.SUPPRESS)
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
    version = browser_version(chrome) if chrome else ""
    print_header(chrome, version, len(wanted))

    swept = sweep_stale_profiles()
    if swept:
        print(f"swept {swept} leaked Chrome profile(s) from an earlier run "
              f"out of {tempfile.gettempdir()}\n")

    total = passed = failed = 0
    never: dict[str, str] = {}         # suite -> why it never loaded
    incomplete: dict[str, str] = {}    # suite -> what it did instead of reporting
    crashed: list[str] = []
    drifted: list[str] = []
    leaked: list[str] = []
    shot_notes: list[str] = []
    t0 = time.time()

    for suite in wanted:
        name = suite.stem
        if chrome is None:
            # One line per suite even though not one of them can run: the count of
            # suites is part of the report, and a run that prints nothing where the
            # results go reads like a run that had nothing to do.
            print(f"  {name:<24} DEPS  {'-':>3}/-   never loaded: needs Chrome or Chromium")
            never[name] = "no browser on this machine"
            continue

        # ONE SUITE MUST NOT BE ABLE TO DESTROY THE WHOLE REPORT. run_suite already
        # turns an in-page failure into a status; what it cannot survive is the suite
        # FILE going unreadable between enumeration and execution (a checkout switching
        # underneath, an editor mid-save, a permission change). That raised out here and
        # killed the process, taking with it every suite that had already passed - and
        # leaking that suite's profile directory, socket and server thread on the way
        # out. A run that loses its findings to an unrelated I/O error has told the
        # operator nothing, which is exactly the silence this runner exists to refuse.
        try:
            out = run_suite(suite, chrome, version, args.timeout, args.headed,
                            args.keep, shots=not args.no_shots)
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:<24} CRASH {'-':>3}/-   the runner itself failed: {exc}")
            never[name] = f"the runner failed on this suite: {exc}"
            continue
        if out.leaked:
            leaked.append(out.leaked)

        if out.status == NEVER:
            print(f"  {name:<24} NONE  {'-':>3}/-   never loaded: {out.note}")
            never[name] = out.note
        elif out.status == INCOMPLETE:
            # A count here would be a number where there is no measurement — the same
            # rule the console follows for a dead sensor, and the same one api's DEPS
            # line follows. "?" is what this project writes when it cannot tell.
            print(f"  {name:<24} ?     {'-':>3}/-   INCOMPLETE: {out.note}")
            incomplete[name] = out.note
        elif out.status == CRASHED:
            print(f"  {name:<24} CRASH {'-':>3}/-   threw before it reported: {out.note}")
            crashed.append(name)
        if out.status != REPORTED:
            for line in out.evidence:
                print(f"      {line}")
            continue

        checks = out.checks
        bad = [c for c in checks if not c.get("pass")]
        total += len(checks)
        passed += len(checks) - len(bad)
        failed += len(bad)
        mark = "ok" if not bad else "FAIL"
        print(f"  {name:<24} {mark:<5} {len(checks)-len(bad):>3}/{len(checks)}")
        for c in checks:
            if args.verbose and c.get("pass"):
                print(f"      pass  {c.get('name','?')} -- {c.get('detail','')}")
        for c in bad:
            print(f"      FAIL  {c.get('name','?')}")
            print(f"            {c.get('detail','')}")
        if not args.no_shots:
            note, drift = check_shot(name, args.bless)
            if note:
                print(f"      {note}")
                shot_notes.append(f"{name}: {note}")
            # DRIFT FAILS THE RUN. It used to only report, and the reasoning was
            # sound at the time: the dashboard is a live instrument photographed
            # mid-flight, the imagery arrives over the network, things pulse, and a
            # gate that cries wolf gets muted inside a week. But every one of those
            # sources has since been removed from the portrait - the map and video are
            # hidden for the layout shot, and animations and transitions are frozen, so
            # two identical runs now differ by zero pixels rather than by up to 0.24%.
            # What was left was a check that printed "VISUAL DRIFT 1.2%" on twelve
            # suites and exited 0, which is the shape of a test nobody acts on: the
            # baselines went twelve rounds without being re-blessed and the picture
            # stopped being looked at. An intentional change says so with --bless;
            # --loose-visual is there for a machine where the noise floor is genuinely
            # different, and it has to be asked for.
            if drift:
                drifted.append(name)
        if out.shot_err:
            print(f"      (no screenshot: {out.shot_err})")

    dt = time.time() - t0
    ran = len(wanted) - len(never) - len(incomplete) - len(crashed)
    across = (f"{ran} of {len(wanted)} suites" if ran != len(wanted)
              else f"{len(wanted)} suite{'' if len(wanted) == 1 else 's'}")
    print(f"\n{passed}/{total} checks passed in {dt:.0f}s across {across}")

    if shot_notes:
        print("\nvisual:")
        for n in shot_notes:
            print(f"  {n}")
    if drifted and args.strict_visual:
        print(f"\nVISUAL DRIFT fails this run (--strict-visual): {', '.join(drifted)}")
    if leaked:
        # Never silently. A profile that would not delete is 40 MB of a handheld's disk,
        # and the only moment anybody can act on it is now, while its name is known.
        print(f"\n{len(leaked)} Chrome profile(s) could not be deleted - delete by hand:")
        for p in leaked:
            print(f"  {p}")

    # The verdict goes LAST because it is the line a person actually reads, and when
    # something could not be run it has to be the thing that contradicts the pass count
    # above it rather than a footnote above it.
    if never or incomplete or crashed:
        print("\nINCOMPLETE - this run certifies nothing about the console as a whole.")
        if never:
            print(f"  {len(never)} of {len(wanted)} suites never loaded: "
                  f"{', '.join(sorted(never))}")
        if incomplete:
            print(f"  {len(incomplete)} of {len(wanted)} suites loaded and never reported "
                  f"a result: {', '.join(sorted(incomplete))}")
        if crashed:
            print(f"  {len(crashed)} of {len(wanted)} suites threw partway through: "
                  f"{', '.join(sorted(crashed))} - the checks after the throw never ran")
        print("  the total above is short by those suites; it is not a whole-console pass")
        if chrome is None:
            print("\n  No Chrome, Chromium or Edge on this machine. The client IS a browser")
            print("  app, so there is nothing here that can run it. Install one, or point")
            print("  the runner at a copy it cannot find by itself:")
            print("      NEPTUNE_CHROME=<path to the binary>   (or --chrome <path>)")
            print("  Looked in:")
            for p in chrome_candidates():
                print(f"      {p}")
            print(f"      and on PATH: {', '.join(_ON_PATH)}")
        else:
            print(f"  browser: {chrome}" + (f"  ({version})" if version else ""))

    if not args.no_shots:
        print(f"screenshots: {SHOTS}")

    if failed or crashed or (drifted and not args.loose_visual):
        return 1
    if never or incomplete:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
