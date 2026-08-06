# Client tests

Browser checks that run against the **real dashboard**. No framework, no
dependencies, no build step — the same rule the client itself follows. Python
standard library plus a Chrome that is already installed.

```bash
python client/tests/run.py                # every suite  (~95 s, 245 checks)
python client/tests/run.py tether         # one suite (substring match)
python client/tests/run.py map view       # several
python client/tests/run.py --headed       # watch it happen in a real window
python client/tests/run.py --list
python client/tests/run.py -v             # print passing checks too
```

Exit status is **0 only if every check passed**, so it works as a pre-push gate.

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

| Suite | Guards |
|---|---|
| `real-link` | Nothing is synthesised while a vehicle is connected; the input vector always answers the stick |
| `input-dial` | The four 0–100 direction numbers; operator vs datum; the blind-nav dial's position |
| `tether` | 100 m cable: clamped in SIM, warned-only on a real link, 3D range, ring centred on the operator |
| `map-zoom-and-rov` | Max-zoom start, F10/F9 paddle zoom vs the SURFACE combo, pinpointing the ROV |
| `operator-marker` | Green / yellow / red dot by source, and `diveUnderway()` |
| `track-history` | Track breaks (no teleport lines), the eye toggle, out-of-reach ROV refusal |
| `hud-layout` | Depth ramp, exit button, icon sizes, the eye, REC/PIC feedback, map panning |
| `status-and-rail` | Ramp evenness in Oklab, rail width, REC's four states, ROV icon shapes, stick-axis detection |
| `view-follow` | Driving takes the view back from a pan |
| `camera-eye` | The camera's three states in one glyph, the second observer (`/__wifi`), nothing saying it twice, top-bar spacing |
| `demo-mode` | `?sim=1` flies immediately, and every glyph/number/control explains itself |

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

## Requirements

Chrome or Edge. `run.py` looks in the usual install locations, then `NEPTUNE_CHROME`,
then `PATH`; `--chrome <path>` overrides. Python 3.9+, standard library only.
