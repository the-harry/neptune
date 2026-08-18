# Build spec: ROV camera control + streaming service

You are implementing a control and video-streaming service that runs on a Raspberry Pi
inside an RC submarine. All of the protocol details below were reverse-engineered from
HAR captures of the vendor Android app. **Treat them as ground truth — do not guess
endpoints, and do not "improve" the values.**

---

## 1. Physical topology

```
[Action camera] --WiFi AP (2.4GHz)--> [Pi wlan0]
                                      [Pi eth0] --tether (Cat5)--> [Topside laptop]
```

- The camera and the Pi are inside the **same dry compartment**. The WiFi hop is ~10 cm
  through air. Radio does not pass through water, so this is the only viable arrangement.
- The tether is the only link to the surface. Video and control both cross it.
- The camera is battery-powered and independent of the Pi.

**Routing constraint:** `wlan0` is on the camera's AP; `eth0` carries the tether and holds
the default route. The camera's DHCP server must not install a default route. Verify at
startup that `ip route get 192.72.1.1` resolves via `wlan0`; if not, pin it:
`ip route add 192.72.1.1/32 dev wlan0`. Fail loudly at boot if this check fails —
misrouting presents as a total camera outage and is easy to misdiagnose as a hardware fault.

---

## 2. Camera identification

| Property | Value |
|---|---|
| Model | WOLFANG 4K action camera |
| Chipset family | AIT / MStar (a.k.a. "redsonic" firmware) |
| Firmware | `0255` |
| HTTP `Server` header | `AIT Multimedia Network Solution, UPnP/1.0 devices/1.6.19` |
| AP SSID | `ActionCam_b981` |
| AP password | `12345678` |
| Camera IP | `192.72.1.1` |
| CGI base | `http://192.72.1.1/cgi-bin/Config.cgi` |
| RTSP port | `554` |

---

## 3. The CGI protocol

### 3.1 Request format

Everything is a `GET` with query parameters. There are four verbs.

```
GET /cgi-bin/Config.cgi?action=get&property=<PROP>
GET /cgi-bin/Config.cgi?action=set&property=<PROP>&value=<VAL>
GET /cgi-bin/Config.cgi?action=del&property=<$-DELIMITED-PATH>
GET /cgi-bin/Config.cgi?action=dir&property=<Normal|Photo>&format=all&from=0&count=100&backward=
```

No authentication of any kind. No headers are required. The vendor app sends
`User-Agent: okhttp/3.11.0` but the server does not check it.

### 3.2 Response format

`text/plain`, newline-delimited:

```
0
OK
Camera.Battery.Level=100
```

- Line 1 — status code. `0` means success.
- Line 2 — status text, `OK` on success.
- Lines 3+ — zero or more `key=value` pairs.

Errors return a non-zero code and message on the first two lines, e.g. `722 Invalid state`
when a `set` is issued while the camera is in the wrong mode.

**Parser must tolerate:** blank lines inside the body (the `WarningMSG` value can contain a
newline), and values containing `=`. Split on the *first* `=` only.

### 3.3 Server behaviour you must design around

These are the four things that will break a naive implementation.

**a) It is single-threaded.** Two concurrent requests do not interleave; the second blocks in
the accept queue. Serialise every CGI call behind one global lock. Never open parallel
connections.

**b) It says `Connection: close` but is handed `Connection: Keep-Alive`.** Connection pooling
gets you a half-dead socket and a hang. Disable keep-alive explicitly on the client, and
always set an explicit timeout — an unbounded client wait is the most common failure here.

**c) Some operations block the whole server for seconds.** Measured, from the capture:

| Operation | Observed time |
|---|---|
| `Video=capture` | 1857 ms, 2256 ms |
| `Camera.Menu.UIMode` change | 1014–1120 ms |
| `Playback=exit` (state actually changes) | 894–992 ms |
| `Playback=exit` (already exited, no-op) | 174–190 ms |
| `Playback=enter` | 355–381 ms |
| `Video=record` (start) | 631 ms |
| `Video=record` (stop) | 293 ms |
| `SD0=format` | 151 ms (returns immediately; formats in background) |
| All `get` operations | 12–60 ms |

