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

    A third way to contribute nothing is quieter still: a test_*.py that discovery
    finds NO checks in — a class that stopped inheriting TestCase, a rename that took
    `test_` off the front of every method. unittest says nothing at all about that
    file, so the suite simply stops appearing and the count gets smaller. The files on
    disk are therefore the roll call, and a file discovery skipped is reported NONE.

COVERAGE IS A MEASUREMENT, NOT A GATE
    `--coverage` adds one table: how much of api/ this run actually executed, per file,
    lines AND branches. It changes nothing else — the same suites run, the same checks
    pass or fail, the same exit code comes out — because a measuring instrument that
    alters the thing it measures is not one.

    coverage.py is a DEV-ONLY tool and is deliberately NOT in api/requirements.txt.
    install.sh builds the Pi's venv from that file, so every line in it is something a
    Raspberry Pi 3B+ fetches over a canal-side hotspot before the vehicle can serve; the
    vehicle does not run tests and has no use for a tool that watches them. The flag
    therefore has to survive the tool being absent, and it does: the run proceeds
    untouched and the missing instrument is NAMED. Absent is not zero. Printing 0% for a
    measurement that was never taken is the same lie as a depth of 0.0 from a sensor that
    is not answering, and this runner is the last place that lie should appear.

USAGE
    python api/tests/run.py              # every suite, from the repo root
    python tests/run.py                  # the same, from api/
    python api/tests/run.py replay       # one suite (substring match)
    python api/tests/run.py --list
    python api/tests/run.py -v           # name every check, not just the failures
    python api/tests/run.py --no-venv    # stay in THIS python, do not hop to the venv
    python api/tests/run.py --coverage   # also print the line/branch table for api/

    Exit status:
        0   every check ran and passed
        1   a check failed
        2   nothing failed, but something could not be RUN — a missing dependency, or
            a suite file with no checks in it. Not success. Non-zero on purpose, so a
            pre-push gate stops either way.

    Coverage never touches those three. It is a report about the run, not a verdict on
    it; there is no threshold here and no --fail-under, because a number that gates a
    push is a number people learn to move rather than earn.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import platform
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
    """Where python lives inside `venv` — Scripts on Windows, bin everywhere else.

    Both names are tried on POSIX. `bin/python` is a symlink the venv module creates,
    and on a Raspberry Pi it is also the first thing to break: a distro upgrade that
    moves /usr/bin/python3 leaves that link dangling while `bin/python3` still resolves,
    and `.exists()` on a dangling symlink is False — so the runner would decide the
    checkout has no venv and quietly test a python that has none of the api's packages.
    The first name that exists wins; if none does, the canonical one is returned so the
    caller's own .exists() check reads False, which is the honest answer.
    """
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    for name in ("python", "python3"):
        py = venv / "bin" / name
        if py.exists():
            return py
    return venv / "bin" / "python"


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


# ---------------------------------------------------------------- coverage (optional)
#
# Everything below is inert unless --coverage is passed AND coverage.py is installed.
# Nothing above imports it, nothing on the Pi ever will, and the runner's behaviour with
# the flag absent is byte-for-byte what it was before this section existed.

# The columns, once, so the header, the rows and the TOTAL line cannot drift apart.
_COV_ROW = "  {:<24}{:>7}{:>7}{:>8}{:>8}{:>8}{:>9}"
_COV_RULE = "  " + "-" * 71


