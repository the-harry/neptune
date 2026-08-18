# Blackbox spec addendum: two-sided logging

Extends the blackbox flight recorder spec. Adds a **client-side recorder** on the topside
dashboard that logs the same events the Pi logs, so the two can be compared afterwards.

**The point is not redundancy — it is differencing.** Two independent records of the same events
let you locate a fault that neither log can pin down alone:

| Symptom | Pi log alone | Both logs compared |
|---|---|---|
| Operator says "it didn't respond" | command applied, looks fine | command never left the client / never arrived / ack lost |
| Video froze | encoder healthy | receiver-side freeze — the tether, not the camera |
| Telemetry gap | sent continuously | client received 987 of 1000 frames |
| Bad manoeuvre | throttle went to 100% | the operator saw stale data and acted correctly on it |

That last row is the one that matters most. The Pi records what was true; the client records
**what the operator could see**. Reconstructing a bad decision needs both.

---

## 1. Files

```
/var/log/rov/
  navigation_20260804T003502Z.jsonl     ← Pi side (existing)
  client_20260804T003502Z.jsonl         ← uploaded from the topside client
  current.jsonl → ...
```

Same session ID and same filename stamp on both. **Keep them as separate files.** Client events
arrive late, batched, and out of order; interleaving them inline would corrupt the Pi log's
ordering guarantees. Merge at analysis time, not write time.

### Session binding

On connect, the client requests the session from the Pi and adopts it:

```
GET /api/session → {"session_id":"20260804T003502Z","pi_boot_id":"...","pi_t_mono":8123.4}
```

If the client connects to a Pi that has rebooted, it starts a new client file. A client
reconnecting to the same session appends to the same one.

---

## 2. Clock alignment — the hard part

The client and Pi have independent, unsynchronised clocks. Without correcting for this, comparing
timestamps across the two logs produces garbage, and the errors will look like real latency.

**Run a continuous SNTP-style exchange over the existing WebSocket**, once every 5 seconds:

```
client → {"e":"ping","t1":<client_mono>}
server → {"e":"pong","t1":<echoed>,"t2":<pi_mono_recv>,"t3":<pi_mono_send>}
client   t4 = <client_mono_recv>

rtt    = (t4 - t1) - (t3 - t2)
offset = ((t2 - t1) + (t3 - t4)) / 2
```

Both sides log the result:

```json
{"e":"clock_sync","d":{"rtt_ms":3.2,"offset_ms":-1284.7,"samples":8,"jitter_ms":0.4}}
```

- Every client record carries `t` in the **client's own monotonic time**, never a corrected value.
  Correction happens in analysis, using the logged offsets. Never rewrite timestamps at write
  time — if the offset estimate is wrong you have destroyed the raw data.
- Log offset drift over the session. A drifting offset is itself diagnostic.
- `rtt` doubles as the tether latency measurement for the `LINK` field in the status strip.

---

## 3. Correlation IDs

Every command gets a UUID at the moment of operator intent, carried through every hop, logged by
both sides at every stage.

```
c_id: "9f3a..."
```

Stages, each a separate logged event:

| Stage | Side | Event |
|---|---|---|
| Operator input registered | client | `cmd_intent` |
| Serialised and handed to the socket | client | `cmd_send` |
| Received by API | Pi | `cmd_recv` |
| Validated / rejected | Pi | `cmd_validate` |
| Applied to hardware | Pi | `cmd_apply` |
| Result acknowledged | Pi | `cmd_ack_send` |
| Ack received | client | `cmd_ack_recv` |
| Effect observed in telemetry | client | `cmd_confirm` |

A missing stage localises the fault precisely. `cmd_send` with no `cmd_recv` is a link loss
outbound; `cmd_ack_send` with no `cmd_ack_recv` is a loss inbound; `cmd_apply` with no
`cmd_confirm` is hardware that accepted a command and did not do it.

The same `c_id` applies to camera CGI calls — so a UI button press ties all the way through to the
`Config.cgi` request and its 2256 ms stall.

---

## 4. What the client logs

### 4.1 Uniquely client-side (not visible to the Pi)

- **WebRTC receiver stats**, 1 Hz: `framesDecoded`, `framesDropped`, `freezeCount`,
  `totalFreezesDuration`, `jitterBufferDelay`, `packetsLost`, `nackCount`, `pliCount`,
  `bytesReceived`. The Pi only sees the sender side; a sender-healthy / receiver-frozen mismatch
  is the signature of a tether problem.
- **Actual rendered frame rate** and dropped animation frames — what the operator's eyes got.
- **Raw gamepad state**, 10 Hz: axes, buttons, and which device. Distinguishes "operator didn't
  push the stick" from "stick input was lost".
- **Browser events**: `visibilitychange`, `online`/`offline`, `beforeunload`, focus loss,
  `unhandledrejection`, `window.onerror` with stack traces.
