# Neptune — topside launcher (ROG Ally / Windows)

**One thing to run.** Double‑click **`Neptune.bat`**. It walks through every step itself,
skipping anything already done:

1. **Find the Pi** — probes `192.168.42.1` (the fixed tether address), then `neptune.local`,
   then whatever was saved last, and uses the **first one that actually answers `/api/status`**.
2. **Desktop shortcut** — creates a **Neptune** icon (trident) on the desktop.
3. **Local server** — a small *concurrent* static server on `127.0.0.1` (no admin, no deps).
4. **Map backend** — starts the API **on this handheld**, so the chart layers, offline areas,
   the downloader and the readiness check work **with no Pi attached**. See below.
5. **Dashboard** — opens **Chrome → Edge** (first found) **fullscreen**, pointed at the Pi.

The Pi is plain HTTP (sealed tether, no TLS), so there's **no certificate to deal with**.

The dashboard is **offline‑first**: if no Pi answers, it still opens and runs, and connects by
itself the moment the Pi appears. A missing Pi is never a launch failure.

---

## ⚡ First time only: set up the tether

A direct Ally↔Pi Ethernet cable has **no DHCP server**. Left on automatic, Windows falls back to
a `169.254.x.x` link‑local address and the Pi does the same — so neither can find the other, and
the dashboard shows "no connection" with the cable plugged in.

Run this **once**, as Administrator (it will prompt):

```powershell
client\launch\tether-setup.ps1
```

It does five things:

| | What | Why |
|---|---|---|
| 1 | Ethernet adapter → **`192.168.42.2/24`** | matches the Pi's fixed `192.168.42.1`; no DHCP, no mDNS needed |
| 2 | **USB selective suspend → off** | Windows was power‑suspending the USB tether NIC mid‑session, which drops the link and looks exactly like the Pi dying |
| 3 | **Adapter power‑down → disabled** | same reason, via the driver's own power management |
| 4 | **Location → allowed for desktop apps** | Windows keeps this *second* switch off by default. Chrome is a desktop app, so with it off `navigator.geolocation` is denied no matter what the page asks, and the map can never take an origin |
| 5 | **Chrome auto‑granted location for `http://localhost`** | otherwise Chrome asks per‑origin, and that prompt is easy to miss on a fullscreen handheld. Scoped to loopback only — the page we serve ourselves |

> Step 5 lives here rather than in the launcher because `HKCU\Software\Policies` is
> ACL‑protected: Chrome policy writes need elevation even in the user hive.
> Verified on a fresh Chrome profile: no prompt, fix returned at ±58 m.

Undo with `tether-setup.ps1 -Revert`. No gateway is set on the tether, so **Wi‑Fi stays your
internet path** — the cable only reaches the Pi.

> The Pi side is handled by `install.sh`, which pins `192.168.42.1/24` on `eth0` *in addition to*
> DHCP. Plugged into a router it still takes a lease; on the tether it always holds that address.

---

## The map backend (`step 4`)

**The Ally holds the map data, not the Pi** — and until this step it had nothing to hold it
*with*.

