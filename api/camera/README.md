# WOLFANG camera integration

Control plane for the WOLFANG 4K action camera, reverse-engineered from the
vendor app (protocol values are ground truth — see the build spec). The **video
plane is go2rtc** (RTSP→WebRTC, zero transcode) and lives in [`../../deploy`](../../deploy),
not here. This package is control only.

```
camera/
├── config.py    # camera settings + the asymmetric write↔read name map + slow-op set
├── models.py    # Pydantic models + CgiError / CameraUnavailable
├── cgi.py       # async CGI client: priority serializer, no-keepalive, timeouts,
│                #   circuit breaker, tolerant parser, timing logs
├── service.py   # CameraService + APIRouter (§7.1): status/config/menu/record/
│                #   capture/files/thumb/download/delete/preflight/telemetry WS
├── app.py       # standalone app + create_camera_service() for the ROV main app
├── mock.py      # faithful mock camera (build-first): CGI, timing table, 722, files
└── cli.py       # bench CLI over the service API
```

## How it's wired

The ROV `main.py` mounts this router into the same `:8000` FastAPI app, so the
control plane serves both the ROV (`/ws/control`) and the camera (`/api/*`,
`/ws/telemetry`) from one origin. nginx proxies `/api` + `/ws` to `:8000` and the
video (go2rtc) to `:1984`; the browser never talks to `192.72.1.1` directly.

## Design points forced by the camera (spec §3.3)

- **Single-threaded server** → every CGI call runs through one worker, serialized;
  a priority queue lets user commands jump ahead of the 15 s telemetry polls.
- **Keep-alive is mishandled** → keep-alive disabled, explicit timeout on every call
  (3 s fast / 6 s slow).
- **Some ops block for seconds** → `Video`/`UIMode`/`Playback`/`SD0`/`Videores`/
  `Imageres` get the long timeout + a 1.5 s settle sleep.
- **Circuit breaker** → after a timeout, calls are refused for 5 s and status reports
  `degraded`, so requests don't pile onto a stalled camera.
- **Asymmetric names** → you *write* `Videores` but *read* `Camera.Menu.VideoRes`;
  mapped both ways in `config.py`.
- **`record` is a toggle** → after firing it we poll `…status.record` until it flips;
  UI state is never optimistic.
- **`WarningMSG` is the fault channel** → surfaced in `/api/status` and failed in preflight.
- Destructive ops (`delete`, `SD0=format`) require `?confirm=true`.

## Develop / test without hardware

```bash
# terminal 1 — the mock camera
cd api && python -m camera.mock                 # :8072

# terminal 2 — the API pointed at the mock
cd api && WOLFANG_BASE=http://127.0.0.1:8072 NEPTUNE_HW=mock NEPTUNE_CAM=none \
          python main.py                        # :8000

# terminal 3 — drive it
cd api && python -m camera.cli preflight
         python -m camera.cli status
         python -m camera.cli config set Videores 4K30
         python -m camera.cli files --type video
```

Everything here is covered by the scratchpad integration tests (parser, timing
table, 722, record-toggle confirmation, Range download, delete/format confirm,
preflight, telemetry WS, and the combined ROV+camera boot).

## Not done yet (next turn)

The **dashboard SPA** — swapping the client's MJPEG `<img>` for a WebRTC player fed
by go2rtc, plus the mode-gated config panel, file browser, and record/SD/warning
UI. That's a frontend change on top of the existing purple client.
