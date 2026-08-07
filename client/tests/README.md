# Client tests

Browser checks that run against the **real dashboard**. No framework, no
dependencies, no build step — the same rule the client itself follows. Python
standard library plus a Chrome that is already installed.

```bash
python client/tests/run.py                # every suite
python client/tests/run.py tether         # one suite (substring match)
python client/tests/run.py map view       # several
python client/tests/run.py --headed       # watch it happen in a real window
python client/tests/run.py --list         # name the suites and exit
python client/tests/run.py -v             # print passing checks too
```

Exit status is **0 only if every check passed**, so it works as a pre-push gate.

---

## Where the numbers live

**The runners are the only thing in this repo entitled to state a check total, and no
document repeats one.** This page used to open with `~95 s, 249 checks`. That line was the
stalest of **four different totals for one suite that were in circulation simultaneously**
— 214 in `bootstrap.py`, 249 here, 286 in `client/README.md` and `.specs/design.md`, and a
fifth number in reality — because each had been copied forward from whichever tree its
writer had open rather than from a run. Two of them went stale in the very commit that
"fixed" them.

A stale count is worse than no count. Someone who reads 286 and watches a different number
scroll past has been told the bench is running something other than what it is, and then
has no reason to believe anything else the page says. That is the real cost: it is not one
wrong number, it is the credibility of every number next to it.

So the rule, and it is now kept by construction rather than by discipline:

| Quantity | Where it comes from | Why |
|---|---|---|
| **checks** | `run.py`'s own final line | a browser check is an `ok(...)` call inside an async flow, several of them inside loops. Nothing short of running Chrome knows how many there are, so nothing short of running Chrome may claim to. |
| **suites** | counted off the tree — one file per suite | `bootstrap.py` globs `suites/*.js`; `--list` names them. Derived, so it cannot drift. |
| **api checks and suites** | `python api/tests/run.py --list` | discovered without running, in well under a second — so `bootstrap.py` *asks* instead of remembering. |
| **wall time** | measured, and stamped with the machine and the date | see below. This one genuinely helps: it is the difference between "I have time for a coffee" and "something has hung". |
| **the visual tolerance** | measured against the noise floor | `--shot-noise`, see *The visual layer*. |

**Wall time, measured on the ROG Ally (RC71L, Ryzen Z1 Extreme, Windows 11), 2026-08-07:**
the full client run took **≈145 s** and the api run **≈5 s**. Those are *measurements on one
machine on one day*, not a contract — they moved from 129 s and 3 s within the same
afternoon as suites landed, which is the whole reason this line is stamped instead of
promised. A Raspberry Pi 3B+ will be several times slower on the client half and the api
half will barely move. If your run is wildly outside that, suspect the machine or a hung
suite, not the suite list.

Nothing else here, in `client/README.md`, in `api/README.md` or in `.specs/tasks.md` states
a total. If you find one, it is a bug in the document — including the one known survivor,
`.specs/design.md` §14, which is logged in `.specs/tasks.md`'s open table because it was
outside the file ownership of the round that cleared the rest. That is the same shape of
miss as the unowned `api/nav/sensors.py`: the document nobody was assigned goes on saying
what every other document has stopped saying.

---

## Running it on each platform

The runner is standard-library Python plus a browser it finds by itself: the usual install
locations first, then `NEPTUNE_CHROME`, then `PATH`, and `--chrome <path>` overrides all
of it. Python 3.9+.

