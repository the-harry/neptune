# Build spec: blackbox flight recorder

Add a centralised, crash-survivable event log to the ROV Pi, for post-incident analysis when
something goes wrong on a dive. This is a companion to the camera control and navigation specs.

The design goal: **after a failure, the log alone must be enough to reconstruct what happened
and why, without needing to have been watching the screen.**

---

## 1. Format and location

A single append-only **JSONL** stream — one JSON object per line — is the container. One line per
event, no multi-line records, no nesting at the top level. It survives truncation (a corrupt tail
loses one line, not the file), it greps, and it streams.

```
/var/log/rov/
  navigation_20260804T003502Z.jsonl     ← one file per session
  current.jsonl                          ← symlink to the active session
  archive/                               ← rotated older sessions
```

**Every line has the same fixed envelope**, with source-specific payload under `d`:

```json
{"t":1234.567,"tw":"2026-08-04T00:35:02.123Z","s":48213,"src":"nav","lvl":"info","e":"dr_step","d":{...}}
```

| Field | Meaning |
|---|---|
| `t` | **`CLOCK_MONOTONIC` seconds since boot.** The primary time base. |
| `tw` | Wall clock, best-effort. May be wrong. Never used for ordering. |
| `s` | Global sequence number, monotonic, never reused. |
| `src` | Subsystem: `nav`, `cam`, `video`, `link`, `sys`, `kern`, `power`, `ui`, `proc` |
| `lvl` | `debug`, `info`, `warn`, `error`, `fatal` |
| `e` | Event type — a short stable identifier, not a prose message |
| `d` | Payload object, schema per event type |

---

## 2. Time — get this right or the log is worthless

**The Pi has no RTC unless the DS3231 is fitted, and there is no NTP once isolated.** Wall clock
at boot is arbitrary, and it will jump when it is later set. A log ordered by wall clock will
appear to travel backwards in time in the middle of a dive.

**Requirements:**

- **`t` (monotonic) is the authoritative ordering key.** All analysis tooling sorts on `t`.
- On every clock adjustment, emit a `clock_step` event recording the delta:
  `{"e":"clock_step","d":{"from":"...","to":"...","delta_s":-3721.4,"source":"manual|rtc|ntp"}}`
  Post-processing uses these to re-derive absolute times retroactively for the whole session.
- At session start, log a `boot_info` event with boot ID, kernel version, uptime at logger start,
  whether an RTC was found, and whether the previous shutdown was clean.
- `s` (sequence) is the tiebreaker for events sharing a timestamp, and **gaps in `s` prove data
  loss** — the analysis tool must detect and report them.

---

## 3. Durability — the log must survive the failure it recorded

The main failure mode is abrupt power loss. A log that loses the final five seconds loses exactly
the part you need.

- Open with `O_APPEND`. Single writer process; all subsystems feed it.
- **Never block the control loop.** Producers push to a bounded in-memory queue and return
  immediately. A dedicated writer thread drains it.
- On queue overflow, **drop oldest, count the drops**, and emit a `log_overflow` event with the
  count as soon as there is room. Silent loss is unacceptable; dropped telemetry is survivable.
- `fsync` at most once per second (batched), plus immediately on any `error`/`fatal` event and on
  any operator `mark`.
- Mount the log directory on a **separate partition** from root. Run root read-only if practical —
  it removes the most common cause of SD corruption on power loss.
- Use `ext4` with `data=ordered`, or `f2fs` for better flash behaviour. Do not log to a tmpfs.
- Set `RemainAfterExit`/`Restart=always` in systemd and log the restart as a `proc_restart` event,
  including the previous exit code and signal.

---

## 4. What to log

"dmesg-level detail" means everything, at source rate, with raw values alongside derived ones.
When something goes wrong you need the inputs, not just the conclusions.

### 4.1 Navigation (`src: nav`) — 10 Hz, plus events

Log **raw and derived together**. A heading that drifts is only diagnosable if the raw
magnetometer is there beside it.