def _start_coverage():
    """Begin measuring api/. Returns (coverage object, []) or (None, lines to print).

    STARTED BEFORE unittest DISCOVERY, on purpose. Discovery imports every test module,
    which imports the api modules under test, and the module-level statements in those
    files — the imports, the constants, the dataclass bodies, every FastAPI route
    decorator in nav/service.py — execute exactly once, right there. An instrument
    switched on after discovery reports all of that as never executed, which is not a
    gap in the tests; it is the instrument being late. The cost is that the flag has to
    be handled before the run knows which suites it is going to run, which is why this
    returns its own explanation rather than printing one: main() decides where it goes.

    NEVER RAISES. A bench with a half-installed coverage must still be able to run its
    tests, so a tool that will not start is reported as a tool that will not start.
    """
    try:
        import coverage
    except ImportError:
        return None, [
            "cover  : NOT INSTALLED - this run cannot say how much of api/ it exercised,",
            "         and a measurement nobody took is not a measurement of zero, so no",
            "         figure is printed. coverage is a DEV-ONLY tool, deliberately absent",
            "         from api/requirements.txt: install.sh builds the Pi's venv from that",
            "         file, and the vehicle does not run tests.",
            f"             {sys.executable} -m pip install coverage",
            "         Every suite below runs exactly as it would without --coverage, and",
            "         the exit code is the one the checks earn.",
        ]
    try:
        cov = coverage.Coverage(
            # NO .coverage FILE. This is a report printed once, not an artifact; a test
            # runner that leaves state in the tree is a test runner that gets .gitignore
            # entries and then gets stale. The data lives in memory for the one report.
            data_file=None,
            branch=True,
            # config_file=False so what this table means is decided HERE and nowhere
            # else. There is no .coveragerc and no pyproject.toml in this repo today;
            # if one appears later it must not be able to change these numbers without
            # a line of this file changing too.
            config_file=False,
            # source=api/ rather than "whatever happened to get imported". With an
            # explicit source tree coverage lists the files NO suite ever imported, at
            # 0%, instead of leaving them out — and a module nothing tested IS the
            # finding. A table that silently omits it reports the tested half of the api
            # and calls it the api, which is the same shape of lie as a suite vanishing
            # from the count (see the module docstring).
            source=[str(API)],
            # The tests are not the subject. api/.venv is not ours at all.
            omit=[str(HERE / "*"), str(API / ".venv" / "*")],
        )
        cov.start()
    except Exception as exc:  # noqa: BLE001 - a broken install must not take the run down
        return None, [
            f"cover  : installed, but would not start ({exc.__class__.__name__}: {exc})",
            "         no figures are printed rather than partial ones. Every suite below",
            "         runs exactly as it would without --coverage.",
        ]
    return cov, []


def _pct(covered: int, total: int) -> str:
    """A percentage that never rounds itself into a claim it cannot support.

    0% is reserved for "nothing was covered" and 100% for "nothing was missed"; anything
    between is clamped to 1..99 before rounding. A file with one missed branch out of
    four hundred is not 100% covered, and a table that rounds it there is exactly the
    reassuring-but-false instrument this runner was written to stop being.

    `-` where there is no denominator. A module with no branches in it has no branch
    coverage — not 0%, which reads as a gap somebody should close, and not 100%, which
    reads as work someone did. Same rule as the DEPS lines above: no number where there
    was no measurement.
    """
    if total <= 0:
        return "-"
    if covered <= 0:
        return "0%"
    if covered >= total:
        return "100%"
    return f"{min(max(100.0 * covered / total, 1.0), 99.0):.0f}%"


def _cov_name(key: str) -> str:
    """A file's row label: its path relative to api/, in forward slashes.

    coverage reports paths relative to the CURRENT DIRECTORY, and this runner is
    documented as runnable from both the repo root and api/ — so the same file would be
    called `api\\nav\\geo.py` in one place and `nav/geo.py` in the other, and two runs of
    the same command would produce tables that cannot be diffed against each other.
    """
    try:
        return Path(key).resolve().relative_to(API).as_posix()
    except (ValueError, OSError):
        return key.replace("\\", "/")


