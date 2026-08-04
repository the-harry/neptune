# NEPTUNE Sub API

FastAPI backend for the ROV. Speaks the client's contract directly: a real-time
control **WebSocket**, an **MJPEG** camera feed, and it serves the static client.
One authoritative `RovState`; a single background loop advances it, runs the
safety **watchdog**, and broadcasts telemetry to every connected client.

## Layout

```
api/
├── main.py        # FastAPI app: /ws/control, /stream.mjpg, /healthz, static mount, loop
├── protocol.py    # Pydantic WS message contract (in + out) — source of truth
├── rov.py         # RovState: applies control/commands, watchdog, telemetry
├── hardware.py    # HW abstraction: MockHardware (bench) + RealHardware (GPIO, TODO)
├── camera.py      # Picamera2 MJPEG, with a synthetic bench fallback
├── sysinfo.py     # REAL Pi health from /proc + /sys (no dependencies)
├── config.py      # all tunables (env-overridable)
└── requirements.txt
```

## Run

```bash
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py            # or: uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000/` (server serves the client), or open the client
from disk and point it at the host via `?host=…`.

- **On a laptop** (no Pi hardware/camera): auto-selects `MockHardware` +
  synthetic camera, so telemetry animates and `/stream.mjpg` shows a test
  pattern. `mock: true` rides in telemetry.
- **On the Pi**: install Picamera2 (`sudo apt install -y python3-picamera2`) and
  fill in the `TODO(hardware)` methods in `hardware.py` (pins are mapped at the
  top of `RealHardware`), then flip the `wired` flag in `RealHardware._gpio_available()`.
  Until you do, `RealHardware.__init__` **raises on purpose** so `NEPTUNE_HW=auto`
  falls back to the bench simulator. That is deliberate: a backend that reports
  `mock: false` while every sensor returns a constant presents fabricated zeros
  (`0.0 V`, `heading 0`, "at the surface") as genuine instrument readings, which is
  strictly worse than an honest simulation. Set `NEPTUNE_HW=real` to require real
  hardware and fail loudly instead.

  **Pi system health is always real**, regardless of the vehicle backend — see below.

## API (matches the client exactly)

- `GET  /stream.mjpg` — multipart MJPEG.
- `WS   /ws/control` — client→server: `control{throttle,steer}`, `camera{pan,tilt}`,
  `ballast{cmd}`, `command{name,value}`, `ping`. server→client: `telemetry{…}`,
  `alarm{name:"leak"}`, `pong`. See `protocol.py`.
- `GET  /healthz`, `GET /api/healthz` — status, hardware/camera backend, client count.
  (The `/api/` alias exists so it is reachable through the nginx reverse proxy topside.)
- `GET  /api/system` — **real Pi hardware + network health** (`sysinfo.py`). CPU
  temperature/load/percent/frequency, RAM, swap, disk, uptime, per-interface link state,
  negotiated speed, addresses and RX/TX throughput, Wi-Fi association + signal, systemd
  service states, undervoltage/throttling flags, and camera reachability.

  Zero dependencies — it reads `/proc`, `/sys` and `os.statvfs` directly (psutil was
  dropped). Slow probes (`vcgencmd`, `systemctl`, `iw`) run on a background task and are
  cached, so the endpoint never blocks the control loop.

  **Every probe degrades on its own.** A field that cannot be read is `null`, never `0` —
  the dashboard renders `--`. That distinction matters: `CPU 0 °C` reads as a measurement
  and hides the fault, which is exactly how the old psutil-less path made every gauge look
  plausible and be wrong.

  The compact subset (`cpu_c`, `cpu_pct`, `ram_pct`, `disk_gb`, `uptime_s`,
  `net_tether_up/_mbps`, `net_cam_up/_signal`) also rides on every telemetry frame.
- `GET  /` … — the static client (`html=True`).

## Safety

- **Watchdog** (`NEPTUNE_WATCHDOG_S`, default 0.5s): if control frames stop while
  armed, thrusters are zeroed until control resumes.
- **Disarm/E-STOP** immediately zero thrusters.
- On shutdown the vehicle is safed (disarm, thrusters off, ballast hold).

## Tuning

Everything is in `config.py`, each value overridable by env var (rates, watchdog
window, camera size/fps/quality, pressure model, hardware/camera backend).

## Tested

`MockHardware` + synthetic camera are exercised end-to-end: watchdog fail-safe,
ping/pong, arm→telemetry, per-command effects, ballast fill, leak alarm edge,
malformed-frame resilience, and a live MJPEG stream with complete JPEG frames.
