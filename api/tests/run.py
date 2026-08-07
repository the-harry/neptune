#!/usr/bin/env python3
"""NEPTUNE api test runner — standard-library unittest, no framework.

Deliberately the same shape as client/tests/run.py: one line per suite, a total in
checks, and a zero exit only when everything ran and passed — so both halves of the
system are gated by the same kind of command and read the same way in a terminal.

NO PYTEST, ON PURPOSE. The api's dependency list is what has to be installed on a
Raspberry Pi 3B+ over a canal-side hotspot; a test framework that must be installed
before the tests can run is a test suite that quietly stops being run. `unittest` is
already there, on every machine, forever.

WHAT A "CHECK" IS
    One test method. The client suite counts individual assertions because a browser
    suite is a list of them; here the unit of work that can pass or fail on its own is
    the method, and reporting anything else would make the two totals lie about each
    other.

A SUITE THAT WOULD NOT IMPORT IS NOT A SUITE THAT FAILED
    unittest represents a module it could not import as one synthetic test that raises
    when run, so it arrives in the totals as "1 check failed" — arithmetically tidy and
    completely wrong. A failed check is a FINDING: the code was exercised and came out
    the wrong shape. A suite that never loaded is an ABSENCE of findings: nothing about
    that code was exercised at all, and no number of green checks elsewhere makes up for
    it. On a clean checkout of this bench that distinction covered half the suites —
    replay and telemetry both die on `No module named 'pydantic'` — and the run still
    printed "100/105 checks passed", which is exactly the reassuring-but-false report
    this project refuses to accept from its instruments. Those suites are now marked
    DEPS, counted separately, named in the verdict, and cannot be read as a pass.

USAGE
    python api/tests/run.py              # every suite, from the repo root
    python tests/run.py                  # the same, from api/
    python api/tests/run.py replay       # one suite (substring match)
    python api/tests/run.py --list
    python api/tests/run.py -v           # name every check, not just the failures
    python api/tests/run.py --no-venv    # stay in THIS python, do not hop to the venv

    Exit status:
        0   every check ran and passed
        1   a check failed
        2   nothing failed, but something could not be RUN (a missing dependency).
            Not success. Non-zero on purpose, so a pre-push gate stops either way.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = HERE.parent
ROOT = API.parent

# api/ is the python root for this project — the code under test does `from config
# import settings` and `from nav.x import y`, and the tests must load it exactly the
# way the server does or they are testing a different arrangement of the same files.
# Index 0 and not append: a stray `config.py` further up the path would otherwise win.
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

# The two venvs this repo can have, in the order they are believed — the same order,
# for the same reason, as bootstrap.py's venv_python(): `bootstrap.py --dev` builds
# ROOT/.venv on a dev box, install.sh builds api/.venv on the Pi and runs uvicorn from
# it. If the two files ever disagree about which interpreter owns the api, one of them
# is testing a machine the other never configured.
VENVS = (ROOT / ".venv", API / ".venv")
# Set in the child so a venv whose interpreter still cannot import the deps re-runs the
# suites once and reports honestly, instead of spawning itself forever.
REEXEC_GUARD = "NEPTUNE_TESTS_IN_VENV"


def _flatten(suite):
    """unittest hands back suites of suites; the runner wants the leaves."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def _suite_of(test) -> str:
    """Which file a test came from, as a bare name ('test_replay')."""
    mod = type(test).__module__
    if mod.startswith("tests."):
        return mod.rsplit(".", 1)[-1]
    # A module that fails to IMPORT still arrives here as a test case — unittest wraps
    # the ImportError so a broken suite reports itself instead of silently vanishing
    # from the count. Its class lives in unittest.loader, so the name is only in the id.
    for part in reversed(test.id().split(".")):
        if part.startswith("test_"):
            return part
    return test.id()


def _label(name: str) -> str:
    return name[5:] if name.startswith("test_") else name


_NO_MODULE = re.compile(r"No module named '([\w.]+)'")


def _missing_dependency(text: str) -> str | None:
    """The name of the third-party package a blob of error text is complaining about.

    A blob, and not an exception, because neither place we need this hands over a clean
    one: unittest wraps an un-importable suite in ImportError(<the entire formatted
    traceback>), and test_filters builds its skip reason by interpolating str(exc).

    The `not ours` test is what makes this a DEPENDENCY report rather than a guess.
    `No module named 'nav.estimator'` means a file is missing from this checkout — a
    real breakage, and calling it "install something" would send a newcomer off to pip
    for a bug that lives in the repo. Only a top-level name that is not a module of the
    api's own tree gets blamed on the environment.
    """
    m = _NO_MODULE.search(text or "")
    if not m:
        return None
    top = m.group(1).split(".")[0]
    if (API / f"{top}.py").exists() or (API / top / "__init__.py").exists():
        return None
    return top