Use a 3 s read timeout for fast properties and 6 s for the slow set. Classify these as slow:
`Video`, `Camera.Menu.UIMode`, `Playback`, `SD0`, `Videores`, `Imageres`.
After any slow command, sleep ~1.5 s before the next request to let the camera settle.

**d) Responses carry `Cache-Control: max-age=2`.** If anything proxies the CGI, battery and
status reads will go stale for two seconds. Bypass or override caching in the proxy layer.

---

## 4. Complete command reference

### 4.1 Settable menu properties

The camera serves its own menu definition at `http://192.72.1.1/cammenu.xml`. Fetch it at
startup and drive the UI from it rather than hardcoding — other firmware revisions differ.
Its current contents:

```
action=set&property=Videores&value=   4K30 | 2.7K30 | 1080P60 | 1080P30 | 720P120
action=set&property=Imageres&value=   20MP | 16MP | 12MP | 8MP
action=set&property=AWB&value=        AUTO | DAYLIGHT | CLOUDY | FLUORESCENT1 |
                                      FLUORESCENT2 | FLUORESCENT3 | INCANDESCENT
action=set&property=SD0&value=format   ← DESTRUCTIVE, wipes the card
```

Note the asymmetry: you **write** `Videores` / `Imageres` / `AWB`, but you **read** them back
as `Camera.Menu.VideoRes` / `Camera.Menu.ImageRes` / `Camera.Menu.AWB`. Different names for
the same setting. Map both directions explicitly.

### 4.2 Mode control

```
action=set&property=Playback&value=exit          # leave playback, return to live
action=set&property=Playback&value=enter         # enter playback (needed for file browse)
action=set&property=Camera.Menu.UIMode&value=VIDEO
action=set&property=Camera.Menu.UIMode&value=CAMERA
action=set&property=TimeSettings&value=YYYY$MM$DD$HH$MM$SS
```

`TimeSettings` uses `$` as its field separator, e.g. `2026$08$03$16$41$01`. Set it on every
connect — the camera has no RTC battery and drifts, and the timestamp is burned into the
recorded video.

### 4.3 Shutter

```
action=set&property=Video&value=record    # TOGGLE — start if stopped, stop if started
action=set&property=Video&value=capture   # take a still
```

Two hard rules, both observed in the app's behaviour:

1. **Set `UIMode` first.** `VIDEO` before `record`, `CAMERA` before `capture`. Issuing the
   shutter in the wrong mode returns `722 Invalid state`.
2. **`record` is a toggle, not a setter.** There is no `value=start` / `value=stop`. After
   firing it, poll `Camera.Preview.MJPEG.status.record` until it changes, and drive the UI
   from the polled value. Never show recording state from optimistic local state — if a
   toggle is lost, the dashboard will claim you are recording when you are not, and you will
   surface with no footage.

### 4.4 Readable state

`action=get&property=Camera.Menu.*` returns the full menu snapshot:

```
Camera.Menu.AWB=AUTO              Camera.Menu.DefMode=VIDEO
Camera.Menu.EV=EV0                Camera.Menu.FWversion=0255
Camera.Menu.Flicker=50Hz          Camera.Menu.GSensor=OFF
Camera.Menu.HDR=OFF               Camera.Menu.ImageRes=20MP
Camera.Menu.IsStreaming=NO        Camera.Menu.LCDPower=30SEC
Camera.Menu.LoopingVideo=OFF      Camera.Menu.MTD=OFF
Camera.Menu.PhotoBurst=UNKNOW     Camera.Menu.PowerOffDelay=OFF
Camera.Menu.PowerSaving=5MIN      Camera.Menu.Property=
Camera.Menu.Q-SHOT=OFF            Camera.Menu.SD0=READY
Camera.Menu.SoundIndicator=ON     Camera.Menu.SpotMeter=OFF
Camera.Menu.StatusLights=OF       Camera.Menu.TV=NONE
Camera.Menu.TVSystem=PAL          Camera.Menu.Timelapse=5SEC
Camera.Menu.UIMode=VIDEO          Camera.Menu.UpsideDown=Normal
Camera.Menu.VideoClipTime=OFF     Camera.Menu.VideoRes=1080P30
```

