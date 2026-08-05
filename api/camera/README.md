# WOLFANG camera integration

Control plane for the WOLFANG 4K action camera, reverse-engineered from the
vendor app (protocol values are ground truth — see the build spec). The **video
plane is go2rtc** (RTSP→WebRTC, zero transcode) and lives in [`../../deploy`](../../deploy),
not here. This package is control only.

```
camera/
├── config.py    # camera settings + the asymmetric write↔read name map + slow-op set
├── defaults.py  # WHAT the camera should be set to, and why — probed, not assumed
├── models.py    # Pydantic models + CgiError / CameraUnavailable
├── cgi.py       # async CGI client: priority serializer, no-keepalive, timeouts,
│                #   circuit breaker, tolerant parser, timing logs
├── service.py   # CameraService + APIRouter (§7.1): status/config/menu/record/
│                #   capture/files/thumb/download/delete/defaults/probe/preflight/
│                #   telemetry WS, plus the defaults guard loop
├── app.py       # standalone app + create_camera_service() for the ROV main app
├── mock.py      # faithful mock camera (build-first): CGI, timing table, 722, files
└── cli.py       # bench CLI over the service API
```

## Defaults — the camera does not stay configured by itself

The factory state is actively hostile to a dive. `PowerSaving=5MIN` powers the camera
off mid-dive, and topside that is **indistinguishable from a tether fault**.
`VideoClipTime=OFF` writes one continuous `.MOV`, which is unrecoverable if power is
cut — the single highest-value setting on the camera.

`defaults.py` is the table: every setting carries the reason it is there, a tier
(`critical` / `quality` / `hull`), and whether it is safe to assert while under way.
It also lists what is **deliberately not set**, which is load-bearing — without it the
next reader assumes those were forgotten.

Nothing is written blind. For almost every property worth setting, **the valid value
set and even the write name are unknown** (§7): a wrong value returns `722`, and a
wrong *name* is accepted with `0 OK` and silently does nothing. So each setting carries
candidate names and candidate values, every attempt is verified by re-reading the
property, and what actually worked is cached per firmware version in
`/var/lib/neptune/camera-caps.json`.

The two failure modes are told apart on the wire: a `722` means the property parsed and
the **value** was refused (keep the name, try the next value); an accepted-but-unchanged
read-back means the **name** is probably wrong (move on). A cold probe of the whole
table costs a couple of seconds in the background; afterwards the cache makes it free.

Applied automatically, without anyone asking:

| When | What |
|---|---|
| camera first seen | connect sequence (§5.5) then the full table |
| camera returns after being unreachable | the same — a rebooted camera has a wrong clock and may be back at factory |
| every `defaults_recheck_s` (60 s) | drift check; anything critical that moved is put back |

That loop is also the **keepalive**. The 15 s telemetry poll only runs while a dashboard
is subscribed, so with nobody watching there is no CGI traffic at all and an idle timer
we failed to disable has nothing to reset.

Slow settings (`Videores`, `Imageres`, the preview bump) are `hot=False`: they stall the
camera's single-threaded server, and RTSP shares it, so applying one mid-dive is a second
of blind piloting. They are connect-time only and are skipped while recording.

```bash
GET  /api/camera/defaults                  # what the last pass achieved, per setting
POST /api/camera/defaults                  # re-apply now
POST /api/camera/defaults?reprobe=true     # ignore cached "firmware ignores it" verdicts
POST /api/camera/probe?prop=LCDPower&values=OFF,1MIN&dwell=5   # discovery, restores after
```

`WOLFANG_APPLY_DEFAULTS=0` leaves the camera exactly as found — but still **audits**, so
preflight reports the truth either way.

### AWB is the conditional one

Water absorbs red first, so everything goes blue-green. With no lamps the warmest preset
(`INCANDESCENT`) counteracts it; with the white LEDs on the lamps restore red and a warm
preset on top produces an orange cast, so `AUTO` is right instead. The service reads the
white-light state from the vehicle (`get_rov`) and reconciles on the next guard tick, so
a light change takes up to `defaults_recheck_s` to follow.

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
- **An unknown property name is accepted with `0 OK` and silently ignored** → anything
  written must be verified by re-reading. `preflight()` once reported
  `PowerSaving=OFF (critical) OK` on a camera that then powered itself off mid-dive: it
  wrote `PowerSaving`, read back a property of that name rather than
  `Camera.Menu.PowerSaving`, got `None`, and scored `None` as a pass.

## Develop / test without hardware

The mock starts at the **factory** state from the HAR capture — `PowerSaving=5MIN`,
`VideoClipTime=OFF`, `IsStreaming=NO`, `StatusLights` reading back as `OF` rather than
`OFF` — and models the write names *independently* of what `config.py` guesses.
`MOCK_WRITE_NAMES=short|dotted|none` switches which naming convention the emulated
firmware honours, so the probe can be tested against a firmware that disagrees with us,
and `none` proves an unsettable critical property is reported as unmet rather than as
success.

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
