# NEPTUNE

Control system for a DIY **tethered canal‑cleaning ROV** (submarine), piloted topside from a
handheld (ASUS ROG Ally — landscape, touch + XInput gamepad) over an Ethernet tether to a
Raspberry Pi on the vehicle.

Two halves, one repo — a topside **dashboard PWA** and an on‑board **Pi backend**:

```
 ROG Ally (topside)                     Raspberry Pi (on the sub)                WOLFANG cam
 ┌───────────────┐   Ethernet tether   ┌────────────────────────────┐   Wi‑Fi   ┌──────────┐
 │ dashboard PWA │◀───────eth0────────▶│ nginx (plain HTTP proxy)    │◀──wlan0──▶│ RTSP/CGI │
 │ runs on Ally  │  API · WS · WebRTC  │  ├─ FastAPI  /api  /ws       │  AP:      └──────────┘
 └───────────────┘                     │  └─ go2rtc  /go2rtc /stream  │  ActionCam_b981
                                        └────────────────────────────┘
```

**The Pi is backend‑only** — it never serves the dashboard. The frontend runs **only on the
ROG Ally**, pointed at the Pi's API. This keeps the Pi lightweight (no static files, no SPA).

Video is **zero‑transcode** (go2rtc re‑streams the camera's H.264 to WebRTC), so even a Pi 3
keeps up. `eth0` is the tether (the way in); `wlan0` joins the camera's Wi‑Fi, pinned off the
default route.

```
neptune/
├── client/      topside dashboard (vanilla-JS PWA, zero deps)
├── api/         FastAPI backend + rovlog analysis CLI
├── docs/        hardware, maths, playbook — and docs/handoff/, the bought vehicle's canon
├── .specs/      requirements, design rationale, changelog
├── deploy/      nginx.conf, go2rtc.yaml, systemd units
└── install.sh   one-shot Pi installer / updater
```

## Quick start

**On the Pi** (Raspberry Pi OS Lite 64‑bit, headless; give it internet on Ethernet for the
first run):

```bash
curl -fsSL https://raw.githubusercontent.com/the-harry/neptune/master/install.sh | sudo bash
```

**On the ROG Ally**, run **`client/launch/tether-setup.ps1` once as Administrator** (fixed tether
IP + stops Windows power‑suspending the USB Ethernet adapter), then double‑click
**`client/launch/Neptune.bat`**. It sets everything up in order — finds the Pi by probing
`192.168.42.1` → `neptune.local` → last known, makes a desktop **Neptune** shortcut, starts a
small concurrent local static server, and opens **Chrome/Edge fullscreen** pointed at the Pi.
After that, just use the desktop icon. If anything ever looks stuck: **`Neptune.bat -Stop`**.
Details → [`client/launch/README.md`](client/launch/README.md).

> **The tether is a fixed point‑to‑point link: Pi `192.168.42.1`, Ally `192.168.42.2`.** A direct
> cable has no DHCP server, so anything relying on automatic addressing (or on `.local` mDNS,
> which Windows resolves only intermittently over a link‑local adapter) will not find the Pi.

The Pi is **backend‑only**; the dashboard runs on the Ally from `localhost` (a secure origin, so
geolocation and the PWA work) and talks to the Pi over the tether. Full Pi walkthrough (imaging,
swap, networking, updating) → **[Installing on the Pi](#-installing-on-the-pi)** below.

**For development / simulation** (no hardware, no water):

```bash
cd api && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && python main.py   # http://localhost:8000/
```

## Specs

The reasoning behind the system lives in [`.specs/`](.specs/) — extracted from the code so it
is readable without opening every file:

| File | What |
|---|---|
| [`.specs/requirements.md`](.specs/requirements.md) | user stories + acceptance criteria — what it must do and why |
| [`.specs/design.md`](.specs/design.md) | the breakdown: architecture, mechanisms, and *why* each decision is the way it is |
| [`.specs/tasks.md`](.specs/tasks.md) | changelog — each defect, its cause, and what remains open |

Start with `design.md` if you are about to change something. Most of what looks like an odd
choice is a failure that has already happened on this hardware.

## 🔩 The vehicle (bought 2026-08-18)

**Every major v1 part is bought** (~£500, one design campaign): 130 × 400 mm acrylic hull
with dome, 390 thruster pods at 8 V, peristaltic-pump ballast, rebuilt 3S pack, ESP32
brainstem over USB, burn-wire drop weight, TL88 sonar plan.
**[`docs/hardware.md`](docs/hardware.md) is the vehicle's document** — BOM, wiring, pin
maps, calibrations, bench checklist — with the campaign's drawings and design PDFs beside
it in [`docs/handoff/`](docs/handoff/).

**The code lags the vehicle:** `api/hardware.py` and `api/config.py` still implement the
old bench vehicle (2S bands, syringe ballast, paddlewheel, Pi-GPIO sensing). The gap
ledger is [`docs/hardware.md` §20](docs/hardware.md) — that list **is** the integration
backlog, and integration is the next phase. Until it lands, `NEPTUNE_HW=auto` keeps
falling back to the bench simulator, honestly.

## Documentation

Everything lives in a README next to the code it describes. Start here, then dive in:

### 📱 [`client/README.md`](client/README.md) — the topside dashboard
The vanilla‑JS PWA: video, telemetry, piloting, the map radar, offline behaviour.
- Offline‑first: the client works without the backend
- Layout · Tuning (`config.js`)
- App / fullscreen (installable, URL‑less)
- Serving from FastAPI on the Pi
- Navigation & radar (satellite basemap, origin, areas, dive replay)
- Blackbox recorder (two‑sided logging)
- Testing the fallbacks · Debug console

### 🛰️ [`api/README.md`](api/README.md) — the Pi backend
FastAPI control plane, hardware abstraction, safety watchdog.
- Layout · Run
- API (the WS + REST contract the client speaks)
- Safety · Tuning · Tested

### 🧭 [`api/nav/README.md`](api/nav/README.md) — navigation & map subsystem
Dead reckoning, snapping, dive logs, offline areas, the satellite tile downloader.
- Two‑phase (bootstrap vs isolated) model
- API (mounted in the main app) · CLI
- Tested (no water needed) · Pi notes

### 📐 [`docs/maths.md`](docs/maths.md) — every piece of maths in the nav stack, explained twice
A plain‑language story first (no symbols, skippable equations), the exact implemented
formulas after — geometry, dead reckoning, the tether bound, snapping, the speed table,
the simulator's deliberate lies, calibration, the filters, snag, confidence, depth.

### 🎥 [`api/camera/README.md`](api/camera/README.md) — WOLFANG camera integration
The reverse‑engineered CGI control plane and the go2rtc video bridge.
- How it's wired
- Design points the camera forces
- Develop / test without hardware

### 🗂️ Flight recorder — `python -m blackbox.rovlog`
Two‑sided blackbox (Pi + client) differenced for post‑dive analysis:
`diverge` (lost commands/telemetry, latency, staleness) · `timeline` (clock‑aligned, side‑by‑side)
· `bundle` (incident zip). Logs in `/var/log/rov/`.

---

## 🍓 Installing on the Pi

**Board:** Raspberry Pi 3 B/B+ or newer. **Image:** *Raspberry Pi OS Lite (64‑bit)*, headless —
flash with **Raspberry Pi Imager** and preset SSH, a username, hostname `neptune`, and your home
Wi‑Fi in ⚙️ settings (use your **router** Wi‑Fi, not the camera's).

1. **Internet on Ethernet for the first install.** The installer flips `wlan0` to the camera AP
   partway through, so the download must arrive over `eth0`. Plug it into your router.
2. **Bump swap on a 1 GB Pi 3** for headroom:
   ```bash
   sudo dphys-swapfile swapoff
   sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
   sudo dphys-swapfile setup && sudo dphys-swapfile swapon
   ```
3. **Run the installer** (idempotent — re‑run any time to update):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/the-harry/neptune/master/install.sh | sudo bash
   ```
   It installs the API + a Python venv, downloads go2rtc, joins `ActionCam_b981` on `wlan0`
   (never‑default), and wires nginx (plain HTTP) + systemd on boot. No TLS/cert — it's a sealed
   two‑device tether.
4. **Verify the backend:** `http://192.168.42.1/api/status` (camera JSON) and
   **`http://192.168.42.1/api/system`** — real Pi hardware + network health (CPU temp/load, RAM,
   disk, uptime, per‑interface link state and throughput, service states, undervoltage flags).
   `http://192.168.42.1/` itself just returns a status line — the dashboard runs topside, not here.
   The installer pins the tether address, so there is **nothing to re‑stamp** when you move `eth0`
   from the router to the tether. Then run the dashboard on the Ally (see **Quick start** above).

Overrides (env): `NEPTUNE_REPO`, `NEPTUNE_BRANCH`, `NEPTUNE_TETHER_IFACE` (`eth0`),
`NEPTUNE_TETHER_IP` (`192.168.42.1`), `NEPTUNE_CAM_IFACE` (`wlan0`),
`NEPTUNE_CAM_SSID` / `NEPTUNE_CAM_PSK`, `NEPTUNE_CAMERA_IP`, `NEPTUNE_HOSTNAME` (`neptune`).

```bash
systemctl status neptune-api go2rtc nginx wolfang-route neptune-tether neptune-wifi  # health
journalctl -u neptune-api -f                                              # logs
curl -s http://192.168.42.1/api/system | python3 -m json.tool             # real Pi health
```

## 🧩 Independent subsystems

Every part is separately monitored and **fails on its own** — the dashboard greys only the controls
that belong to whatever is down, and keeps everything else live:

| Subsystem | Down means | Still works |
|---|---|---|
| **ROV link** (`/ws/control`) | vehicle commands rejected (they *never* queue) | map, radar, saved areas, dive logs, config, camera buttons |
| **Video** (go2rtc WebRTC) | NO FEED overlay on the video panel only | everything else, including camera REC/PIC |
| **Camera control** (WOLFANG CGI) | REC greyed — **PIC still saves a topside still** | piloting, video, map |
| **Nav** (`/ws/nav`) | no live track (needs an origin fix first) | piloting, video, camera |
| **Internet** | no place search / new tile downloads | saved offline areas |

The Pi's own health (`/api/system`) is polled independently of all of them, so you can still see
CPU, RAM, disk and both network interfaces when the vehicle link is down.

## ⚠️ Safety

- **SURFACE** (blows ballast) fires only from a deliberate press‑and‑hold (UI) or a two‑paddle
  3 s hold (gamepad) — never a single tap.
- Vehicle commands **fail fast and never queue**: a command sent while the link is down is
  rejected, not replayed later.
- Destructive actions (delete area/dive, SD ops) require explicit confirmation.
- The **camera is configured for the dive automatically** and kept there: the factory
  `PowerSaving=5MIN` powers it off mid‑dive (topside that is indistinguishable from a tether
  fault), and `VideoClipTime=OFF` writes one continuous file that is unrecoverable if power is
  cut. Every setting is verified by re‑reading it — a write the firmware accepted but ignored is
  reported as *not applied*, never as success. See
  [`api/camera/README.md`](api/camera/README.md#defaults--the-camera-does-not-stay-configured-by-itself).