Every piece of chart the console draws is an **API endpoint**: `/api/areas` (what is on this
card), `/api/areas/<area>/crt` and `/crt/<layer>` (the Trust's hazard layers),
`/api/areas/<area>/depth/nominal` and `/depth/surveyed`, `/api/areas/<area>/centreline`,
`/api/areas/fetch` (the downloader), `/api/readiness`. The launcher used to serve the client's
**files** and start no backend at all, so on a handheld with no Pi there was nothing to answer
any of them — and the chart panel said *"no chart data downloaded"*. That sentence was **true
and useless**: it named a missing download when what was missing was a server.

### The map and the vehicle are different backends

> **Simulating the vehicle means there is no sub.** The physics is a model and every reading
> taken off it is flagged as simulated — that rule does not move, and step 4 does not touch it.
>
> **It never meant the map was fake.** Satellite imagery, the Trust's hazard layers, the canal
> centreline and the offline area are real data about real water, downloaded from real services.
> They are exactly as true with nothing on the end of the tether as with a sub on it, so the map
> data served here is **not** marked simulated — because it is not.

The launcher starts the API with **`NEPTUNE_HW=mock`**, stated outright rather than left to
`auto`. `auto` lands on the same bench mock (`RealHardware.__init__` raises while the GPIO is
unwired) but gets there by *trying to be a vehicle and failing*, and logs that as a warning on a
machine where no sub was ever expected. `mock` states the intent: **this process is a map server
on a handheld.** `NEPTUNE_CAM=none` for the same reason — left on `auto` the API hunts for a
camera and would end up holding the Ally's own webcam.

It says so on screen, and again on `/__api`, so nothing can mistake this backend for a sub:

```
[4/5] Map backend
      OK  python 3.14.6  (found on PATH)
      --  starting it (this takes a few seconds the first time)
      OK  map backend on http://127.0.0.1:8000  (started here; stops when this window does)
      --  vehicle hardware: mock - this serves the MAP. The sub stays simulated and flagged.
      !!  the map backend is up but NO area is downloaded - chart panels will still read empty
      --  download one from the map panel while this handheld has internet
```

| | |
|---|---|
| **Address** | `http://127.0.0.1:8000`, **loopback only** (the API's own default is `0.0.0.0`; this is a backend for the machine it runs on). |
| **Which port** | **Fixed, and deliberately not auto‑advanced the way `-Port` is.** The static server's port only has to be *free*, because the launcher hands the browser the URL it chose. This port has to be **guessable** — the dashboard finds it without being told, and `127.0.0.1:8000` is the address the repo already writes down (`bootstrap.py` prints it, `api/config.py` defaults to it). Sliding quietly to 8001 would leave a running map backend the console could not find, which looks exactly like the blank map this step exists to fix. If it is busy, the launcher **says so**. |
| **Which python** | `Find-Python` — the same search `-Test` uses, written once: the repo venvs (`.venv`, then `api/.venv`) exactly as `bootstrap.py`'s `venv_python()` and `api/tests/run.py`'s `VENVS` believe them, then `PATH`, then the `py` launcher. A `PATH` python is *proved to run* first: on Windows `python.exe` is usually the Microsoft Store alias stub, which is not an interpreter at all. |
| **Supervised, not assumed** | The launcher polls `/api/healthz` until it answers (up to 40 s — a cold start imports FastAPI, the nav service and the chart code). *"Started the process"* is not the claim the operator needs; *"the map has a backend"* is. |
| **Then it counts the areas** | With a backend up, *"no chart data downloaded"* finally has **two** meanings, so the launcher says which one this handheld is in. A map server with nothing on the card is still a blank map — and that is a download somebody has to go and do while there is internet. |

### When it can't start

**A map that cannot download is a degraded console, not a dead one.** Every failure below is
reported in the launcher's own voice and **the dashboard still opens and flies**:

| What happened | What it says |
|---|---|
| No python anywhere | `no working python (tried the repo venvs, PATH, then the py launcher)` + the `bootstrap.py --dev` fix |
| The API's packages are missing | the last lines of the child's own output, and — on a `ModuleNotFoundError` — `run: python bootstrap.py --dev` |
| Port 8000 held by something else | `port 8000 is held by <name> (pid N) and it is not a Neptune api`, plus `-ApiPort 8010` |
| It started and never answered | `did not answer … within 40 s`, with the tail of `navigation_logs/logs/map-backend.err.log` |

Both of the child's streams are written to `navigation_logs/logs/map-backend.{out,err}.log`,
**truncated per launch**: that log is the only record of a backend that dies in 300 ms, and it is
read exactly once — just after it happened.

### Stopping, and not fighting over the port

The backend is a **separate process**, not a thread of the launcher, so nothing about closing
the window stops it on its own — and it is the one thing here holding a well‑known port.

- The dashboard's **EXIT** button, closing the window, and **`-Stop`** all take it down.
- A **second launch** sweeps ours away first, so two can never fight over 8000.
- **Only ours.** It is identified by our `--app-dir` on its command line — the same idiom the
  browser cleanup uses with `--user-data-dir`, and for the same reason. A `uvicorn` you started
  yourself in a terminal is *adopted* if it answers on the port, and is **left running** at exit.
- Killed **twice over**: the handle `Start-Process` returned, then a sweep for the marker.
  That is not belt‑and‑braces — measured on this handheld, the `python` on `PATH` is the Windows
  **Python Manager shim**, which re‑execs the real interpreter as a *child* carrying the same
  command line. The handle is a wrapper; the sweep is the part that gets the process actually
  holding the port. For the same reason the startup wait asks the **port** before it asks the
  handle: a wrapper exiting is not the same event as the backend dying.

`-NoApi` skips the whole step and serves the dashboard files only.

## Options

- `Neptune.bat -Stop` — **close a stuck dashboard, map backend and free the ports.** Use this if
  anything looks wedged; it is always safe.
- `Neptune.bat -PiHost 192.168.42.1` — skip discovery, use this address.
- `Neptune.bat -Port 8090` — different local port for the dashboard files (it auto‑advances if
  one is busy anyway).
- `Neptune.bat -ApiPort 8010` — different local port for the **map backend**. Fixed, never
  auto‑advanced — see above for why.
- `Neptune.bat -NoApi` — **don't start the map backend.** Dashboard files only; chart layers,
  areas and downloads will have nothing to answer them.
- `Neptune.bat -Setup` — steps 1–2 only, don't launch.
- `Neptune.bat -Kiosk` — locked kiosk window. **Not recommended on the handheld** (see below).
- `Neptune.bat -Test` — **run both check suites and show the result.** `-Test client` or
  `-Test api` for one half. See [Checking the build](#checking-the-build-neptunebat--test).

## Checking the build (`Neptune.bat -Test`)

Same double‑click as launching the console, because that is the only interaction this
handheld has. There is no terminal on it and no keyboard to type one with, so *"run the
suites before you get in the boat"* was a checkout ritual nobody could perform at the
waterside — and **a suite that cannot be run where the vehicle is, is a suite that quietly
stops being run.**

```
Neptune.bat -Test           api, then client
Neptune.bat -Test client    the dashboard only
Neptune.bat -Test api       the vehicle only
```

It runs `api/tests/run.py` and `client/tests/run.py` — the same two runners you would type
by hand, unchanged. **No totals are written on this page on purpose:** the runners print
their own, this repo has already carried four stale counts, and a number in prose is a
number that goes wrong quietly. Run it and read what it says.

| | |
|---|---|
| **Where it sits** | Ahead of the single‑instance mutex. It starts no server, opens no port, creates no shortcut and closes no browser — so it works with a dive already up on the machine. |
| **Which python** | The repo venv first (`.venv`, then `api/.venv`) exactly as `bootstrap.py`'s `venv_python()` and `api/tests/run.py`'s `VENVS` believe them, and only then `PATH`. A `PATH` python is *proved to run* before it is used: on Windows `python.exe` is usually the Microsoft Store alias stub, which is not an interpreter at all. |
| **What it needs** | Python 3 for both halves; a Chrome or Edge for the client half (the runner finds it and says so). If the api half reports missing packages, it prints the one command that fixes it — `python bootstrap.py --dev`. |
| **Exit status** | `0` everything ran and passed · `1` a check failed · `2` nothing failed but something could not be **run**. The same three‑value language `api/tests/run.py` speaks, and `Neptune.bat` hands it back to whoever called it. |

**Green means it ran.** The verdict quotes the runner's own total instead of counting
anything itself, and a zero exit is *not* accepted as a pass when there was **no total
printed**, **a total of zero checks**, or an `INCOMPLETE` verdict. `-Test api` says on the
block that only half the system was checked, rather than "all checks passed".

**The result is built to be read on a small screen in sunlight**, by someone holding a wet
ROV: a filled colour block whose **frame character carries the answer as well as the colour
does** — `=` passed, `#` failed, `?` could not tell. That `?` is the same one the dashboard
shows for a sensor that is not answering, and it means the same thing here. Colour is never
the only carrier in this project, and a phone photo of that window may be all anyone has by
the time it is discussed.

The window then holds itself open long enough to read (longer on a failure) and closes by
itself. **It never waits for a keypress** — there is no keyboard on this machine.

## Why it is not a kiosk any more

`--kiosk` removes every window control. On a device with **no physical keyboard**, "press Alt+F4
to exit" is not an exit — once it was up, the only way out was a hard reboot. Two changes:

- the default is now a **fullscreen app window**, which can be closed and Alt‑Tabbed away from;
- the dashboard has an **EXIT button** (top right) that stops the local server and closes the window.

## Getting unstuck

The launcher is single‑instance and cleans up after itself, so this should not happen. If it ever
does, `Neptune.bat -Stop` fixes it without a reboot:

- It refuses to start a **second** server (a global mutex), instead of the old behaviour where the
  second instance threw, hit a blocking `Read-Host`, and waited forever *behind* a fullscreen window.
- It **kills leftover Neptune browser windows** before launching. A window orphaned by a previous
  run owns Chromium's process‑singleton for this profile, so a new launch would hand its command
  line to the orphan and exit instantly — which the old script read as "the browser closed" and
  used to shut its own web server down ~180 ms after starting.
- Liveness is decided by **scanning for browser processes using our own profile directory**, never
  by the PID `Start-Process` returned (that PID legitimately exits immediately in the case above).
- It only ever touches browsers launched with **our** `--user-data-dir`, so your own browser
  windows are never closed.
- On exit it stops the browser **and** the listener, so the port is always free for the next launch.
- It stops the **map backend** too — a separate process that would otherwise outlive the window
  and sit on port 8000, making the *next* launch report the port busy. See
  [Stopping, and not fighting over the port](#stopping-and-not-fighting-over-the-port).

## Files

| File | What |
|---|---|
| `Neptune.bat` | double‑click launcher (runs the script) |
| `neptune.ps1` | discovery + shortcut + concurrent static server + **map backend** + fullscreen browser, and `-Test` (the check suites) |
| `tether-setup.ps1` | **run once as admin** — fixed tether IP, USB power fixes, location auto-grant |
| `crash-diagnostics.ps1` | **run as admin** — kernel crash dumps + GPU timeout headroom |
| `neptune-host.txt` | last known Pi address (discovery overwrites it when it finds a live one) |

**Chrome, then Edge.** Brave is no longer used. If Chrome isn't installed the launcher falls back
to Edge, which is the same Chromium engine and works identically.

## `/__api`

**Where the map backend is, and whether it came up** — as JSON, on the launcher's own port:

```json
{"ok":true,"url":"http://127.0.0.1:8000","why":"","hardware":"mock","areas":0,"vehicle":false}
```

The page cannot work any of this out for itself, and the launcher's own answer is printed to a
console window that is **behind a fullscreen browser within half a minute of launch** — so on the
machine that matters, that message is unread. Without this, *"the map backend never started"* and
*"this card has no areas on it"* look identical from the dashboard: both end in "no chart data
downloaded", which is true and useless, and is the exact defect this endpoint exists to make
actionable.

| Field | Means |
|---|---|
| `ok` | a backend answered `/api/healthz`. |
| `url` | where it is. Empty only if the launcher never got that far. |
| `why` | the launcher's **own words** for whatever went wrong, so the console can repeat them instead of inventing a diagnosis. Empty when `ok`. |
| `hardware` | the API's vehicle backend — `mock`. Reported so nothing can mistake this for a sub. |
| `areas` | offline areas on this handheld. `0` is a real, empty card; `-1` means it could not be asked. |
| `vehicle` | **always `false`.** This is a map server. What it *serves* — areas, the Trust's chart layers, the centreline, the depth models — is real data about real water and is **not** flagged as simulated. |

## `/__screenshot`

The dashboard's PIC button hits this for a real screen capture — a page cannot screenshot
itself, and a canvas only knows about the video and the map, not the instruments around them.
Returns a PNG of the primary screen (`CopyFromScreen`, the same thing PrintScreen does).

The listener is **loopback-only**, so only this machine can ask. `SetProcessDPIAware()` is
called at startup because a DPI-unaware process on this 1920x1080-at-150% handheld is told the
screen is 1280x720 and captures only its top-left corner.

## `/__save` and `/__record`

The dashboard has no way to write a file: the browser can only offer a download, and Chrome
blocks every automatic download after the first. So the launcher writes everything a session
produces, into `client/navigation_logs/`:

| Endpoint | Writes |
|---|---|
| `POST /__save?kind=images\|videos\|logs&name=<n>[&append=1]` | composite stills, the session log |
| `POST /__record?action=start\|stop\|status&name=<n>` | screen recording via ffmpeg |
| `GET /__screenshot?name=<n>` | full-screen PNG into `images/` |

Names are sanitised to a bare filename before they touch the filesystem - the page is not
trusted to stay inside the folder.

Recording is `gdigrab -> libx264 -crf 23 -preset veryfast -an` (no audio), about 1.4 MB/min on
a mostly-static screen. Stopping writes `q` to ffmpeg's stdin instead of killing it, so the
moov atom is written and the file actually plays.

The AMD GPU encoder (`h264_amf`) would use less CPU and is deliberately not used: this
handheld has an unresolved kernel fault under sustained GPU load, and a recorder that can take
the machine down mid-dive is worse than one that costs CPU.

ffmpeg is installed by `tether-setup.ps1`, or drop `ffmpeg.exe` in `client/launch/bin/`.
Without it stills and logs are unaffected and only recording is unavailable - the launcher
says which at startup.
