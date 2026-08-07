#!/usr/bin/env python3
"""NEPTUNE bootstrap — take a fresh machine to a working state, and say what is missing.

    python bootstrap.py            # check everything, change nothing
    python bootstrap.py --dev      # also build the API venv (topside development)
    python bootstrap.py --test     # also run the test suites (client, and the API's if present)

There are two machines in this system and they need opposite things:

    TOPSIDE   the ROG Ally. Runs the CLIENT and nothing else. Needs a browser and
              (for development) Python. The Pi is not required to fly the simulator.
    VEHICLE   the Raspberry Pi. Runs the API, go2rtc and nginx. Installed by
              install.sh, which this will point you at rather than duplicate.

Deliberately read-only unless asked. A bootstrap that silently installs things is
one you cannot run to find out where you stand, which is the main reason to have one.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# The two venvs this repo can have, in the order they are believed: --dev builds ROOT/.venv
# on a dev box, install.sh builds INSTALL_DIR/api/.venv on the Pi (the one the systemd unit
# runs uvicorn from). Named here so nothing hardcodes either path twice.
VENV = ROOT / ".venv"
PI_VENV = ROOT / "api" / ".venv"
OK, WARN, BAD = "  ok  ", " note ", " MISS "

# The Pi-only libraries RealHardware imports LAZILY, inside itself: (module to probe,
# what to install, what stops working without it). They are absent on a dev box by
# design - this file is developed on a machine with no GPIO - which is why the probe
# is find_spec and never an import: importing gpiozero off a Pi is slow at best.
# Keep this list in step with api/requirements.txt and docs/hardware.md §1.3.
HW_DEPS = (
    ("gpiozero", "gpiozero",
     "thrusters, stepper, limit switches, leak probes, pulse counting"),
    ("smbus2", "smbus2",
     "I2C bus: MS5837 depth, INA219 pack voltage/current"),
    ("adafruit_bno08x", "adafruit-circuitpython-bno08x",
     "BNO085: heading, mag-cal status, gyro rate, linear accel"),
)

# The api's CORE libraries: the first section of api/requirements.txt, the ones BOTH
# machines need if they are to run the api at all. Kept apart from HW_DEPS because the
# two absences mean opposite things, and conflating them is how an afternoon gets lost:
# a missing gpiozero on the bench is correct and expected, a missing pydantic is a
# machine that cannot start the server or import half its own test suites.
CORE_DEPS = (
    ("fastapi", "the HTTP + WebSocket app in api/main.py"),
    ("uvicorn", "the ASGI server that app is served by"),
    ("pydantic", "protocol.py and nav/models.py - the client/server wire contract"),
    ("httpx", "WOLFANG CGI client, file offload, thumbnails"),
)


def say(mark, what, detail=""):
    print(f"[{mark}] {what}" + (f"   {detail}" if detail else ""))


def call(cmd, **kw) -> int:
    """subprocess.call, with this process's own output flushed out of the way first.

    Both test suites run as children that inherit this stdout. Straight to a terminal
    that is line-buffered and the order is the order you wrote it in; redirected to a
    file it is BLOCK-buffered, so every word bootstrap had printed sat in the buffer
    until exit - and `python bootstrap.py --test > report.txt` produced a file that
    opened with the client suite's whole run (45 lines, measured by redirecting it on
    its own) and only reached "NEPTUNE bootstrap" after all of it, with each section
    heading below the section it introduces. A report whose parts arrive in a
    different order depending on whether it was piped is not a report, and this is the
    file people pipe when they are asking someone else for help.

    The line count above is the measured one, and it is written here because the
    number that used to be in its place was 295 - the client suite's CHECK total,
    pasted in as though it were a line total. Two different quantities wearing one
    number is the same class of mistake as the stale counts below.
    """
    sys.stdout.flush()
    return subprocess.call(cmd, **kw)


def interpreter_in(venv: Path) -> Path:
    """Where python lives inside `venv` - Scripts on Windows, bin everywhere else.
    The path is built even when nothing is there yet, because --dev needs it to create it."""
    return venv / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")


def venv_python() -> Path | None:
    """The interpreter of whichever venv this machine actually has, or None for neither.

    ONE definition on purpose, and it knows about both machines: --dev installs the API
    deps into ROOT/.venv here, install.sh installs them into api/.venv on the Pi. Anything
    that runs the API's own code has to use that interpreter, because `sys.executable` is
    merely whatever python typed `bootstrap.py` - usually the bare system one, with none of
    those deps - and a suite run under it fails on imports and reports the repo broken on
    the very machine this run has just finished certifying.
    """
    return next((p for p in map(interpreter_in, (VENV, PI_VENV)) if p.exists()), None)


def is_pi() -> bool:
    try:
        return "raspberry pi" in Path("/proc/device-tree/model").read_text(errors="ignore").lower()
    except Exception:  # noqa: BLE001
        return False


def find_browser() -> str | None:
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if p and Path(p).exists():
            return p
    for n in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge"):
        w = shutil.which(n)
        if w:
            return w
    return None


def check_common() -> int:
    missing = 0
    v = sys.version_info
    if v >= (3, 9):
        say(OK, "python", f"{v.major}.{v.minor}.{v.micro}")
    else:
        say(BAD, "python 3.9+", f"found {v.major}.{v.minor}")
        missing += 1
    say(OK if shutil.which("git") else WARN, "git",
        shutil.which("git") or "not on PATH (only needed to update)")
    for p in ("client/index.html", "api/main.py", "api/requirements.txt"):
        if not (ROOT / p).exists():
            say(BAD, f"{p}", "missing - is this a full checkout?")
            missing += 1
    return missing


def topside(args) -> int:
    print("\n--- TOPSIDE (the handheld: runs the client) ---")
    missing = 0
    br = find_browser()
    if br:
        say(OK, "browser", br)
    else:
        say(BAD, "browser", "install Chrome or Edge - the client IS a browser app")
        missing += 1

    launcher = ROOT / "client" / "launch" / "Neptune.bat"
    say(OK if launcher.exists() else BAD, "launcher", str(launcher))
    # Counted, not merely marked. A missing launcher printed [ MISS ] and then fell
    # through to "Everything this machine needs is present." at the bottom, because the
    # line was drawn from a condition nobody added to the tally - a report that
    # contradicts itself six lines apart is worse than one that never mentioned it.
    missing += (0 if launcher.exists() else 1)

    hostfile = ROOT / "client" / "launch" / "neptune-host.txt"
    if hostfile.exists():
        say(OK, "vehicle address", hostfile.read_text(encoding="utf-8", errors="replace").strip()
            or "(empty - the client will run the simulator)")
    else:
        say(WARN, "vehicle address", "no neptune-host.txt - the client runs the simulator")

    if platform.system() == "Windows":
        setup = ROOT / "client" / "launch" / "tether-setup.ps1"
        say(WARN, "one-time setup", f"run as Administrator once: {setup}")

    print("\n  To fly the simulator right now, with no vehicle and no network:")
    print(f"    {launcher}")
    print("  Or open the client from any static server and add ?sim=1 to the URL.")

    if args.test:
        print("\n--- CLIENT TESTS ---")
        if not br:
            say(BAD, "tests", "need a browser")
            return missing + 1
        rc = call([sys.executable, str(ROOT / "client" / "tests" / "run.py")])
        missing += (1 if rc else 0)
    else:
        # THE SUITE COUNT IS COUNTED, NOT REMEMBERED - one file per suite, so the one
        # number that can drift silently is derived from the tree instead. The CHECK
        # total cannot be: a browser check is an `ok(...)` call inside an async flow,
        # several of them inside loops, so nothing short of running Chrome knows how
        # many there are. So it stays a MEASURED figure with the measurement attached.
        #
        # MEASURED on the ROG Ally, re-run 2026-08-07: "295/295 checks passed in 109s
        # across 12 suites", exit 0. FOUR different totals for this one suite were in
        # circulation simultaneously - 214 on this very line, 249 in
        # client/tests/README.md, 286 in client/README.md and .specs/design.md, and 295
        # in reality - because each was copied forward from whichever tree the writer had
        # open rather than from a run. A stale number here is worse than none:
        # someone who reads 286 and watches 295 go by has been told the bench is
        # running something other than what it is, and then has no reason to believe
        # anything else this file prints. Re-measure by RUNNING it whenever a suite is
        # added, never by adjusting it until it feels right.
        # SUITE COUNT ONLY. The check TOTAL has now gone stale three times, twice in
        # the very commit that "fixed" it, because it is a measurement that ages the
        # moment anyone adds an assertion - and a bootstrap that prints a number the
        # suite then contradicts is the one thing this file must never do. The suite
        # count comes off the tree, so it cannot drift; the check total is left to the
        # runner, which is the only thing entitled to state it.
        n = len(list((ROOT / "client" / "tests" / "suites").glob("*.js")))
        say(WARN, "tests", f"python client/tests/run.py   ({n} suites, ~2 min)")
    return missing


def vehicle(args) -> int:
    print("\n--- VEHICLE (the Raspberry Pi: runs the API) ---")
    if is_pi():
        say(OK, "hardware", "Raspberry Pi detected")
        print("\n  Install or update the backend with:")
        print("    curl -fsSL https://raw.githubusercontent.com/the-harry/neptune/master/install.sh | sudo bash")
        print("  (idempotent - re-run it to update; it never touches the client)")
        return 0

    say(WARN, "hardware", "not a Raspberry Pi - this machine is topside or a dev box")
    if not args.dev:
        say(WARN, "api venv", "pass --dev to build one for local API work")
        return 0

    print("\n--- API DEV ENVIRONMENT ---")
    venv = VENV
    py = interpreter_in(venv)
    if not py.exists():
        say(WARN, "creating venv", str(venv))
        if call([sys.executable, "-m", "venv", str(venv)]) != 0:
            say(BAD, "venv", "could not create it")
            return 1
    req = ROOT / "api" / "requirements.txt"
    say(WARN, "installing", str(req))
    if call([str(py), "-m", "pip", "install", "-q", "-r", str(req)]) != 0:
        say(BAD, "pip", "install failed")
        return 1
    say(OK, "api venv", str(venv))
    print("\n  Run the API against the bench simulator (no hardware needed):")
    # Printed in the shell of the machine it is printed ON. This line used to be
    # `cd api && NEPTUNE_HW=mock python ...` unconditionally, which is bash - and the
    # only machine that ever reaches this branch is the Windows dev box, where
    # PowerShell 5.1 rejects `&&` outright and `VAR=value cmd` is not a thing. A
    # copyable command that does not run is a worse instruction than no command.
    if platform.system() == "Windows":
        print(f"    cd {ROOT / 'api'}")
        print(f'    $env:NEPTUNE_HW="mock"; & "{py}" -m uvicorn main:app --port 8000')
    else:
        print(f"    cd {ROOT / 'api'} && NEPTUNE_HW=mock {py} -m uvicorn main:app --port 8000")
    print("  Then point the client at it:  ?host=127.0.0.1:8000")
    return 0


def hardware_deps() -> int:
    """Report the Pi-only hardware libraries. Never installs one.

    Absent on the bench is CORRECT, not broken: every hardware import in RealHardware is
    lazy, and with the wiring flag still False `NEPTUNE_HW=auto` lands on the flagged
    bench simulator regardless of what is installed. On the Pi the same absence is the
    difference between a real dive and a simulated one presented as real - the exact
    failure this system is arranged against - so there, and only there, it counts.
    """
    print("\n--- VEHICLE HARDWARE LIBRARIES (Pi only) ---")
    on_pi = is_pi()
    missing = 0
    for mod, pkg, what in HW_DEPS:
        try:
            found = importlib.util.find_spec(mod) is not None
        except Exception:  # noqa: BLE001 - a half-installed package reports absent, not raises
            found = False
        if found:
            say(OK, mod, what)
        elif on_pi:
            say(BAD, mod, f"pip install {pkg}   ({what})")
            missing += 1
        else:
            say(WARN, mod, f"not needed here - the bench runs NEPTUNE_HW=mock   ({what})")
    if not on_pi:
        print("  Nothing above is installed by this check, on purpose. See docs/hardware.md.")
    return missing


def probe_imports(py: Path | None, mods: tuple[str, ...]) -> dict[str, bool]:
    """Which of `mods` the interpreter that would actually RUN the api can import.

    Deliberately NOT find_spec in this process when a venv exists. bootstrap.py is typed
    with whatever python is on PATH - usually the bare system one - while the api's deps
    were installed into the venv. Asking this interpreter would report fastapi missing on
    a machine where `--dev` installed it thirty seconds earlier, and then print an
    install command that has already been run, which teaches a newcomer to distrust the
    one tool whose entire job is telling them where they stand.

    A venv that will not answer reports its modules ABSENT rather than assumed present:
    this check exists to find out, and "could not find out" is not "fine".
    """
    def here(m: str) -> bool:
        try:
            return importlib.util.find_spec(m) is not None
        except Exception:  # noqa: BLE001 - a half-installed package reports absent
            return False

    if py is None:
        return {m: here(m) for m in mods}
    code = ("import importlib.util as u, sys\n"
            "def ok(m):\n"
            "    try: return u.find_spec(m) is not None\n"
            "    except Exception: return False\n"
            "print(''.join('1' if ok(m) else '0' for m in sys.argv[1:]))\n")
    try:
        out = subprocess.run([str(py), "-c", code, *mods],
                             capture_output=True, text=True, timeout=60)
        flags = (out.stdout.strip().splitlines() or [""])[-1]
        if len(flags) == len(mods) and set(flags) <= {"0", "1"}:
            return {m: f == "1" for m, f in zip(mods, flags)}
    except Exception:  # noqa: BLE001 - a venv whose python is gone or broken
        pass
    return {m: False for m in mods}


def core_deps() -> int:
    """Report the api's core python libraries, and say exactly how to get them.

    This section exists because every agent and every newcomer on this bench hit the
    same wall - `No module named 'pydantic'` out of api/tests/run.py - and worked around
    it by hand with a throwaway venv, while `python bootstrap.py` sat there reporting a
    healthy machine. The file whose job is to say what state your machine is in was
    silent about the one thing wrong with it.

    Counted as MISSING only on the Pi, where the api IS the job. Topside the api is not
    needed at all: the client is a browser app with no python in it, and the simulator
    flies with nothing installed. So off the Pi this is a note with a command attached,
    not a failure - the same rule, and for the same reason, as hardware_deps().
    """
    print("\n--- API CORE LIBRARIES ---")
    py = venv_python()
    say(OK if py else WARN, "interpreter", str(py) if py else
        f"no repo venv - checking {sys.executable}")
    found = probe_imports(py, tuple(m for m, _ in CORE_DEPS))
    absent = [m for m, _ in CORE_DEPS if not found[m]]
    on_pi = is_pi()
    for mod, what in CORE_DEPS:
        # The absent ones say "not installed" in the detail, in the same shape as
        # hardware_deps. Printing only the description left four [ note ] lines that a
        # reader skims as four things that are fine.
        say(OK if found[mod] else (BAD if on_pi else WARN), mod,
            what if found[mod] else f"not installed   ({what})")
    if not absent:
        return 0
    if on_pi:
        print("\n  The vehicle cannot serve without these. Re-run install.sh; it builds")
        print(f"  {PI_VENV} from api/requirements.txt.")
        return len(absent)
    print("\n  Without these you can still fly the simulator and run the client suite -")
    print("  neither uses python at all. What you cannot do is start the api or run")
    print("  its full test suite: some suites will report as never loaded, which is")
    # Which ones is NOT written here. This sentence used to name "replay and telemetry",
    # and a third suite (liveness) was added without it - the same drift as the counts
    # below, in prose instead of digits. The API TESTS section names them from the
    # runner itself, so it says which, and this says why.
    print("  not a failure of the code. The API TESTS line below names them.")
    print("\n  One command fixes it, and nothing else has to be remembered:")
    print("      python bootstrap.py --dev")
    # Said here, next to the evidence, because the summary at the bottom of this run
    # will report a healthy machine and a reader who has just been told what they
    # cannot do deserves to know which of the two statements to believe.
    print("\n  None of this is counted as missing below: a topside handheld runs the")
    print("  client and never the api, so on that machine the absence is correct.")
    return 0


def api_suite_totals(py: Path | None):
    """(suites, checks, names that cannot load here) — ASKED, never typed.

    THIS NUMBER HAS NOW DRIFTED THREE TIMES, and the third time it was already stale in
    the commit that introduced it: this line advertised "4 suites, 147 checks" against a
    tree that runs 5 suites and 229 checks, and the no-deps figure beside it claimed
    "100/103 across 2 of 4" where the bench actually reports "100/104 across 2 of 5". A
    hand-copied count cannot survive a suite being added, because nothing fails when it
    goes wrong - it just quietly starts describing a different repo, which is precisely
    what this file exists not to do.

    So it is derived from the thing that produces it. `api/tests/run.py --list` names
    every suite it discovered and how many checks are in it, in the same run-order the
    real suite uses, and it costs well under a second because it discovers without
    running. --no-venv because the interpreter has already been chosen HERE, by
    venv_python(), and a second hop would report a machine this run did not certify.

    A suite that cannot load reports itself as such rather than as zero checks: the
    difference between "this code was exercised and is fine" and "nothing about this
    code was exercised at all" is the whole reason that runner distinguishes them, and
    folding it away here would put the lie back one layer up.
    """
    suite = ROOT / "api" / "tests" / "run.py"
    if not suite.exists():
        return None
    try:
        out = subprocess.run([str(py or sys.executable), str(suite), "--list", "--no-venv"],
                             capture_output=True, text=True, timeout=120,
                             cwd=str(ROOT / "api"))
    except Exception:  # noqa: BLE001 - an interpreter that will not start is "cannot say"
        return None
    if out.returncode != 0:
        return None
    suites, checks, blocked = 0, 0, []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        suites += 1
        m = re.search(r"\b(\d+) checks$", line)
        if m:
            checks += int(m.group(1))
        else:                       # "<name>   cannot load here - needs <package>"
            blocked.append(line.split()[0])
    return (suites, checks, blocked) if suites else None


def api_tests(args) -> int:
    print("\n--- API TESTS ---")
    suite = ROOT / "api" / "tests" / "run.py"
    if not suite.exists():
        say(WARN, "api tests", "no api/tests/run.py in this checkout - nothing to run")
        return 0
    if not args.test:
        # BOTH numbers, and the condition on the second is the load-bearing part: a
        # count that silently omitted the suites which never loaded would be the very
        # lie that runner was rewritten to stop telling. Both now come out of the
        # runner itself - see api_suite_totals for why none of this is typed by hand
        # any more. "could not ask" is reported as could-not-ask and never as a
        # remembered number, for the same reason.
        totals = api_suite_totals(venv_python())
        if totals is None:
            say(WARN, "api tests", "python api/tests/run.py   "
                                   "(could not ask it how many checks it has; "
                                   "run it and read the total off the run)")
        elif totals[2]:
            say(WARN, "api tests", f"python api/tests/run.py   ({totals[0]} suites, "
                                   f"{totals[1]} checks loadable in this python; "
                                   f"{len(totals[2])} suite(s) cannot load here "
                                   f"({', '.join(totals[2])}) - see API CORE LIBRARIES above)")
        else:
            say(WARN, "api tests", f"python api/tests/run.py   ({totals[0]} suites, "
                                   f"{totals[1]} checks; or pass --test)")
        return 0
    # The venv interpreter whenever there is one (see venv_python): fastapi, pydantic and
    # httpx were installed INTO it, and the suite imports the API's own modules. Running
    # this under sys.executable is how `--test` came to fail with ModuleNotFoundError
    # immediately after `--dev` finished installing the very modules it could not find.
    # api/tests/run.py now hops to the same venv by itself; passing it explicitly keeps
    # this working if that ever gets a --no-venv in front of it.
    py = venv_python()
    if py is None:
        say(WARN, "interpreter", f"no venv - running under {sys.executable} "
                                 "(pass --dev to build one if the imports fail)")
    # cwd=api because the API's imports are rooted there (`from config import settings`),
    # the same way the README and install.sh run it.
    rc = call([str(py or sys.executable), str(suite)], cwd=str(ROOT / "api"))
    return 1 if rc else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dev", action="store_true", help="build the API virtualenv")
    ap.add_argument("--test", action="store_true", help="run the test suites")
    args = ap.parse_args()

    print("NEPTUNE bootstrap")
    print(f"repo: {ROOT}")
    print(f"host: {platform.system()} {platform.release()}")
    print("\n--- COMMON ---")
    missing = check_common()
    missing += topside(args)
    missing += vehicle(args)
    missing += hardware_deps()
    missing += core_deps()
    missing += api_tests(args)

    print()
    if missing:
        print(f"{missing} thing(s) need attention above.")
    else:
        print("Everything this machine needs is present.")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