Most of these are not in the vendor UI but are probably settable by the same name. Treat
writes to them as best-effort: attempt, re-read, report whether it took.

Three matter operationally:

- **`PowerSaving=5MIN`** — will power the camera off mid-dive. Set to `OFF` on connect.
- **`LCDPower=30SEC`** — screen blanking; set to `OFF` (or leave, to save battery — but know
  that a blank screen is not a fault).
- **`UpsideDown=Normal`** — set to inverted if the camera is mounted upside down in the hull.

Other useful reads:

```
action=get&property=Camera.Battery.Level              → 0-100
action=get&property=Camera.Capture.Remaining          → shots left on card
action=get&property=Camera.Capture.*
action=get&property=Camera.Preview.*
action=get&property=Camera.Preview.MJPEG.status.record → Standby | (recording state)
action=get&property=Camera.Menu.FWversion
action=get&property=*                                  → everything the firmware exposes
```

`Camera.Preview.*` currently returns:

```
Camera.Preview.H264.bitrate=1200000    Camera.Preview.H264.h=360
Camera.Preview.H264.w=640              Camera.Preview.MJPEG.bitrate=4000000
Camera.Preview.MJPEG.fps=30            Camera.Preview.MJPEG.w=320
Camera.Preview.MJPEG.h=240             Camera.Preview.MJPEG.status=ACTIVE
Camera.Preview.MJPEG.status.mode=Videomode
Camera.Preview.MJPEG.status.record=Standby
Camera.Preview.MJPEG.TimeStamp=ACTIVE  Camera.Preview.MJPEG.WarningMSG=<text>
Camera.Preview.RTSP.av=4               Camera.Preview.RTSP.keepalive=60
Camera.Preview.RTSP.rtcp=10            Camera.Preview.RTSP.tran=100
Camera.Preview.Source.1.Camid=front    Camera.Preview.Source.Totals=2
```

**`Camera.Preview.MJPEG.WarningMSG` is your primary fault channel.** Observed value when the
card was absent: `NO CARD!`. Surface it prominently on the dashboard; anything other than
empty means the camera will not record.

Attempt to raise the preview stream resolution at startup, and re-read to see if it took
(firmware 0255 may ignore it):

```
action=set&property=Camera.Preview.H264.w&value=1280
action=set&property=Camera.Preview.H264.h&value=720
```

---

## 5. File API

### 5.1 Listing

```
action=dir&property=Normal&format=all&from=0&count=100&backward=   # videos
action=dir&property=Photo&format=all&from=0&count=100&backward=    # stills
```

Paginated by `from` / `count`. Returns XML:

```xml
<?xml version="1.0" encoding="UTF-8" ?>
<Normal>
<file>
	<name>/SD/Video/FILE260803-164124-000001F.MOV</name>
	<format size="1920x1080" fps="30" time="7.1">MOV</format>
	<size>14487552</size>
	<attr>RW</attr>
	<time>2026-08-03 16:41:24</time>
</file>
<amount>1</amount>
</Normal>
```

The root element is `<Normal>` or `<Photo>` matching the query. `<format>` carries `size`,
and for video also `fps` and `time` (duration in seconds) as attributes. Photos report
`size="5120x3840"` with element text `jpeg`.

Listing requires the camera to be in playback mode for some firmware paths — the app calls
`Playback=enter` around browsing and `Playback=exit` when done. Wrap file operations in that
pair, and always `Playback=exit` afterwards so live preview resumes.