- **Performance**: JS heap size, long tasks over 50 ms, page memory pressure.
- **UI state**: map expanded/collapsed, which panel had focus, all-stop triggered, `MARK` presses.
- **Environment** at session start: user agent, screen size, device pixel ratio, browser version,
  app git SHA, whether served over HTTPS.

### 4.2 The overlap set — logged by both, deliberately

This is where the value is. Both sides record their view of:

- Telemetry frames: Pi logs sent, client logs received.
- Commands: full lifecycle per §3.
- Connection state: `ws_connect`, `ws_disconnect`, `ws_reconnect`, with reason codes.
- Video stream state: Pi logs producer state, client logs consumer state.
- Origin set, dive start/end, record toggle.

**Do not duplicate full telemetry payloads client-side.** At 10 Hz that doubles volume for no
gain. Instead the client logs **received sequence ranges** compactly:

```json
{"e":"tlm_rx","d":{"seq_from":48200,"seq_to":48299,"n":97,"gaps":[[48241,48243]],"max_age_ms":118}}
```

One line per 100 frames. Gaps are detectable, volume stays trivial, and full payloads are logged
only for anomalies — a frame that failed to parse, or one arriving more than 500 ms stale.

`max_age_ms` — the age of the newest telemetry at render time — is the "was the operator looking
at stale data" measurement. Log it, and surface it in the HUD when it exceeds a threshold.

---

## 5. Client-side durability

The client log must survive the failure it is recording, including the link going down. The
browser cannot write to disk directly.

- **Ring buffer in IndexedDB**, not `localStorage` — the 5 MB quota is too small and the API is
  synchronous. Target a 50 MB cap, oldest-out.
- Write to IndexedDB immediately; upload separately and asynchronously. An event is durable the
  moment it is stored, not when it is uploaded.
- **Batch upload** to the Pi every 5 seconds or 200 events, whichever first:
  `POST /api/clientlog` with `{session_id, records: [...]}`. Pi appends verbatim to
  `client_<session>.jsonl`.
- Delete from IndexedDB only after the Pi confirms the write.
- **On reconnect, flush the backlog first**, before resuming live upload. The events recorded
  during a link outage are the most valuable ones in the file.
- Tag every uploaded record with `up_lag_ms` — how long it sat before upload — so analysis knows
  which records crossed a degraded link.
- **Manual export button.** If the link never returns, the operator must be able to download the
  client log as a file from the browser. Put it in `CONFIG`.
- On `beforeunload`, attempt a final flush via `navigator.sendBeacon`.

### Backpressure

If upload fails repeatedly, keep recording locally and stop retrying at full rate — exponential
backoff to a 30 s ceiling. Never let log upload compete with telemetry or video for tether
bandwidth. Cap upload bandwidth explicitly (default 64 kbps) and log when the cap is hit.

---

## 6. Analysis: merge and diverge

Extend the `rovlog` CLI:

**`rovlog merge <nav.jsonl> <client.jsonl>`** — produces a single time-aligned stream. Applies the
logged clock offsets, interpolating between `clock_sync` samples. Emits records tagged with side.
Flags any window where the offset estimate is unreliable (high jitter, few samples) rather than
silently aligning badly.

**`rovlog diverge <session>`** — the payoff command. Reports:

- **Lost commands**: `cmd_send` with no matching `cmd_recv`, and vice versa.
- **Lost telemetry**: Pi `seq` ranges sent versus client ranges received; total loss count and
  worst contiguous gap.
- **Latency distribution** per stage: intent → apply, apply → ack, apply → confirm. p50/p95/max.
- **Staleness**: distribution of `max_age_ms`, and every window where it exceeded threshold.
- **Video divergence**: sender frames encoded versus receiver frames decoded, freeze correlation.
- **One-sided outages**: periods where one side logged nothing.
- **Clock anomalies**: offset jumps, drift rate, sync gaps.

**`rovlog timeline <session> --around <t> --window 30`** — a merged, side-by-side text timeline
around an incident. This is what you actually read after a bad dive.

**`rovlog bundle <session>`** — incident zip: both logs, merge output, diverge report, config,
kernel log, dive track GeoJSON.

---

## 7. Acceptance criteria

1. Killing the tether mid-dive and restoring it: the client log contains the outage period in full,
   uploaded on reconnect, with `up_lag_ms` reflecting the delay.
2. Closing the browser tab abruptly loses at most the last few events; IndexedDB contents survive
   and upload on next connect.
3. `rovlog diverge` correctly identifies a command dropped by artificially blocking one direction.
4. `rovlog diverge` correctly counts telemetry frames dropped by artificially throttling the link.
5. Clock offset is tracked continuously; a deliberate 10 s clock change on either side is detected
   and correctly compensated in `rovlog merge`.
6. Client timestamps in the raw file are never rewritten — verify by inspection.
7. Log upload never exceeds its bandwidth cap and never degrades video frame rate or command
   latency under a constrained link.
8. Manual export produces a valid JSONL file with the link fully down.
9. Every operator action is traceable end to end by `c_id` through all eight stages.
10. A full session merges into a single ordered timeline with no duplicate or out-of-order records.