def _detail(tb: str, limit: int = 25) -> list[str]:
    """The lines of a failure worth printing.

    The whole traceback, minus its first line, capped. NOT just the last line: an
    acceptance test's assertion message carries the measured numbers, and those numbers
    are the finding — truncating them turns "the filter lost by 3.2 m" into "a test
    failed", which is the difference between a report and a shrug.
    """
    lines = [ln for ln in tb.strip().splitlines() if ln.strip()]
    if lines and lines[0].startswith("Traceback"):
        lines = lines[1:]
    if len(lines) > limit:
        lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
    return lines


def _interpreter_in(venv: Path) -> Path:
    """Where python lives inside `venv` — Scripts on Windows, bin everywhere else."""
    return venv / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")


def _repo_venv_python() -> Path | None:
    """The repo venv's interpreter, if this checkout has one and we are not already it."""
    try:
        here = Path(sys.executable).resolve()
    except OSError:  # pragma: no cover — no sys.executable is possible under embedding
        here = None
    for py in map(_interpreter_in, VENVS):
        if py.exists() and py.resolve() != here:
            return py
    return None


def _reexec_in_venv(argv: list[str]) -> int | None:
    """Re-run this suite under the repo venv. Returns its exit code, or None to stay put.

    WHY THIS EXISTS. The suites import the api's own modules and those import pydantic,
    but `python tests/run.py` is typed with whatever python is on PATH — normally the
    bare system one, which has never had api/requirements.txt installed. So a bench that
    `bootstrap.py --dev` had just finished setting up correctly still reported half its
    suites dead on ModuleNotFoundError, and every session since has worked around it by
    building a throwaway venv by hand. The interpreter that owns the api is a fact of
    the checkout, not something a person should have to remember to type.

    It announces the hop, and --no-venv turns it off, because a runner that silently
    swaps the interpreter out from under you is its own kind of dishonesty.
    """
    if os.environ.get(REEXEC_GUARD):
        return None
    py = _repo_venv_python()
    if py is None:
        return None
    print(f"using the repo venv : {py}")
    print(f"  started from        : {sys.executable}")
    print("  --no-venv to stay here\n")
    # Flush before handing stdout to the child. Into a pipe or a file this stream is
    # block-buffered, so without this the notice about which interpreter is running
    # drains after the child's report has already been written and the reader sees a
    # run header naming one python and a footer announcing a different one.
    sys.stdout.flush()
    env = dict(os.environ, **{REEXEC_GUARD: "1"})
    try:
        return subprocess.call([str(py), str(Path(__file__).resolve()), *argv], env=env)
    except OSError as exc:  # the venv is there but its python will not start
        print(f"  could not start it ({exc}); carrying on in this python\n")
        return None


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description="Run the Neptune api tests.")
    ap.add_argument("suites", nargs="*", help="substring(s) of suite names; default: all")
    ap.add_argument("--list", action="store_true", help="list the suites and exit")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print every check, not just the failures")
    ap.add_argument("--no-venv", action="store_true",
                    help="run in this interpreter instead of hopping to the repo venv")
    args = ap.parse_args(argv)

    # Before anything is discovered: discovery itself is what fails when pydantic is
    # absent, so the interpreter has to be settled first or the run classifies the
    # wrong machine.
    if not args.no_venv:
        rc = _reexec_in_venv(argv)
        if rc is not None:
            return rc

    # top_level_dir=api so the modules load as `tests.test_x`, i.e. the same package
    # path they have when anything else imports them.
    discovered = list(_flatten(
        unittest.TestLoader().discover(str(HERE), pattern="test_*.py", top_level_dir=str(API))))

    groups: dict[str, list] = {}
    for test in discovered:
        groups.setdefault(_suite_of(test), []).append(test)

    # Sort the suites that never loaded out of the ones that merely failed, BEFORE
    # anything counts or prints them. See the module docstring: unittest gives both the
    # same shape, and only the wrapped exception tells them apart.
    blocked: dict[str, str] = {}          # suite name -> the package it needed
    for name, tests in groups.items():
        if len(tests) == 1 and type(tests[0]).__name__ == "_FailedTest":
            need = _missing_dependency(str(getattr(tests[0], "_exception", "")))
            if need:
                blocked[name] = need

    names = sorted(groups)
    if args.list:
        for n in names:
            if n in blocked:
                print(f"{_label(n):<24} cannot load here - needs {blocked[n]}")
            else:
                print(f"{_label(n):<24} {len(groups[n])} checks")
        return 0
    if args.suites:
        names = [n for n in names if any(w.lower() in n.lower() for w in args.suites)]
        if not names:
            sys.exit(f"no suite matches {args.suites}; try --list")
    if not names:
        sys.exit(f"no test_*.py found in {HERE}")
    # Narrow to what is actually about to run. Classification above deliberately covers
    # every discovered suite so --list can flag the ones that will not load, but the
    # verdict must only ever speak about this run: `run.py hardware` once reported
    # "2 of 1 suites never loaded", indicting suites nobody had asked for.
    blocked = {n: need for n, need in blocked.items() if n in names}

    print(f"python : {sys.version.split()[0]}  {sys.executable}")
    print(f"api    : {API}")
    print(f"suites : {len(names)}\n")

    total = failed = skipped = 0
    dep_skipped = 0                       # skips whose reason is also a missing package
    missing: set[str] = set()
    t0 = time.time()
    for name in names:
        if name in blocked:
            missing.add(blocked[name])
            # No count in the count column, on purpose. This project's rule for a signal
            # whose sensor is absent is to show CANNOT-TELL and never a plausible
            # number; a suite whose module never loaded is the same situation, and a
            # "0/1" here would be a number where there is no measurement.
            print(f"  {_label(name):<24} DEPS {'-':>3}/-   never loaded: needs {blocked[name]}")
            continue
        suite = unittest.TestSuite(groups[name])
        # The suite's own stdout goes nowhere: this runner prints the report, and a
        # dotted progress line interleaved with it makes both harder to read.
        res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        bad = res.failures + res.errors
        total += res.testsRun
        failed += len(bad)
        skipped += len(res.skipped)
        mark = "ok  " if not bad else "FAIL"
        # A skipped check is NOT a passed check, so it is subtracted from the count and
        # named. unittest's testsRun includes skips, and a runner that quietly folded
        # them into the pass total would report a suite that verified nothing as green.
        passed = res.testsRun - len(bad) - len(res.skipped)
        # A suite can also lose individual checks to the same missing package without
        # dying outright - test_filters skips its two estimator cases rather than take
        # the whole module down, so the pure filter maths stays checkable on a bare
        # bench. Those skips belong in the verdict with the blocked suites, not in the
        # ordinary "skipped" bucket, because they share one cause and one cure.
        dep_reasons = [d for _t, why in res.skipped if (d := _missing_dependency(why))]
        dep_skipped += len(dep_reasons)
        deps_here = set(dep_reasons)
        missing |= deps_here
        note = ""
        if res.skipped:
            note = (f"  ({len(res.skipped)} skipped: needs {', '.join(sorted(deps_here))})"
                    if deps_here else f"  ({len(res.skipped)} skipped)")
        print(f"  {_label(name):<24} {mark} {passed:>3}/{res.testsRun}{note}")
        if args.verbose:
            bad_ids = {t.id() for t, _ in bad}
            for test in groups[name]:
                if test.id() not in bad_ids:
                    print(f"      pass  {test.id().rsplit('.', 1)[-1]}")
        for test, tb in bad:
            print(f"      FAIL  {test.id().rsplit('.', 1)[-1]}")
            for line in _detail(tb):
                print(f"            {line}")
        for test, why in res.skipped:
            print(f"      skip  {test.id().rsplit('.', 1)[-1]}  ({why})")

    dt = time.time() - t0
    ran = len(names) - len(blocked)
    if blocked:
        print(f"\n{total - failed - skipped}/{total} checks passed in {dt:.0f}s "
              f"across {ran} of {len(names)} suites")
    else:
        print(f"\n{total - failed - skipped}/{total} checks passed in {dt:.0f}s across "
              f"{len(names)} suite{'' if len(names) == 1 else 's'}")
    # Only the skips nobody can act on are counted here; the ones caused by a missing
    # package are attributed, with their cause, in the verdict below. Printing both
    # totals would make three skipped checks look like six.
    if skipped - dep_skipped:
        print(f"{skipped - dep_skipped} skipped - not run, and not counted as passed")

    # The verdict goes LAST because it is the line a person actually reads, and when
    # something could not be run it has to be the thing that contradicts the pass
    # count immediately above it rather than a footnote above it.
    if blocked or dep_skipped:
        print("\nINCOMPLETE - this run certifies nothing about the api as a whole.")
        if blocked:
            print(f"  {len(blocked)} of {len(names)} suites never loaded: "
                  f"{', '.join(_label(n) for n in sorted(blocked))}")
        if dep_skipped:
            print(f"  {dep_skipped} check(s) inside a suite that did load were skipped "
                  "because a package they need is absent")
        print(f"  not installed: {', '.join(sorted(missing))}")
        print(f"  in this python: {sys.executable}")
        # Named as CORE deps explicitly. api/requirements.txt also lists gpiozero,
        # smbus2 and the BNO085 driver, which are Pi-only and are SUPPOSED to be absent
        # here — without that sentence a newcomer reads "install the requirements" and
        # tries to pip a board-detection stack onto a Windows laptop, where it does not
        # build, which is how this ends in an afternoon lost to the wrong problem.
        print("\n  These are core api dependencies from api/requirements.txt, not the")
        print("  Pi-only hardware ones (which are meant to be missing here). One")
        print("  command installs them into the repo venv, creating it if needed:")
        print("      python bootstrap.py --dev")
        print(f"  This runner then finds {ROOT / '.venv'} and re-runs")
        print("  itself under it, with nothing more to remember.")
        return 1 if failed else 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
