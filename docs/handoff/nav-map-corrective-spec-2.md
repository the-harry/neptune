# Corrective spec 2: radar-integrated map

The previous round fixed layout priority correctly — camera feed is now primary, `ORIGIN: NOT
SET` appears in the status strip, and the map has a real `LOAD OR DOWNLOAD` empty state. Keep
all of that.

**One thing is wrong: the mini-map was built as a second, separate rectangular panel.** It
should not exist as its own element at all. This document describes the change.

---

## 1. Current state

- Camera feed fills the viewport (correct — showing `NO FEED / NO BACKEND` because the backend
  is unreachable).
- Top status strip renders correctly, including the new `ORIGIN — NOT SET` field in warning
  magenta.
- Right rail renders correctly.
- **Bottom-left:** the circular heading/throttle instrument — a cyan ring with a heading needle,
  `FWD`/`REV` markings, and `THROTTLE` / `STEER` readouts alongside.
- **Bottom-right:** a large rectangular map panel showing `NO MAP AREA LOADED` and a
  `LOAD OR DOWNLOAD` button.

Two separate instruments occupy two corners, and the rectangular one is far too large for a
secondary view.

---

## 2. The change: one circular instrument

**Delete the rectangular map panel. Render the map inside the existing circular dial.**

The bottom-left instrument becomes a GTA-style radar: a compact circle containing the live
basemap, with the existing heading and throttle indication drawn on top of it. There is exactly
one map instance in the application, and in its collapsed state it lives inside that circle.

### Collapsed (default) state

- **Circle diameter ~180–220 px**, in its current bottom-left position. Roughly the size of the
  existing dial — it must stay compact. It is a glance instrument, not a panel.
- The map is **clipped to a circle** (CSS `border-radius: 50%` + `overflow: hidden`, or an SVG
  clip path). No square corners bleeding outside the ring.
- The existing cyan ring, heading needle, and `FWD`/`REV` markings render **on top of** the map
  as an overlay, keeping their current appearance.
- `THROTTLE` and `STEER` readouts stay exactly where they are, outside the circle to its right.
- The scale bar (`10 m`) stays below the circle and must update with zoom level.
- **Heading-up orientation by default** — the map rotates under a fixed forward-pointing marker,
  as in GTA. Provide a north indicator on the ring, and a setting to switch to north-up.
- The sub position is fixed at the circle's centre; the map moves beneath it.
- Dive track renders as a trail behind the marker.
- **No labels or minimal labels** at this size — street names are illegible in a 200 px circle
  and add render cost. Use a simplified style: water, major ways, and the waterway centreline
  only.

### Empty state, compact

The current `NO MAP AREA LOADED` + `LOAD OR DOWNLOAD` treatment is correct in substance but far
too large. Inside the circle, show a short label only — `NO MAP` — and make the whole circle
clickable to open the area manager. The full explanatory empty state belongs in the expanded
view, not the radar.

Same for origin: with no origin set, draw no position marker. The circle shows `NO ORIGIN` and
clicking opens the origin flow.

---

## 3. Expanded state (click to zoom, GTA-style)

Clicking the radar expands the map to fullscreen.

- Animate the circle expanding into the full viewport. Circular clip can relax to rectangular
  during the transition.
- **Call `map.resize()` on `transitionend`, not at transition start** — otherwise half-grey render.
- Same single map instance. Do not create a second one.
- In the expanded view, restore full labels, full styling, and interaction: pan, zoom, draw-bbox,
  origin tap, area manager, dive log browsing.
- **The camera feed shrinks to a corner panel and keeps playing.** Do not unmount or re-create the
  video element and do not tear down the WebRTC peer connection — reparent or resize it. A
  reconnect costs seconds.
- Collapse via clicking the video panel, an explicit close control, or `Esc`.

### "Pause" — read this before implementing

GTA pauses the world when the map opens. **A submarine cannot be paused.** It keeps moving,
drifting, and consuming battery while the operator reads the map, and the operator is no longer
watching the video.

Implement the safe analogue instead:

- On expanding the map, **issue an all-stop**: command throttle to zero and hold. Show
  `ALL STOP — MAP OPEN` prominently in the expanded view.
- **Telemetry, video, recording, and all safety indicators continue running at full rate.** Never
  pause, throttle, or unsubscribe from the telemetry stream. The `SURFACE` warning and depth
  readouts must remain live and visible in the expanded view.
- **Piloting controls remain live.** Any gamepad thrust or steer input immediately collapses the
  map back to camera-primary and returns control. The operator must never have to find a close
  button in order to drive.
- Make the all-stop behaviour configurable (default on) — some operators will want to keep station
  under power in current.

Do not implement anything that suspends state, buffers telemetry for later, or freezes the display.

---

## 4. Rendering constraints

The radar is always on screen and must not compete with the video.

- Throttle radar redraws to **10 Hz**, decoupled from the telemetry rate.
- Use a **simplified map style** in the collapsed state — no labels, reduced layers. Switch to the
  full style on expand.
- **Decimate the dive track** rather than growing the polyline unbounded. Keep full resolution in
  the log; downsample for display.
- Verify WebRTC frame rate is unchanged with the radar rendering live. If the radar costs frames,
  reduce its update rate before anything else.
- Map rotation for heading-up must use the map's own bearing property, not a CSS transform on the
  container — a CSS-rotated square canvas inside a circular clip leaves uncovered corners as it turns.

---

## 5. Preserved behaviour

Unchanged from the previous round, and still required:

- Camera feed is primary in the default state.
- Top status strip fields, layout, and styling — including `ORIGIN` with accuracy.
- Right rail: record, PIC, SURFACE, LIGHTS, BALLAST, zoom, CAM, CONFIG.
- `THROTTLE` / `STEER` readouts and the heading needle.
- Neon-on-blue visual style.
- Map never captures gamepad or keyboard piloting input (`keyboard: false`, handlers scoped to
  the map container).
- Map failure degrades to a blank circle with the heading needle and throttle readouts intact —
  never a blank screen, never loss of video or control.
- Origin acquisition (phone GNSS / map tap / manual), area management, and all empty states from
  the previous spec.

---

## 6. Acceptance criteria

1. There is **no rectangular map panel** anywhere in the UI.
2. The bottom-left circular instrument contains live basemap geometry, clipped to a circle, with
   the heading needle and ring drawn over it.
3. The circle is ~180–220 px — it does not grow to panel size.
4. `THROTTLE` and `STEER` readouts are unmoved from their current position.
5. The map rotates with heading, with no uncovered corners at any rotation angle.
6. Clicking the radar expands to fullscreen; the video shrinks to a corner and keeps playing with
   no WebRTC reconnect.
7. Expanding issues an all-stop and displays `ALL STOP — MAP OPEN`.
8. Depth, `SURFACE` warning, and all telemetry remain live and visible while expanded.
9. Gamepad input while expanded immediately collapses the map and returns control.
10. `Esc` and the close control also collapse.
11. No grey half-render after expanding.
12. With no area loaded, the circle shows a compact `NO MAP` and is clickable to the area manager.
13. With no origin set, no position marker is drawn.
14. Video frame rate is unchanged with the radar live versus the radar hidden.
