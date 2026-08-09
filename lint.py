#!/usr/bin/env python3
"""NEPTUNE code checks — one command for the four desk tools, and an honest answer
when one of them is not installed.

    python lint.py                 every check, changes NOTHING on disk
    python lint.py --fix           let isort and black WRITE (formatting, not verdicts)
    python lint.py black mypy      substring match on the names below
    python lint.py --list          name the checks and exit

WHY ONE ENTRY POINT. Four tools with four command lines, four sets of flags and four
config files is four chances to run a different check from the one CI runs, which is how
a repo ends up with a green desk and a red pipeline and an argument about whose machine
is wrong. There is one command here, it reads the same pyproject.toml and setup.cfg the
editors read, and the CI job runs THIS FILE rather than a copy of its contents.

WHAT IT RUNS, in this order:

    isort         import order. First, because it moves whole lines and black then
                  decides how they wrap; the other way round is two passes.
    black         formatting. The line length lives in pyproject.toml, once.
    flake8        the lint proper. setup.cfg, because flake8 does not read pyproject.
    mypy          types over api/, light by default and STRICT AT THE PUBLIC
                  BOUNDARIES. The reasoning is written out in pyproject.toml.
    suppressions  this file's own check, and the only one that cannot be absent: every
                  `# type: ignore` carries an error code AND a written reason, and every
                  `# noqa` carries a code. Needs no tool, so it always runs.

WHAT IT RUNS OVER. isort, black and flake8 see every .py this repo owns: api/, the
scripts at the root, AND client/tests/ — the console is vanilla JS with no build step,
but its test harness is python and pretending otherwise would leave a thousand lines
unchecked while the report said the tree was clean. mypy sees api/ ONLY, which is where
the wire contract and the hardware contract live and where a wrong type reaches an
operator.

NONE OF THIS IS A PI RUNTIME DEPENDENCY. install.sh builds the vehicle's venv from
api/requirements.txt, where the four tools are COMMENTED OUT with the reason beside them.
A formatter downloaded onto a Raspberry Pi 3B+ over a canal-side hotspot is a cost paid
by the machine that has the least to spare, for work that happens at a desk.

ABSENT IS NOT A PASS. That rule is the whole reason this file is longer than a shell
alias. A tool that is not installed is reported by name, with the command that installs
it, and the run ends INCOMPLETE with a non-zero exit — the same shape, and the same
sentence, as `api/tests/run.py --coverage` on a machine with no coverage package. A
checker that quietly skips what it cannot run and prints "ok" is the same instrument as
a depth readout showing 0.0 with the sensor unplugged.

    Exit status — deliberately the same three as api/tests/run.py and client/tests/run.py:
        0   every check ran and passed
        1   a check ran and found something
        2   nothing found anything, but something could not be RUN — a tool is absent,
            or it would not start. Not success. Non-zero so a pre-push gate stops either
            way, and so "we never checked" cannot be mistaken for "there was nothing to
            find".
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / "api" / "requirements.txt"

# The two venvs this repo can have, in the order they are believed. THE SAME ORDER, for
# the same reason, as bootstrap.py's venv_python() and api/tests/run.py's VENVS:
# `bootstrap.py --dev` builds ROOT/.venv on a dev box, install.sh builds api/.venv on the
# Pi. This is the third copy of that fact and it is a copy on purpose — this file has to
# run on a checkout where nothing else has been imported — but if the three ever
# disagree about which interpreter owns the api, one of them is checking a machine the
# others never configured. mypy in particular is worthless in an interpreter that cannot
# import pydantic: every model becomes an unresolved import and the run reports a
# catastrophe that is really just the wrong python.
VENVS = (ROOT / ".venv", ROOT / "api" / ".venv")

# What each check IS, what it needs, and how to ask it. Fields:
#   name      what it is called on the report line and on the command line
#   module    what to run with `-m`, and what to probe for to find out if it is here
#   dist      the pip distribution name (same as module for all four today, but they
#             are different kinds of name and conflating them is how `pip install PIL`
#             gets suggested)
#   check     argv after `-m module` for a read-only run
#   fix       argv for --fix, or None where the tool has no opinion it can act on
#   pattern   how to recognise ONE finding in that tool's output (see _count)
#   what      the sentence printed beside it
#
# Every tool is invoked as `python -m <module>`, never as a bare `black` on PATH. pip
# installs the console scripts into a Scripts/ or bin/ directory that is frequently NOT
# on PATH — this machine's pip says so out loud on every install — and a runner that
# depends on that is a runner that reports the tool absent on a machine that has it.
CHECKS = (
    {
        "name": "isort",
        "module": "isort",
        "dist": "isort",
        # No --diff. isort's diff of a whole tree buries every other tool's output, and
        # the fix is one flag away: `python lint.py --fix`.
        "check": ["--check-only", "."],
        "fix": ["."],
        "pattern": re.compile(r"^ERROR: .*(Imports are incorrectly sorted|would be)", re.M),
        "what": "import order (black-compatible profile, pyproject.toml)",
    },
    {
        "name": "black",
        "module": "black",
        "dist": "black",
        "check": ["--check", "."],
        "fix": ["."],
        "pattern": re.compile(r"^would reformat ", re.M),
        "what": "formatting at 120 columns (pyproject.toml)",
    },
    {
        "name": "flake8",
        "module": "flake8",
        "dist": "flake8",
        "check": [],
        "fix": None,
        "pattern": re.compile(r"^.+:\d+:\d+: [A-Z]+\d+ ", re.M),
        "what": "lint at 120 columns (setup.cfg - flake8 cannot read pyproject.toml)",
    },
    {
        "name": "mypy",
        "module": "mypy",
        "dist": "mypy",
        # --cache-dir OUT OF THE TREE. mypy's incremental cache is worth having - it is
        # the difference between forty seconds and four on the second run - but by
        # default it lands as .mypy_cache/ next to the code, and a checker that leaves
        # state in the repo is a checker that earns a .gitignore entry and then, one
        # inattentive `git add -A` later, gets committed. Same rule the coverage work
        # follows with data_file=None: the report is the output, the tree is not.
        "check": ["--cache-dir", str(Path(tempfile.gettempdir()) / "neptune-mypy-cache")],
        "fix": None,
        "pattern": re.compile(r"^.+:\d+: error: ", re.M),
        "what": "types over api/: light by default, strict at the public boundaries",
    },
)

# Which python the tools are run BY, and which files they are run OVER, are two different
# questions. This is the second: every .py this project owns, which is api/, the root
# scripts, AND client/tests/ - the console's test harness is python even though the
# console itself is not. Only genuine non-source is pruned, and the same set appears in
# pyproject.toml and setup.cfg, because a person running `black .` by hand must get the
# answer this file gets.
SUPPRESSION_SKIP = {".venv", ".git", ".mypy_cache", "__pycache__", "data"}

# The two suppression forms, matched against COMMENT TOKENS ONLY (see suppressions()).
#
# THE `#` IS PART OF THE PATTERN, and it is what makes this check about suppressions
# rather than about the word "noqa". Both mypy and flake8 trigger on a hash followed by
# the marker; a comment that MENTIONS one mid-sentence suppresses nothing and must not be
# reported as though it did - this file's own comments would be the first false positive,
# and a checker that cannot be written about is a checker people work around.
#
# Both bracket/colon groups are optional ON PURPOSE: a suppression written WITHOUT them
# is precisely the finding, so it has to match here before it can be named.
_TYPE_IGNORE = re.compile(r"#\s*type:\s*ignore(?P<codes>\[[^\]]*\])?(?P<rest>.*)$")
_NOQA = re.compile(r"#\s*noqa(?P<sep>:)?(?P<rest>.*)$", re.I)
# A code is a letter run and a number: E501, F401, BLE001, PLC0415.
_IS_CODE = re.compile(r"^[A-Z]+\d+")
# A reason is prose. A stray dash, colon or bracket is punctuation somebody left behind.
_HAS_PROSE = re.compile(r"[A-Za-z]{3}")


def _interpreter_in(venv: Path) -> Path:
    """Where python lives inside `venv` — Scripts on Windows, bin everywhere else.

    Both POSIX names are tried, and in this order, for the reason api/tests/run.py
    documents at length: `bin/python` is a symlink, on a Pi it is the first thing a
    distro upgrade breaks, and `.exists()` on a dangling symlink is False.
    """
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    for name in ("python", "python3"):
        py = venv / "bin" / name
        if py.exists():
            return py
    return venv / "bin" / "python"


def interpreter() -> Path:
    """The python the tools run under: the repo's venv if it has one, else this one.

    NOT sys.executable by default. mypy has to import pydantic and fastapi to know what
    a Telemetry model is, and `python lint.py` is typed with whatever python is on PATH —
    usually the bare system one, which has never had api/requirements.txt installed. Under
    that interpreter every model on the wire boundary degenerates into an unresolved
    import and the report describes a broken repo that is really a mis-chosen python.

    This does NOT re-exec: the tools are subprocesses either way, so the interpreter is
    chosen once, here, and printed at the top of the run so nobody has to guess which
    machine the verdict came from.
    """
    for venv in VENVS:
        py = _interpreter_in(venv)
        if py.exists():
            return py
    return Path(sys.executable)


def pinned() -> tuple[dict[str, str], str | None]:
    """The pinned versions, READ FROM api/requirements.txt rather than typed here.

    Returns ({tool: "black==26.5.1"}, None) or ({}, why not).

    ONE SOURCE OF TRUTH, ASKED RATHER THAN COPIED. The dev-tooling block in
    api/requirements.txt is commented out — it has to be, because install.sh builds the
    Pi's venv from that file — so nothing can `pip install -r` it, and the versions would
    otherwise have to be written out again in this file and again in the CI workflow.
    Three copies of a version number is three chances for the desk and the pipeline to
    format the same file differently and blame each other. So this parses the commented
    pins out of the one file that has them, and the install command it prints is the
    command that file documents.

    Degrades the way everything else here does: if the block cannot be read, say so and
    fall back to unpinned names, rather than inventing versions or dying.
    """
    names = {c["name"] for c in CHECKS}
    try:
        text = REQUIREMENTS.read_text(encoding="utf-8", errors="replace")
    # No noqa here, and none below: OSError is a named exception, not a blind `except
    # Exception`, so there is nothing for a suppression to suppress. A marker that
    # silences nothing is a marker that teaches the next reader to add one by habit.
    except OSError as exc:
        return {}, f"{REQUIREMENTS.name} could not be read ({exc.__class__.__name__})"
    found = {}
    for line in text.splitlines():
        m = re.match(r"^#\s*([A-Za-z0-9_.-]+)==([^\s#]+)\s*$", line.strip())
        if m and m.group(1) in names:
            found[m.group(1)] = f"{m.group(1)}=={m.group(2)}"
    missing = sorted(names - set(found))
    if missing:
        return found, ("no pinned version in api/requirements.txt for: " + ", ".join(missing))
    return found, None


def probe(py: Path, checks) -> dict[str, str | None]:
    """{tool: version} for what is installed under `py`, None for what is not.

    ONE subprocess for all of them, and it imports NOTHING: importlib.metadata reads the
    installed distribution's metadata off disk, so probing mypy costs no more than
    probing isort. The alternative — importing each tool to read its __version__ — is
    seconds of startup to answer a question that is really about a directory listing.

    Probed under the interpreter the tools will actually RUN under, never this one. A
    machine whose venv has black and whose system python does not would otherwise be told
    to install something it already has.
    """
    script = (
        "import importlib.util as u\n"
        "from importlib.metadata import version, PackageNotFoundError\n"
        "import sys\n"
        "for spec in sys.argv[1:]:\n"
        "    mod, dist = spec.split(':')\n"
        "    if u.find_spec(mod) is None:\n"
        "        print(mod + ' -')\n"
        "        continue\n"
        "    try:\n"
        "        print(mod + ' ' + version(dist))\n"
        "    except PackageNotFoundError:\n"
        "        print(mod + ' ?')\n"
    )
    args = [f"{c['module']}:{c['dist']}" for c in checks]
    out = {c["name"]: None for c in checks}
    try:
        done = subprocess.run([str(py), "-c", script, *args], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        # An interpreter that will not start cannot be asked. Everything reads absent,
        # which is what it is from here, and the run says which python it asked.
        return out
    by_module = {c["module"]: c["name"] for c in checks}
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in by_module:
            out[by_module[parts[0]]] = None if parts[1] == "-" else parts[1]
    return out


def _count(pattern: re.Pattern, text: str) -> int:
    return len(pattern.findall(text))


def run_tool(py: Path, check, fix: bool) -> tuple[str, int, str]:
    """Run one tool. Returns (verdict, findings, output).

    verdict is "ok", "found", or "broke" — the third being a tool that IS installed and
    exited in a way its own output does not explain. That case is kept separate from
    "found" because a crashed linter has judged nothing, and folding it into a failure
    count would make an unusable tool look like a tool with an opinion.
    """
    cmd = [str(py), "-m", check["module"]] + list(check["fix"] if fix and check["fix"] else check["check"])
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        # A tool that will not start has not judged anything, so it is reported as a tool
        # that will not start - never folded into a finding count.
        return "broke", 0, f"{check['name']} would not start: {exc.__class__.__name__}: {exc}"
    text = (done.stdout or "") + (done.stderr or "")
    if fix and check["fix"]:
        # In --fix mode the tools REWRITE rather than report, and a rewrite is not a
        # finding: black exits 0 having changed fifty files. The count is meaningless
        # here, so it is not printed as one.
        return ("ok" if done.returncode == 0 else "broke"), 0, text
    found = _count(check["pattern"], text)
    if done.returncode == 0:
        # Zero exit and findings counted is the tool and the pattern disagreeing. Believe
        # the tool's exit code about pass/fail, and show the output so a stale pattern
        # here cannot silently swallow real findings.
        return "ok", 0, text
    if found:
        return "found", found, text
    return "broke", 0, text


def suppressions() -> tuple[list[str], int]:
    """Every suppression in this project's python carries a code, and an ignore carries
    a reason. Returns (findings as printable lines, how many suppressions were inspected).

    THE ONE CHECK THAT CANNOT BE ABSENT. It needs no package, so `python lint.py` on a
    bare machine still enforces this rule while it is reporting that the other four could
    not run. That matters here more than it looks: a suppression is how a check gets
    turned off for one line, and a rule about turning checks off is worth least on
    exactly the machine that has no checks installed.

    THE RULE.
        a type-ignore   must carry an error code in brackets AND prose saying why.
        a noqa          must carry a code at minimum.

    A BARE SUPPRESSION SILENCES THE FUTURE. A type-ignore with no code does not silence
    the error someone was looking at; it silences every error that line will ever
    produce, including the one nobody has written yet. That is the same failure as a
    latched reading that keeps showing the last good number: the line goes quiet and
    stays quiet, and quiet reads as fine.

    WHY THE TWO BARS DIFFER, said out loud so it does not look like an oversight. The
    prose requirement is the rule this repo set for mypy ignores, and there are none in
    the tree to grandfather, so it costs nothing to enforce from the first line. The
    noqa marker is older here and widespread, and many of them carry a code with no
    sentence after it; demanding prose would report a mass of findings in files this
    commit is not allowed to edit, and a report like that is one the next reader learns
    to scroll past. The code requirement is real, enforced, and currently met — raising
    the noqa bar later is a deliberate edit to this function, never a silent drift.

    COMMENT TOKENS ONLY, VIA tokenize. Searching raw lines would report this very
    docstring, and every other place the repo EXPLAINS a suppression rather than uses
    one — documentation and code look identical to a regex and are not the same thing at
    all. The stdlib tokenizer knows which is which, and it costs one pass per file.
    """
    bad: list[str] = []
    inspected = 0
    for path in sorted(_python_files()):
        rel = path.relative_to(ROOT).as_posix()
        try:
            with open(path, "rb") as fh:
                comments = [
                    (tok.start[0], tok.string) for tok in tokenize.tokenize(fh.readline) if tok.type == tokenize.COMMENT
                ]
        except (OSError, SyntaxError, tokenize.TokenError, UnicodeDecodeError) as exc:
            # A file that will not tokenize is NOT silently skipped. It is python this
            # check was asked to read and could not, which is a finding of its own -
            # black and flake8 will have their own opinion of it a moment later.
            bad.append(f"  {rel}: could not be read as python ({exc.__class__.__name__})")
            continue
        for line_no, comment in comments:
            m = _TYPE_IGNORE.search(comment)
            if m:
                inspected += 1
                codes = (m.group("codes") or "").strip("[]").strip()
                if not codes:
                    bad.append(
                        f"  {rel}:{line_no}: type-ignore with no error code - it silences "
                        f"whatever lands on this line next, too"
                    )
                elif not _HAS_PROSE.search(m.group("rest")):
                    bad.append(f"  {rel}:{line_no}: type-ignore[{codes}] with no reason written beside it")
            m = _NOQA.search(comment)
            if m:
                inspected += 1
                codes = m.group("rest").strip() if m.group("sep") else ""
                if not _IS_CODE.match(codes):
                    bad.append(f"  {rel}:{line_no}: bare noqa - name the code it is silencing")
    return bad, inspected


def _python_files():
    """Every .py this project owns, pruning as it walks rather than filtering after.

    os.walk with in-place pruning of `dirnames`, not rglob: data/ on a handheld that has
    downloaded a few offline areas is tens of thousands of tiles, and rglob would open
    every directory in it to find the .py files that are not there.
    """
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SUPPRESSION_SKIP)
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield Path(dirpath) / name


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the Neptune code checks.")
    ap.add_argument("checks", nargs="*", help="substring(s) of check names; default: all")
    ap.add_argument(
        "--fix", action="store_true", help="let isort and black rewrite files (flake8 and mypy only ever report)"
    )
    ap.add_argument("--list", action="store_true", help="name the checks and exit")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    wanted = [c for c in CHECKS if not args.checks or any(s in c["name"] for s in args.checks)]
    do_suppressions = not args.checks or any(s in "suppressions" for s in args.checks)
    if args.list:
        for c in CHECKS:
            print(f"  {c['name']:<8}{c['what']}")
        print(f"  {'supp':<8}every `type: ignore` has a code and a reason; every `noqa` has a code")
        return 0
    if not wanted and not do_suppressions:
        print(f"no check matches {args.checks}; try --list", file=sys.stderr)
        return 2

    py = interpreter()
    pins, pin_problem = pinned()
    print("NEPTUNE code checks")
    print(f"  python : {py}")
    print(f"  config : {(ROOT / 'pyproject.toml').name}, {(ROOT / 'setup.cfg').name}")
    if pin_problem:
        print(f"  pins   : NOT READ - {pin_problem}; the install command below is unpinned")
    if args.fix:
        print("  --fix  : isort and black will WRITE. flake8 and mypy still only report.")
    print()

    versions = probe(py, wanted)
    results = []
    for c in wanted:
        version = versions[c["name"]]
        if version is None:
            results.append((c["name"], "absent", 0, ""))
            continue
        verdict, found, text = run_tool(py, c, args.fix)
        if text.strip():
            print(f"--- {c['name']} {version} " + "-" * max(0, 62 - len(c["name"]) - len(version)))
            print(text.rstrip())
            print()
        results.append((c["name"], verdict, found, version))

    supp_bad, supp_seen = ([], 0)
    if do_suppressions:
        supp_bad, supp_seen = suppressions()
        if supp_bad:
            print("--- suppressions " + "-" * 46)
            print("\n".join(supp_bad))
            print()

    # ---- the report. One line per check, then the verdict, then what to do about it.
    print("check         verdict")
    print("  " + "-" * 68)
    absent, found_total, broke = [], 0, []
    for name, verdict, found, version in results:
        if verdict == "absent":
            absent.append(name)
            print(f"  {name:<12}NOT INSTALLED - nothing was checked, and that is not a pass")
        elif verdict == "ok":
            print(f"  {name:<12}ok" + (" (rewritten)" if args.fix else "") + f"   ({name} {version})")
        elif verdict == "found":
            found_total += found
            print(f"  {name:<12}{found} finding{'' if found == 1 else 's'}   ({name} {version})")
        else:
            broke.append(name)
            print(f"  {name:<12}RAN BUT DID NOT REPORT - see its output above; judged nothing")
    if do_suppressions:
        if supp_bad:
            found_total += len(supp_bad)
            print(
                f"  {'supp':<12}{len(supp_bad)} finding{'' if len(supp_bad) == 1 else 's'}   "
                f"(of {supp_seen} suppressions inspected)"
            )
        else:
            print(f"  {'supp':<12}ok   ({supp_seen} suppressions inspected, every one named)")

    print()
    for name, verdict, found, version in results:
        pin = pins.get(name)
        if verdict not in ("absent", "broke") and pin and pin.split("==")[1] != version:
            print(f"note   : {name} {version} is running, api/requirements.txt pins {pin}.")
            print("         A formatter at a different version reformats different lines, so a")
            print("         desk and a pipeline on different pins will hand each other churn.")

    if absent:
        # The same sentence api/tests/run.py --coverage prints for a missing coverage
        # package, and for the same reason: a measurement nobody took is not a
        # measurement of zero, and the tool that was not run found nothing because it
        # was not run.
        print(
            f"\nINCOMPLETE - {', '.join(absent)} did not run, so nothing was checked by "
            f"{'it' if len(absent) == 1 else 'them'}."
        )
        print("  Absent is not clean. These are DEV-ONLY tools, deliberately not installed by")
        print("  api/requirements.txt - install.sh builds the Pi's venv from that file and the")
        print("  vehicle does not lint itself. Nothing here installs them for you:")
        print(f"      {py} -m pip install " + " ".join(pins.get(n, n) for n in absent))
        print("  api/requirements.txt carries the same command with the reasoning beside it.")
    if broke:
        print(f"\nINCOMPLETE - {', '.join(broke)} is installed but exited without a verdict this")
        print("  file can read. Its own output is above; no count is printed rather than a")
        print("  wrong one.")

    if found_total:
        print(f"\n{found_total} finding{'' if found_total == 1 else 's'} in total.")
        if any(c["fix"] for c in wanted):
            print("  `python lint.py --fix` settles the isort and black ones by rewriting the")
            print("  files. flake8 and mypy findings are read and fixed by a person; neither")
            print("  tool is allowed to edit this repo.")
    elif not absent and not broke:
        # A SUBSET SAYS SO. `python lint.py black` coming back "everything came out
        # clean" would be a green sentence about four checks when one was run, which is
        # the same shape of overclaim as a test total that quietly drops a suite.
        if args.checks:
            ran = [name for name, *_ in results] + (["suppressions"] if do_suppressions else [])
            print(f"\nClean - but this run checked only {', '.join(ran)}.")
            print("  `python lint.py` with no arguments runs all of them.")
        else:
            print("\nEverything this repo checks came out clean.")

    if found_total:
        return 1
    return 2 if (absent or broke) else 0


if __name__ == "__main__":
    sys.exit(main())