def _coverage_lines(cov) -> list[str]:
    """The per-file line/branch table, as lines ready to print. Never raises.

    The numbers come out of coverage's own JSON report — its public API, rendered to
    stdout and caught here — rather than being recomputed from the raw data, because the
    arithmetic of a partial branch is coverage's business and a second implementation of
    it in this file would eventually disagree with the tool and be believed anyway.
    """
    try:
        import coverage
        buf = io.StringIO()
        # "-" means "write it to stdout"; stdout is this buffer for the duration.
        with contextlib.redirect_stdout(buf):
            cov.json_report(outfile="-")
        data = json.loads(buf.getvalue())
        files = data["files"]
        totals = data["totals"]
    except Exception as exc:  # noqa: BLE001 - a report that will not render is not a crash
        return ["", f"cover  : measured, but the report would not render "
                    f"({exc.__class__.__name__}: {exc}); no figures rather than wrong ones"]

    def row(label, s):
        return _COV_ROW.format(
            label, s["num_statements"], s["missing_lines"],
            _pct(s["covered_lines"], s["num_statements"]),
            s["num_branches"], s["missing_branches"],
            _pct(s["covered_branches"], s["num_branches"]))

    out = ["", f"coverage of api/, api/tests/ excluded  "
               f"(coverage {getattr(coverage, '__version__', '?')}, branch mode)",
           _COV_ROW.format("file", "stmts", "miss", "line%", "branch", "brmiss", "branch%"),
           _COV_RULE]
    out += [row(_cov_name(k), files[k]["summary"]) for k in sorted(files, key=_cov_name)]
    out += [_COV_RULE, row("TOTAL", totals)]
    out.append("  A file at 0% is a file this run never imported, not a file that is "
               "missing.")
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description="Run the Neptune api tests.")
    ap.add_argument("suites", nargs="*", help="substring(s) of suite names; default: all")
    ap.add_argument("--list", action="store_true", help="list the suites and exit")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print every check, not just the failures")
    ap.add_argument("--no-venv", action="store_true",
                    help="run in this interpreter instead of hopping to the repo venv")
    ap.add_argument("--coverage", action="store_true",
                    help="also print which lines and branches of api/ this run executed "
                         "(needs the dev-only coverage package; says so if it is absent)")
    args = ap.parse_args(argv)

    # Before anything is discovered: discovery itself is what fails when pydantic is
    # absent, so the interpreter has to be settled first or the run classifies the
    # wrong machine.
    if not args.no_venv:
        rc = _reexec_in_venv(argv)
        if rc is not None:
            return rc

    # AFTER the interpreter is settled and BEFORE anything is imported. The hop above
    # re-runs this whole file in the venv with the same argv, so measuring on this side
    # of it would measure a process that is about to hand the work to another one.
    # Skipped for --list, which imports the suites but never executes a check: measuring
    # it would report the api as almost entirely unexercised by a command nobody asked
    # to exercise it.
    cov, cov_absent = None, []
    if args.coverage and not args.list:
        cov, cov_absent = _start_coverage()

    # top_level_dir=api so the modules load as `tests.test_x`, i.e. the same package
    # path they have when anything else imports them.
    discovered = list(_flatten(
        unittest.TestLoader().discover(str(HERE), pattern="test_*.py", top_level_dir=str(API))))

    groups: dict[str, list] = {}
    for test in discovered:
        groups.setdefault(_suite_of(test), []).append(test)

    # A suite discovery walked straight past. unittest reports "this file contained no
    # tests" as SILENCE: a test_*.py whose class stopped inheriting from TestCase, or
    # whose methods no longer start with test_ after a rename, contributes nothing and
    # appears nowhere — the total just gets smaller and every line still says ok. That
    # is a suite vanishing from the count, which is the one thing this runner exists to
    # make impossible, so the files on disk are the roll call and discovery answers it.
    empty = {p.stem for p in HERE.glob("test_*.py") if p.stem not in groups}
    for name in empty:
        groups[name] = []

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
        if args.coverage:
            print("cover  : nothing to measure - --list discovers the suites without "
                  "running them\n")
        for n in names:
            if n in blocked:
                print(f"{_label(n):<24} cannot load here - needs {blocked[n]}")
            elif n in empty:
                print(f"{_label(n):<24} no checks discovered in {n}.py")
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
    empty = {n for n in empty if n in names}

    # The machine is part of the result. This suite's totals are quoted in four
    # documents and not one of them could say which box produced them - Pi or handheld,
    # 3.9 or 3.14 - which is exactly how "260/260 on the Ally" gets read as "260/260
    # everywhere". Printed before the run, so it is attached to whatever follows.
    print(f"machine: {platform.platform()}  ({sys.platform}, "
          f"{platform.machine() or 'unknown arch'})")
    print(f"python : {sys.version.split()[0]}  {sys.executable}")
    print(f"api    : {API}")
    print(f"suites : {len(names)}")
    # The instrument is part of the result too, and it is stated BEFORE the run for the
    # same reason the machine is: whichever of these two lines gets printed, it belongs
    # to the report below it and not to some other run somebody pasted next to it.
    if cov is not None:
        print("cover  : measuring api/ (lines and branches); api/tests/ excluded")
    for line in cov_absent:
        print(line)
    print()

    total = failed = skipped = 0
    dep_skipped = 0                       # skips whose reason is also a missing package
    missing: set[str] = set()
    cov_stop_error = None
    t0 = time.time()
    try:
        for name in names:
            if name in blocked:
                missing.add(blocked[name])
                # No count in the count column, on purpose. This project's rule for a signal
                # whose sensor is absent is to show CANNOT-TELL and never a plausible
                # number; a suite whose module never loaded is the same situation, and a
                # "0/1" here would be a number where there is no measurement.
                print(f"  {_label(name):<24} DEPS {'-':>3}/-   never loaded: "
                      f"needs {blocked[name]}")
                continue
            if name in empty:
                # Same rule as DEPS, different cause: no count where there was no
                # measurement. "0/0" would read as a suite that ran and had nothing to say.
                print(f"  {_label(name):<24} NONE {'-':>3}/-   no checks discovered in "
                      f"{name}.py")
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
    finally:
        # The tracer comes off whatever happens, including the way out of an exception
        # that escaped a suite entirely. It cannot change the outcome - it swallows
        # nothing, re-raises nothing, and records its own failure instead of raising a
        # second one over the top of the first, which would replace a real finding about
        # the api with a complaint about the measuring equipment.
        if cov is not None:
            try:
                cov.stop()
            except Exception as exc:  # noqa: BLE001
                cov_stop_error = f"{exc.__class__.__name__}: {exc}"

    dt = time.time() - t0
    ran = len(names) - len(blocked) - len(empty)
    if blocked or empty:
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

    # BEFORE the verdict, never after it. The verdict is the line a person reads, and a
    # forty-row table pushed underneath it would bury the one sentence that says whether
    # this run means anything.
    if cov is not None:
        if cov_stop_error:
            print(f"\ncover  : the measurement could not be stopped cleanly "
                  f"({cov_stop_error});")
            print("         no table is printed rather than a partial one. The check "
                  "results above")
            print("         are unaffected - coverage watches the run, it does not "
                  "take part in it.")
        else:
            for line in _coverage_lines(cov):
                print(line)
            if blocked or dep_skipped or empty:
                # The two reports have to be read together or the table lies by
                # omission: a suite that never loaded exercised nothing, so every file
                # only that suite would have touched sits at 0% for a reason that has
                # nothing to do with what the api's tests actually cover.
                print("  These figures come from an INCOMPLETE run - see below. Code a "
                      "suite that")
                print("  never loaded would have exercised is counted as unexercised, "
                      "which it was")
                print("  here and need not be on a machine that has the dependencies.")

    # The verdict goes LAST because it is the line a person actually reads, and when
    # something could not be run it has to be the thing that contradicts the pass
    # count immediately above it rather than a footnote above it.
    if blocked or dep_skipped or empty:
        print("\nINCOMPLETE - this run certifies nothing about the api as a whole.")
        if blocked:
            print(f"  {len(blocked)} of {len(names)} suites never loaded: "
                  f"{', '.join(_label(n) for n in sorted(blocked))}")
        if empty:
            print(f"  {len(empty)} of {len(names)} suites contained no checks at all: "
                  f"{', '.join(_label(n) for n in sorted(empty))} - the file is there "
                  "and discovery found nothing in it to run")
        if dep_skipped:
            print(f"  {dep_skipped} check(s) inside a suite that did load were skipped "
                  "because a package they need is absent")
        if not missing:
            # An empty suite has no package to install and no command to suggest; the
            # rest of this block is about absent dependencies and would send the reader
            # off to pip for a file that needs editing.
            print(f"  in this python: {sys.executable}")
            return 1 if failed else 2
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
