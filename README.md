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
├── deploy/      nginx.conf, go2rtc.yaml, systemd units
└── install.sh   one-shot Pi installer / updater
```

## Quick start

**On the Pi** (Raspberry Pi OS Lite 64‑bit, headless; give it internet on Ethernet for the
first run):

```bash
curl -fsSL https://raw.githubusercontent.com/the-harry/neptune/master/install.sh | sudo bash
```

**On the ROG Ally**, double‑click **`client/launch/Neptune.bat`**. It sets everything up in
order — asks for the Pi IP once, makes a desktop **Neptune** shortcut, starts a tiny local
static server, and opens **Brave/Chrome/Edge fullscreen** pointed at the Pi.
After that, just use the desktop icon. Details → [`client/launch/README.md`](client/launch/README.md).

The Pi is **backend‑only**; the dashboard runs on the Ally from `localhost` (a secure origin, so
geolocation and the PWA work) and talks to the Pi over the tether. Full Pi walkthrough (imaging,
swap, networking, updating) → **[Installing on the Pi](#-installing-on-the-pi)** below.

**For development / simulation** (no hardware, no water):

```bash
cd api && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && python main.py   # http://localhost:8000/
```

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
4. **Verify the backend:** `http://neptune.local/api/status` (JSON) and `/stream/` for video.
   `http://neptune.local/` itself just returns a status line — the dashboard runs topside, not
   here. When you move `eth0` to the **tether**, re‑run the installer once so it re‑stamps the
   real tether IP into go2rtc. Then run the dashboard on the Ally (see **Quick start** above).

Overrides (env): `NEPTUNE_REPO`, `NEPTUNE_BRANCH`, `NEPTUNE_TETHER_IFACE` (`eth0`),
`NEPTUNE_CAM_IFACE` (`wlan0`), `NEPTUNE_CAM_SSID` / `NEPTUNE_CAM_PSK`, `NEPTUNE_CAMERA_IP`.

```bash
systemctl status neptune-api go2rtc nginx wolfang-route   # health
journalctl -u neptune-api -f                               # logs
```

## ⚠️ Safety

- **SURFACE** (blows ballast) fires only from a deliberate press‑and‑hold (UI) or a two‑paddle
  3 s hold (gamepad) — never a single tap.
- Vehicle commands **fail fast and never queue**: a command sent while the link is down is
  rejected, not replayed later.
- Destructive actions (delete area/dive, SD ops) require explicit confirmation.
