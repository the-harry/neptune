#!/usr/bin/env python3
"""NEPTUNE bootstrap — take a fresh machine to a working state, and say what is missing.

    python bootstrap.py            # check everything, change nothing
    python bootstrap.py --dev      # also build the API venv (topside development)
    python bootstrap.py --test     # also run the client test suite

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
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OK, WARN, BAD = "  ok  ", " note ", " MISS "


def say(mark, what, detail=""):
    print(f"[{mark}] {what}" + (f"   {detail}" if detail else ""))


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
        rc = subprocess.call([sys.executable, str(ROOT / "client" / "tests" / "run.py")])
        missing += (1 if rc else 0)
    else:
        say(WARN, "tests", "python client/tests/run.py   (214 checks, ~90 s)")
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
    venv = ROOT / ".venv"
    py = venv / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    if not py.exists():
        say(WARN, "creating venv", str(venv))
        if subprocess.call([sys.executable, "-m", "venv", str(venv)]) != 0:
            say(BAD, "venv", "could not create it")
            return 1
    req = ROOT / "api" / "requirements.txt"
    say(WARN, "installing", str(req))
    if subprocess.call([str(py), "-m", "pip", "install", "-q", "-r", str(req)]) != 0:
        say(BAD, "pip", "install failed")
        return 1
    say(OK, "api venv", str(venv))
    print("\n  Run the API against the bench simulator (no hardware needed):")
    print(f"    cd api && NEPTUNE_HW=mock {py} -m uvicorn main:app --port 8000")
    print("  Then point the client at it:  ?host=127.0.0.1:8000")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dev", action="store_true", help="build the API virtualenv")
    ap.add_argument("--test", action="store_true", help="run the client test suite")
    args = ap.parse_args()

    print("NEPTUNE bootstrap")
    print(f"repo: {ROOT}")
    print(f"host: {platform.system()} {platform.release()}")
    print("\n--- COMMON ---")
    missing = check_common()
    missing += topside(args)
    missing += vehicle(args)

    print()
    if missing:
        print(f"{missing} thing(s) need attention above.")
    else:
        print("Everything this machine needs is present.")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
