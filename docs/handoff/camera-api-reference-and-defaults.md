# Camera API reference and recommended defaults

Context for an agent configuring the action camera used as the forward camera on an RC
submarine. Everything below was reverse-engineered from HAR captures of the vendor Android app
against the actual device. **Treat the protocol details as ground truth; treat the value lists as
a starting point and re-enumerate on the device** (see §7).

---

## 1. Device

| Property | Value |
|---|---|
| Model | WOLFANG 4K action camera |
| Chipset family | AIT / MStar ("redsonic" firmware) |
| Firmware | `0255` |
| `Server` header | `AIT Multimedia Network Solution, UPnP/1.0 devices/1.6.19` |
| AP SSID / PWD | `ActionCam_b981` / `12345678` |
| Camera IP | `192.72.1.1` |
| CGI base | `http://192.72.1.1/cgi-bin/Config.cgi` |
| RTSP | `rtsp://192.72.1.1/liveRTSP/av4` (port 554) |

Deployment: the camera sits in a sealed dry compartment with a Raspberry Pi ~10 cm away on the
camera's own WiFi AP. The Pi relays video and control to the surface over a wired tether.

---

## 2. Protocol

Four verbs, all `GET` with query parameters, no authentication, no CORS headers.

```
?action=get&property=<PROP>
?action=set&property=<PROP>&value=<VAL>
?action=del&property=<$-DELIMITED-PATH>
?action=dir&property=<Normal|Photo>&format=all&from=0&count=100&backward=
```

Response is `text/plain`:

```
0
OK
Camera.Battery.Level=100
```

Line 1 = status code (`0` = success), line 2 = status text, lines 3+ = `key=value`. Errors return
non-zero, e.g. `722 Invalid state` when the camera is in the wrong mode for the requested set.

**Parser must** split on the first `=` only, and tolerate embedded newlines in values
(`WarningMSG` can contain them).

### Four behaviours that break naive clients

1. **Single-threaded.** Concurrent requests block. Serialise everything behind one lock.
2. **Sends `Connection: close` while the client asks keep-alive.** Connection pooling yields a
   half-dead socket and a hang. Disable keep-alive; always pass an explicit timeout.
3. **Some operations block the whole server for seconds** (§6).
4. **`Cache-Control: max-age=2`** on responses. Bypass caching in any proxy.

---

## 3. Settable commands

### 3.1 From `cammenu.xml` (the camera serves its own menu definition at `http://192.72.1.1/cammenu.xml`)

```
?action=set&property=Videores&value=   4K30 | 2.7K30 | 1080P60 | 1080P30 | 720P120
?action=set&property=Imageres&value=   20MP | 16MP | 12MP | 8MP
?action=set&property=AWB&value=        AUTO | DAYLIGHT | CLOUDY | FLUORESCENT1 |
                                       FLUORESCENT2 | FLUORESCENT3 | INCANDESCENT
?action=set&property=SD0&value=format   ← DESTRUCTIVE
```

**Name asymmetry — important.** You *write* `Videores`, `Imageres`, `AWB`, but you *read* them
back as `Camera.Menu.VideoRes`, `Camera.Menu.ImageRes`, `Camera.Menu.AWB`. Map both directions
explicitly or verification will always appear to fail.

### 3.2 Mode and shutter

```
?action=set&property=Playback&value=exit | enter
?action=set&property=Camera.Menu.UIMode&value=VIDEO | CAMERA
?action=set&property=TimeSettings&value=YYYY$MM$DD$HH$MM$SS     ($ separators)
?action=set&property=Video&value=record     ← TOGGLE, not a setter
?action=set&property=Video&value=capture
```

`UIMode` must match the shutter: `VIDEO` before `record`, `CAMERA` before `capture`. Wrong mode
returns `722`.

`record` toggles. There is no explicit start/stop. Fire it, then poll
`Camera.Preview.MJPEG.status.record` until it changes, and drive all UI state from the polled
value — never from optimistic local state.

### 3.3 Files

```
?action=dir&property=Normal&format=all&from=0&count=100&backward=
?action=del&property=$SD$Video$FILE260803-164124-000001F.MOV
GET http://192.72.1.1/SD/Video/<file>          ← Range requests supported (206)
GET http://192.72.1.1/thumb/Video/<file>       ← JPEG thumbnail
```

Listing returns `<name>/SD/Video/...</name>` with `/`; delete needs `$`. Convert with
`name.replace("/", "$")`.

---

## 4. Full readable state

`?action=get&property=Camera.Menu.*` returns:

```
AWB=AUTO            DefMode=VIDEO       EV=EV0            Flicker=50Hz
GSensor=OFF         HDR=OFF             ImageRes=20MP     IsStreaming=NO
LCDPower=30SEC      LoopingVideo=OFF    MTD=OFF           PhotoBurst=UNKNOW
PowerOffDelay=OFF   PowerSaving=5MIN    Q-SHOT=OFF        SD0=READY
SoundIndicator=ON   SpotMeter=OFF       StatusLights=OF   TV=NONE
TVSystem=PAL        Timelapse=5SEC      UIMode=VIDEO      UpsideDown=Normal
VideoClipTime=OFF   VideoRes=1080P30    FWversion=0255
```

Most of these are not exposed in the vendor UI but are probably settable by the same name. Treat
writes as best-effort: set, re-read, report whether it took.

`?action=get&property=Camera.Preview.*` returns:

```
H264.w=640  H264.h=360  H264.bitrate=1200000
MJPEG.w=320 MJPEG.h=240 MJPEG.fps=30 MJPEG.bitrate=4000000
MJPEG.status=ACTIVE  MJPEG.status.mode=Videomode  MJPEG.status.record=Standby
MJPEG.TimeStamp=ACTIVE  MJPEG.WarningMSG=<text>
RTSP.av=4  RTSP.keepalive=60  RTSP.rtcp=10  RTSP.tran=100
Source.1.Camid=front  Source.Totals=2
```

Other useful reads: `Camera.Battery.Level`, `Camera.Capture.Remaining`,
`Camera.Menu.FWversion`, and `?action=get&property=*` for everything.

---

## 5. Recommended defaults for this application

Rationale matters more than the values — conditions vary and the agent should be able to reason
about deviations.

### 5.1 Critical — will cause dive failures if left at factory settings

| Property | Set to | Why |
|---|---|---|
| `PowerSaving` | `OFF` | Factory `5MIN` powers the camera off mid-dive. The symptom looks exactly like a tether fault and will be misdiagnosed. |
| `PowerOffDelay` | `OFF` | Same class of failure. |
| `VideoClipTime` | **a finite value, ~3–5 min** | Currently `OFF` = one continuous file. A `.MOV` being written when power is cut is **unrecoverable**. Segmenting caps the loss at one segment. On a battery-powered sub with a hard-kill risk, this is the single highest-value setting change. |
| `MTD` (motion detect) | `OFF` | Would start/stop recording on motion. Drifting particulate underwater would trigger it constantly. |
| `GSensor` | `OFF` | Impact-triggered file locking. Locked files accumulate and fill the card, and the sub bumps things routinely. |

### 5.2 Image quality for turbid, low-light water

| Property | Set to | Why |
|---|---|---|
| `Videores` | `1080P30` | Not 4K. Underwater detail is limited by turbidity, not sensor resolution, so 4K buys little real information while costing card space, bitrate, and — critically — **heat in a sealed hull with no convection**. Use `2.7K30` for clear shallow water; reserve `4K30` for short bright dives. |
| — avoid | `1080P60`, `720P120` | Higher frame rates mean shorter exposures. Underwater is a low-light environment; 30 fps buys roughly double the light of 60 fps. Motion is slow anyway. |
| `AWB` | **conditional — see below** | The most consequential image setting. |
| `EV` | `EV0` with lights, `+0.3…+0.7` without | Enumerate valid values first; only `EV0` was observed. Raising EV lengthens exposure and adds noise, so prefer adding light over adding EV. |
| `HDR` | `OFF` | Underwater scenes are low-contrast, not high-contrast — HDR adds processing with little benefit and can look mushy. Consider `ON` only near the surface with bright sky in frame. |
| `SpotMeter` | `OFF` | Spot metering samples the centre, which underwater is usually the dark open water column. Average metering handles the scene better. |
| `Imageres` | `12MP` | 20MP on these sensors is almost certainly interpolated rather than native. Lower resolution should also shorten the very slow capture operation (§6). |

**AWB is conditional on lighting, and this matters more than anything else in the list.** Water
absorbs red first, so everything goes blue-green.

- **Natural light, no lamps** → `INCANDESCENT`. It is the warmest preset available and
  counteracts the blue cast most strongly. This is the standard trick for cameras without a
  dedicated underwater mode.
- **Artificial white LEDs on** (the rig has controllable lights) → `AUTO` or `DAYLIGHT`. The lamps
  restore red at short range, and forcing a warm preset on top of that produces an orange cast.
- Expose this as a two-position toggle in the dashboard tied to the lights control, rather than
  burying it in a settings menu.

### 5.3 Hull-specific