### 5.2 Download

Files are served over plain HTTP at the path from `<name>`:

```
GET http://192.72.1.1/SD/Video/FILE260803-164124-000001F.MOV
```

**Range requests are supported.** Confirmed:

```
Range: bytes=65580-
→ 206 Partial Content
  Content-Range: bytes 65580-14483915/14483916
  Content-Type: video/quicktime
```

So downloads are resumable and chunkable. Implement offload with `Range`, resume on failure,
and a modest chunk size — a dropped tether mid-transfer must not restart a multi-GB file.
Do **not** parallelise across many connections; the server is single-threaded and will stall.
One transfer at a time, sequential.

Thumbnails come from a parallel path and return JPEG:

```
GET http://192.72.1.1/thumb/Video/FILE260803-164124-000001F.MOV
```

### 5.3 Delete

```
action=del&property=$SD$Video$FILE260803-164124-000001F.MOV
```

The delete path uses `$` where the listing returns `/`. Convert with
`name.replace("/", "$")`. Deletion is immediate and unrecoverable.

---

## 6. Video streaming

### 6.1 Source

```
rtsp://192.72.1.1/liveRTSP/av4
```

H.264, **640×360, 1.2 Mbps**, from `Camera.Preview.H264.*`. Aliases: `/liveRTSP/v1` and
`/liveRTSP/v3` are the same stream. `/liveRTSP/v2`, `/av1`, `/av3` give a low-resolution
MJPEG stream instead — ignore them.

If RTSP refuses to connect, issue `Playback=exit` first. The app always does.

### 6.2 Do not transcode

The source is already small and already H.264. Re-encoding on the Pi costs 200+ ms of latency
and most of the CPU budget to produce a worse picture. **Remux only — pass the video
bitstream through untouched and drop audio.**

Use **go2rtc** (or MediaMTX) as the video plane, RTSP in, WebRTC out:

```yaml
# go2rtc.yaml
streams:
  sub:
    - rtsp://192.72.1.1/liveRTSP/av4#video=copy#audio=drop
webrtc:
  candidates:
    - <PI_TETHER_IP>:8555
api:
  listen: ":1984"
```

`#video=copy` is mandatory — it is what makes this zero-encode. `#audio=drop` avoids a
sync-stall failure mode common on these cameras.

**Do not use HLS.** It adds 2–6 s of latency and defeats the purpose.

Expected end-to-end: ~400–800 ms, dominated by the camera's own RTSP buffering.

### 6.3 Client-side latency settings

Whatever plays the stream must be configured for low latency or it will buffer a second away
by itself. For reference, the equivalent tuning in other players:

```
vlc --network-caching=50 --live-caching=50 --clock-jitter=0 --clock-synchro=0 --no-audio
ffplay -fflags nobuffer -flags low_delay -probesize 32 -analyzeduration 0 -sync ext -framedrop -an
gst-launch-1.0 rtspsrc location=... latency=0 protocols=udp drop-on-latency=true ! ...
```

---

## 7. Service architecture

```
Pi:
  go2rtc      :1984   video plane, WebRTC, no transcode
  <your API>  :8000   control plane, REST + WebSocket telemetry
  nginx       :80     serves the dashboard SPA, reverse-proxies both

Topside: browser → http://<pi>/
```

**Everything goes through the Pi. The frontend must never talk to `192.72.1.1` directly.**
Three independent reasons: the CGI sends no CORS headers so the browser will block it; the
camera subnet only exists on the Pi's `wlan0` and is unroutable from topside; and a browser
opening six parallel connections is precisely what hangs a single-threaded server. Serving
the SPA and proxying both planes from one nginx origin also removes CORS entirely.

The wrapper's overhead is ~1–2 ms against a camera that needs 12 ms at best and 2256 ms at
worst. It is not a latency concern.

### 7.1 Required API surface