```json
{"e":"dr_step","d":{
  "quat":[w,x,y,z], "accel":[x,y,z], "gyro":[x,y,z], "mag":[x,y,z],
  "imu_cal":{"sys":3,"gyro":3,"accel":2,"mag":1},
  "hdg_deg":33.4, "pitch_deg":-2.1, "roll_deg":0.8,
  "press_mbar":1013.2, "depth_m":0.0, "temp_c":18.4,
  "throttle_pct":100, "steer_pct":0, "v_est_ms":0.94, "lut_id":"hull_a_v2",
  "x_m":12.4, "y_m":-3.1, "conf":0.82,
  "snapped":true, "snap_offset_m":1.7,
  "encoder_counts":10432, "payout_m":18.2
}}
```

Event-type records: `origin_set` (lat, lon, accuracy, source, heading0), `origin_adjust`,
`imu_cal_change`, `snap_lost`, `payout_clamp` (when dead reckoning exceeded tether length),
`dive_start`, `dive_end`.

**`imu_cal` is critical.** A magnetometer that quietly decalibrates near the thrusters produces a
heading that looks fine and is wrong. Log the calibration status every sample and alert on
degradation.

### 4.2 Camera (`src: cam`)

Every CGI call, with timing — the camera is single-threaded and its stalls explain video dropouts:

```json
{"e":"cgi","d":{"action":"set","prop":"Camera.Menu.UIMode","value":"VIDEO",
  "code":"0","status":"OK","dur_ms":1120,"body":"..."}}
```

Also: `rtsp_connect`, `rtsp_disconnect`, `warning_msg` (whenever
`Camera.Preview.MJPEG.WarningMSG` changes — `NO CARD!` and similar), `record_state_change` (with
both the commanded toggle and the polled confirmation), `cam_battery`, `sd_state`,
`capture_remaining`, `circuit_breaker_open`/`_close`.

### 4.3 Video (`src: video`) — 1 Hz

WebRTC stats: bitrate, framerate, `framesDropped`, `freezeCount`, `totalFreezesDuration`, jitter,
packet loss, RTT, keyframe requests, decoder errors. Plus `go2rtc` producer/consumer state changes.

A frozen picture with healthy stats and a frozen picture with rising `freezeCount` are different
faults; only the log distinguishes them.

### 4.4 Link (`src: link`) — 1 Hz

Tether health: RTT to topside, packet loss, and **interface counters** from
`/sys/class/net/{eth0,wlan0}/statistics/` — `rx_bytes`, `tx_bytes`, `rx_errors`, `tx_errors`,
`rx_dropped`, `collisions`. Also `wlan0` RSSI and link rate to the camera AP, since the camera
WiFi hop degrading is a real failure mode inside a sealed hull.

Events: `link_down`, `link_up`, `ws_disconnect`, `ws_reconnect`.

### 4.5 System (`src: sys`) — 1 Hz

```json
{"e":"sys","d":{
  "cpu_pct":34,"load":[0.8,0.6,0.5],"mem_used_mb":812,"swap_mb":0,
  "cpu_temp_c":62.4,"gpu_temp_c":61.1,
  "throttled":"0x50005","freq_mhz":1500,
  "disk_free_gb":12.4,"log_size_mb":48,
  "proc":{"go2rtc":{"cpu":18,"rss_mb":92},"navd":{"cpu":9,"rss_mb":41}}
}}
```

**`vcgencmd get_throttled` is mandatory and non-obvious.** In a sealed hull on battery,
undervoltage and thermal throttling are among the most likely causes of unexplained misbehaviour,
and they are invisible without this flag. Decode the bits into named booleans
(`under_voltage_now`, `throttled_now`, `under_voltage_since_boot`, ...) and emit a `warn` on any
transition.

### 4.6 Kernel (`src: kern`)

Stream `/dev/kmsg` continuously into the same log, tagged `src: kern`, preserving the kernel
facility, level, and monotonic timestamp. This captures USB resets, SD card I/O errors, WiFi
firmware crashes, OOM kills, and undervoltage messages — the failures that take down a service
without the service ever knowing why.

Read `/dev/kmsg` from position 0 at startup to capture the pre-existing ring buffer, then follow.

### 4.7 Power (`src: power`) — 1 Hz

Pack voltage, current, power, amp-hours consumed, per-cell voltages if a BMS is present, and
estimated remaining. Also camera battery percentage. **Log voltage under load and at rest** if the
hardware allows — a pack that sags under thruster load but reads fine at rest is a specific,
diagnosable fault.