| Property | Set to | Why |
|---|---|---|
| `StatusLights` | `OFF` | Indicator LEDs inside a sealed hull are invisible to the operator and **reflect off the inside of the port or dome**, causing flare in the recorded image. |
| `SoundIndicator` | `OFF` | Beeps are inaudible inside a sealed hull and pointless. Also avoids contaminating any future hydrophone. |
| `LCDPower` | see note | **Verify the semantics before setting.** `OFF` may mean "screen never blanks" (wasting power and adding heat) rather than "screen off". Set it, observe the physical camera, and record which it is. In a sealed hull you want the screen dark. |
| `UpsideDown` | per mounting | Set to inverted if the camera is mounted upside down, rather than rotating in post. |
| `Flicker` / `TVSystem` | `50Hz` / `PAL` | Correct for UK mains. Only relevant under artificial lighting, but harmless and already correct. |
| `LoopingVideo` | `OFF` | You want the whole dive retained. Consider `ON` only for long unattended runs where a full card is likelier than a need for the earliest footage. |
| `Timelapse`, `Q-SHOT`, `PhotoBurst` | ensure inactive | Enumerate and confirm none are engaged. |
| `MJPEG.TimeStamp` | `ACTIVE` (keep) | Burns a timestamp into the image, which lets recorded footage be correlated against the blackbox log after a dive. Worth keeping for that reason alone. |

### 5.4 The pilot preview stream

The RTSP substream is what the operator flies on. It is **640×360 at 1.2 Mbps** by default — the
4K30 recording goes to the SD card separately and is not what you see live.

```
?action=set&property=Camera.Preview.H264.w&value=1280
?action=set&property=Camera.Preview.H264.h&value=720
```

Attempt the bump, re-read to confirm, and fall back gracefully — firmware `0255` may ignore it.
Do **not** re-encode the stream on the Pi; remux only. Re-encoding costs latency and CPU to
produce a worse image.

`RTSP.tran=100` is likely a transport or latency knob. Poke it and re-read; cheap experiment,
potentially useful.

### 5.5 Connect sequence

Serialised, in order:

1. `Playback=exit`
2. `TimeSettings=<now>` — no RTC in the camera, and the timestamp is burned into the video
3. Read `Camera.Menu.*` and cache
4. Fetch and parse `cammenu.xml`
5. Apply the §5.1 critical settings, verifying each by re-read
6. Apply §5.2 / §5.3 per current conditions
7. Attempt the preview bump
8. Read `Camera.Preview.MJPEG.WarningMSG` — **fail pre-flight if non-empty** (observed: `NO CARD!`)
9. Read `Camera.Menu.SD0` and `Camera.Capture.Remaining` — fail if not `READY` or implausibly low
10. Read `Camera.Battery.Level` — warn below 40%

---

## 6. Timings — measured, not estimated

| Operation | Observed |
|---|---|
| `Video=capture` | 1857 ms, 2256 ms |
| `Camera.Menu.UIMode` change | 1014–1120 ms |
| `Playback=exit` (state changes) | 894–992 ms |
| `Playback=exit` (already exited) | 174–190 ms |
| `Playback=enter` | 355–381 ms |
| `Video=record` start / stop | 631 ms / 293 ms |
| `SD0=format` | 151 ms (returns immediately, formats in background) |
| Any `get` | 12–60 ms |

Use a 3 s read timeout for fast properties, 6 s for the slow set (`Video`, `Camera.Menu.UIMode`,
`Playback`, `SD0`, `Videores`, `Imageres`), and sleep ~1.5 s after any slow command.

**Operational consequence:** mode changes and the RTSP stream share the same single-threaded
server, so **every `UIMode` switch blanks the video for ~1.1 s.** Under way that is a second of
blind piloting. Gate configuration changes behind a "surfaced/stopped" state; keep the in-dive UI
to record toggle and telemetry only.

---

## 7. Enumerate before setting

Several properties above are readable but their **valid values are unknown** — only the current
value was observed. Do not guess; a bad value returns `722` or silently no-ops.

Unknown value sets: `EV`, `HDR`, `LCDPower`, `PowerSaving`, `PowerOffDelay`, `LoopingVideo`,
`GSensor`, `MTD`, `SpotMeter`, `SoundIndicator`, `StatusLights`, `UpsideDown`, `VideoClipTime`,
`Timelapse`, `Q-SHOT`, `PhotoBurst`, `Flicker`, `TVSystem`, `DefMode`.

Discovery procedure:

1. `?action=get&property=*` — dump everything the firmware exposes.
2. Fetch `http://192.72.1.1/cammenu.xml` — authoritative for anything the vendor UI exposes.
3. For the rest, probe: attempt a plausible value, re-read, record whether it took. Build a
   capability table for this firmware and cache it.
4. Record `FWversion` alongside the table — a different firmware invalidates it.

**Do not send `SD0=format` during discovery.** It is destructive and returns success immediately,
so it will not look like a mistake until the card is empty.