```
GET  /api/status              → battery, record state, mode, SD state, warning msg
GET  /api/config              → cached Camera.Menu.* snapshot
PUT  /api/config/{property}   → set + re-read + return actual value
GET  /api/menu                → parsed cammenu.xml (valid options for the UI)
POST /api/record/toggle       → fire Video=record, poll until state flips, return new state
POST /api/capture             → UIMode=CAMERA, Video=capture, return new file
GET  /api/files?type=video|photo&from=0&count=100
GET  /api/files/{name}/thumb
POST /api/files/{name}/download   → queued sequential offload with Range resume
DELETE /api/files/{name}          → requires explicit confirm flag
POST /api/preflight               → the startup sequence in §7.3
WS   /ws/telemetry                → pushes status at 15 s intervals
```

### 7.2 Implementation requirements

- **One global async lock** around every CGI call. User commands take priority over telemetry
  polls in the queue.
- **Cache `Camera.Menu.*`** — read once on connect, update on write, never re-read per render.
- **Battery/telemetry poll every 15 s**, pushed over WebSocket. Faster polling stutters the
  RTSP stream; the CGI server and the encoder share the same weak SoC.
- **Circuit breaker:** after a timeout, stop issuing CGI calls for ~5 s and report degraded
  state. Piling requests onto a stalled camera turns a 1 s hiccup into a dead session.
- **Destructive operations** (`SD0=format`, `del`) require an explicit confirmation parameter
  and must not be reachable from a single UI click.
- **Structured logging** of every CGI call with duration, so the timing table above can be
  re-validated against real firmware behaviour.

### 7.3 Startup / pre-flight sequence

Run in order, serialised, before a dive; report a pass/fail checklist:

1. Verify `ip route get 192.72.1.1` → `dev wlan0`.
2. `Playback=exit`.
3. `TimeSettings=<now>`.
4. Read `Camera.Menu.*`, cache it.
5. Fetch and parse `cammenu.xml`.
6. Set `PowerSaving=OFF` and `LCDPower=OFF`. **Critical** — the 5-minute default powers the
   camera down mid-dive, and that failure looks exactly like a tether fault.
7. Read `Camera.Preview.MJPEG.WarningMSG`; fail pre-flight if non-empty.
8. Read `Camera.Capture.Remaining` and `Camera.Menu.SD0`; fail if not `READY` or if remaining
   is implausibly low.
9. Attempt the 1280×720 preview bump, re-read, report actual.
10. Confirm RTSP connects and go2rtc reports the stream healthy.
11. Read `Camera.Battery.Level`; warn below 40%.

### 7.4 The operational hazard to design for

Mode changes and the RTSP stream share the same single-threaded server. **Every `UIMode`
switch blanks the video for roughly 1.1 s.** Under way, that is a second of blind piloting
caused by someone nudging a settings dropdown.

Therefore:

- Gate all config changes behind a "surfaced / stopped" toggle in the UI. In-dive controls are
  limited to record toggle plus telemetry.
- Give the WebRTC client aggressive automatic reconnect so a mode change self-heals without a
  page refresh.
- Poll `Camera.Menu.IsStreaming` as a health signal.
- Show a clear "video interrupted — camera reconfiguring" state rather than a frozen frame, so
  the pilot knows the difference between a stall and a still scene.

---

## 8. Deliverables

1. The Pi service (Python/FastAPI or Go — your call, state the choice and why).
2. `go2rtc.yaml` and systemd units for both services.
3. nginx config serving the SPA and proxying `/api`, `/ws`, and the go2rtc endpoints.
4. A dashboard SPA: live video, battery, record state with polled confirmation, SD status and
   warning message, mode-gated config panel driven by `cammenu.xml`, file browser with
   thumbnails and resumable download.
5. A CLI for the same API, for bench testing without a browser.
6. A mock camera server that reproduces the response formats, the timing table from §3.3(c),
   the single-threaded blocking, and the `722 Invalid state` error — so the whole stack can be
   developed and tested without the hardware in the loop.

Build the mock first.