### 4.8 Operator actions (`src: ui`)

Every command with its origin: throttle/steer inputs (downsampled to 10 Hz), record toggle,
capture, light and ballast changes, config writes, map expand/collapse, all-stop, surface command.
Include which input device produced it (gamepad, touch, keyboard).

**Add a `MARK` button to the dashboard.** One press writes `{"e":"mark","d":{"note":null}}` and
forces an `fsync`. When the operator notices something odd, this is how they find it later in a
seven-megabyte log.

### 4.9 Process lifecycle (`src: proc`)

Service start/stop/restart with exit codes and signals, uncaught exceptions with full stack traces,
config load with a hash of the effective config, version/git SHA of every component at startup, and
a `session_start` record capturing the complete resolved configuration.

---

## 5. Volume and rotation

At 10 Hz navigation plus 1 Hz everything else, expect roughly **2–3 KB/s, or 7–10 MB per hour**.
A four-hour session is under 50 MB. This is not a volume problem; do not downsample to save space.

- Rotate per session, not per size. A dive is the natural analysis unit.
- Also rotate at 512 MB within a session as a safety valve, with a `..._part002.jsonl` suffix.
- Retain by total size (default 4 GB) — delete oldest sessions first, **never the current one**.
- At 90% disk, emit a `warn` and surface it in the status strip; at 95%, stop logging `debug` level
  but keep everything else. Never stop logging errors.
- Optionally gzip archived sessions; JSONL compresses roughly 10:1.

---

## 6. Analysis tooling

A log nobody can read is not a blackbox. Ship these:

**`rovlog` CLI:**
- `rovlog tail [--src nav] [--lvl warn]` — live follow with filtering
- `rovlog range --from <t> --to <t>` — extract a window around an incident
- `rovlog check <file>` — validate: sequence gaps, dropped-event counts, clock steps, unclean
  shutdowns, throttling events. Run this first on any incident.
- `rovlog export <file> --format csv|parquet --src nav` — flatten telemetry for plotting
- `rovlog track <file> --format geojson` — extract the dive track for map overlay

**Incident bundle:** one command producing a zip with the session log, the effective config,
`dmesg` at time of failure, service journals, the dive track GeoJSON, and a summary of detected
anomalies.

**Replay into the simulator.** The navigation simulator from the earlier spec should accept a
recorded session as input and replay the sensor stream. This closes the loop: reproduce the
failure on the bench, fix it, verify against the same data.

---

## 7. Two additions worth making while you are here

**Leak and humidity sensing.** For a submarine this is the single highest-value thing to log and
it is not yet in the system. A £3 water-detection board in the bilge plus a DHT22 for hull
humidity gives you `src: sys` events for `leak_detected` and rising internal humidity — which
often precedes a leak as pressure cycling draws in damp air. Log at 1 Hz, alarm immediately, and
consider auto-surface on leak.

**Hardware watchdog.** Enable the Pi's watchdog timer. On reset, the next session's `boot_info`
records that the previous shutdown was unclean and watchdog-triggered — which distinguishes a
software hang from a power failure, and those have completely different fixes.

---

## 8. Acceptance criteria

1. Pulling power mid-dive loses at most one second of log; the file remains valid JSONL and the
   final seconds before the cut are present.
2. `rovlog check` detects and reports sequence gaps, clock steps, and dropped events.
3. Logging never blocks the control loop — verify throttle response latency is unchanged with the
   logger writing at full rate, and with the disk artificially slowed.
4. Queue overflow drops oldest and reports the count; it never blocks or crashes a producer.
5. `/dev/kmsg` content appears interleaved in the same file with correct monotonic timestamps.
6. `vcgencmd get_throttled` transitions produce a `warn` and appear in the status strip.
7. A manual clock change mid-session produces a `clock_step`, and post-processing correctly
   re-derives absolute times for events logged before it.
8. `MARK` writes immediately and is findable in under a second.
9. A recorded session replays through the simulator and reproduces the same dead-reckoning output.
10. Disk-full behaviour degrades as specified and never loses error-level events.