| Platform | Browser it finds | Notes |
|---|---|---|
| **Windows** (the ROG Ally — the machine this is flown from) | `C:\Program Files\Google\Chrome\Application\chrome.exe`, the x86 and `%LOCALAPPDATA%` variants, then Edge | Edge is the same engine and works |
| **macOS** | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` | nothing to install if Chrome is there |
| **Raspberry Pi OS / Linux** | `chromium-browser`, `chromium`, `google-chrome` off `PATH` | `sudo apt install -y chromium-browser`. Nothing here needs the internet once that is on the card, which is the point — the Pi is normally on a sealed tether |

Both suites, both halves of the system, one command, on any of the three:

```bash
python bootstrap.py --test
```

That is also what to run when you do not yet know what the machine is missing: without
`--test` it reports what is present and absent for both halves — including the Pi-only
hardware libraries, which it never installs — and prints the two suite commands with the
counts it *asked the runners for*.

**On the Ally, one touch:** `client/launch/neptune.ps1 -Test` runs the suites, shows the
result and exits non-zero if anything is wrong, without a terminal or a remembered command
line. The handheld has no keyboard in the field and the whole point of the launcher is
that the console is reachable by double-click; a check gate that needs a shell is a check
gate that stops being run on the machine it is meant to guard.

---

## How it works

`run.py` serves `client/` over loopback, injects one file from `suites/` into
`index.html` as an extra `<script>`, opens it in headless Chrome, and waits for the
suite to `POST` its results to `/__result`.

**The page under test is the shipping dashboard, byte for byte, plus that one tag.**
Nothing is stubbed, mocked, or rebuilt: the suites drive the same `MAP`, `STATUS`,
`CONFIG` and `state` objects the operator drives, call the same functions the buttons
call, and read layout back out of `getBoundingClientRect()`. A check that passes here
passed in a browser, not in an approximation of one.

Each suite runs in its own throwaway Chrome profile on its own free port, so IndexedDB
and service-worker state never leak between them.

## The suites

`--list` is authoritative; this table says what each one is *for*.

| Suite | Guards |
|---|---|
| `real-link` | Nothing is synthesised while a vehicle is connected; the input vector always answers the stick |
| `input-dial` | The four 0–100 direction numbers; operator vs datum; the blind-nav dial's position |
| `tether` | 100 m cable: clamped in SIM, warned-only on a real link, 3D range, ring centred on the operator |
| `map-zoom-and-rov` | Max-zoom start, F10/F9 paddle zoom vs the SURFACE combo, pinpointing the ROV |
| `operator-marker` | Green / yellow / red dot by source, and `diveUnderway()` |
| `track-history` | Track breaks (no teleport lines), the eye toggle, out-of-reach ROV refusal |
| `hud-layout` | Depth ramp, exit button, icon sizes, the eye, REC/PIC feedback, map panning |
| `status-and-rail` | Ramp evenness in Oklab, rail width, REC's four states, tether icon shapes (including that `connecting` alone earns nothing), stick-axis detection |
| `view-follow` | Driving takes the view back from a pan |
| `camera-eye` | The camera's three states in one glyph, the second observer (`/__net`), the four Wi-Fi states and the tether's cable-first states, nothing saying it twice, top-bar spacing |
| `ballast-syringe` | The syringe shape (one clip-path for wall and liquid), drag-up-to-fill driven by real pointer events, and the depth-colour link — including that a real dive refuses to tint depth/pressure from ballast |
| `sensor-loss` | **The last few centimetres**: that a `null` from the vehicle arrives on the glass as an *admission*. `?` and amber for cannot-tell against `--` and dim for a dropped frame; the fourth leak shape; the radar refusing to swing to `-null === 0`; a dead INA219 raising no invented `0.0 V · SURFACE` alarm; tri-state `snagged`/`gyro_only` neither raising nor clearing an alarm on a null |
| `demo-mode` | `?sim=1` flies immediately, and **every glyph, number and control explains itself** — a written `title` *and* `aria-label` in whole sentences. This is the suite that fails you for adding a readout without saying what it means |

## Writing a suite

Drop a `.js` file in `suites/`. It runs after the dashboard has booted; give it a
moment to settle, then post an array of `{name, pass, detail}`:

```js
/* WHAT THIS GUARDS — and why it is worth a test. */
(function(){
  const R=[], errs=[];
  window.addEventListener('error', e=>errs.push(String(e.message)));
  const ok=(n,p,d)=>R.push({name:n, pass:!!p, detail:String(d)});
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));

  (async function(){
    await sleep(2600);                    // boot: IndexedDB, map, service worker
    ok('the thing does the thing', actual===expected, 'actual was '+actual);
    ok('no script errors', errs.length===0, errs.join(' | ')||'none');
    fetch('/__result',{method:'POST',body:JSON.stringify(R,null,1)});
  })();
})();
```

Always put the observed value in `detail`, passing or failing. A green run that prints
`0.1859 m/px` tells you what the code did; a green run that prints `ok` does not, and
the difference matters the day it turns red.

Adding a suite changes both totals. **Do not go and update a number anywhere** — that is
the habit this page exists to break. `bootstrap.py` recounts the suites off the tree and
asks the api runner for its own figures, and no document quotes a check total at all.

`run.py` prepends a small preamble that catches an uncaught throw **inside the suite**
and posts the stack, so a broken suite fails in a second instead of burning the timeout.
Errors raised by the *dashboard* are deliberately left alone — several suites assert on
exactly those.

## Gotchas worth knowing

Every one of these produced a test that passed while the product was wrong, or failed
while the product was right. They are the reason this file exists.

- **Persist the origin.** `refreshBootstrap` re-reads it from IndexedDB every 5 s, so
  setting `MAP.origin` in memory alone gets silently reverted mid-test. Use
  `await STORE.set('origin', o)` as well — that is what every real path does.
- **Never poke `state.input`.** `computeInput` rewrites it every frame, so an assertion
  set that way is testing a zero. Install a fake gamepad and set `axes` instead.
- **Wait for transitions.** The rail buttons carry `transition-all`. Sampling a colour
  80 ms after a class change compares two nearly identical values and proves nothing —
  this is how a REC button that visibly did not change colour passed its own test. Give
  it ~700 ms.
- **Read colours from a canvas pixel**, not `getComputedStyle().color`. Modern syntax
  comes back as `oklch(...)` / `color(srgb ...)`, and scraping digits out of that yields
  nonsense — once reporting an Oklab distance of 1.979, a value the space cannot hold.
- **Headless has no video**, so BLIND NAV engages on its own a second or so after boot.
  Suites that need the collapsed radar must set `CONFIG.map.blindNav=false` and call
  `exitBlindNav()` first.
- **Expanding the map engages ALL STOP**, and any held movement key collapses it again
  immediately. Clear `state.keys` before calling `expandMap()`.
- **`MAP.hdg` is driven every tick** in SIM, so assigning it and expecting it to stick
  does not work; drive it through heading instead.
- **Assert the rendered string, not its truthiness.** `-null === 0` is a real defect that
  every "did it change?" check walks straight past — which is why `sensor-loss` asserts
  the radar's whole `rotate(...)` transform text rather than that a heading was applied.

## The visual layer

Every suite is photographed at the moment it finishes, over CDP (`cdp.py` — a ~130-line
WebSocket client, because Chrome's `--screenshot` flag fires on load and then *exits*,
so it can never capture a state a suite drove the page into). Two shots per suite:

| File | Purpose |
|---|---|
| `screenshots/<suite>.png` | the real thing, kept to be **looked at** |
| `screenshots/<suite>.layout.png` | the same page with the live map and video hidden — the only version stable enough to **compare** |

`baseline/*.layout.png` is committed; `screenshots/` is gitignored. `--bless` accepts the
current shots as the new baseline, `--shot-noise` prints the drift percentage even when
it passes.

**The tolerance is measured, not guessed.** With the live surfaces hidden the residue is
0.000–0.016% (a few digits of telemetry and antialiasing), so the threshold is **0.1%**,
about six times the floor. This matters more than it sounds: at the 2% I first reached
for, the exit button growing from 28 px to 44 px went completely unnoticed — it is only
0.13% of the screen. At 0.1% the same change reports **0.91% drift** while all 24 numeric
checks still pass, which is precisely the gap a picture is here to cover.

**`map-zoom-and-rov` is recorded but never compared.** Its picture is *about* the map:
satellite tiles arrive from the network and the vehicle moves underneath them, so two
identical runs differed by 36% and 66%. A check that cannot be stable should not pretend
to be — so one suite owns the map and tolerates it, and every other suite is photographed
without it.

Drift **reports** by default and only fails the run with `--strict-visual`. The numeric
checks are the gate; the picture is the thing you look at when they all pass and something
still feels wrong.

**A baseline is part of a UI change, not a follow-up.** A round that changes the client and
leaves `baseline/` alone puts every compared suite over tolerance at once, and because
drift only reports, the run still exits 0 — so the picture layer spends that round telling
nobody anything, which is the one state it must not be in. `--bless` in the same commit,
and look at the shots before you do.

## Requirements

Chrome or Edge. `run.py` looks in the usual install locations, then `NEPTUNE_CHROME`,
then `PATH`; `--chrome <path>` overrides. Python 3.9+, standard library only.
