# NEPTUNE — the design playbook

How this console **speaks**. One document for the presentation contract: the states a
reading can be in and the one look each state has, the colours and what they are allowed to
mean, the words on the glyphs, and the rules a new readout or map layer must follow before
it ships.

This is the canonical copy. `.specs/design.md` explains the *mechanisms* that produce these
states and cites this file for how they look; `docs/hardware.md` describes the chips whose
deaths these marks report; the READMEs point here instead of restating. If a rule about how
something is *shown* appears anywhere else, this file wins and the other copy is a defect.

Unlike `docs/hardware.md` (which mirrors code and forbids aspiration), the playbook is
allowed to **reserve** a decision before the code lands — reserved entries are marked as
such, so nothing here silently pretends to be shipped.

---

## 1. The prime directive

**Tell the operator the truth, and prefer cannot-tell over a plausible guess.**

Every subsystem below the client is an instrument, instruments fail as a steady state, and
the console's one unforgivable failure is looking healthy while hiding the fault it exists
to show. The full liveness doctrine — four review rounds of it — is `.specs/design.md` §24
and the normative acceptance criteria are `requirements.md` R7.6. What presentation owes it:

- **"Absent" includes "was here and stopped."** The sensor that worked for four minutes and
  then froze is the one that leaves a number behind. The last number is **never** shown —
  a frozen reading and a steady one look identical.
- **A default that is itself a valid reading is not a cannot-tell.** `0.0` heading is due
  north; `0.0` depth is the surface; leak `NORMAL` is a positive claim the hull is dry.
  The test is not "is this default harmless?" but *"if an operator read it as a
  measurement, would they act on it?"*
- **A subsystem's death must never look like good news.** A dying nav must not clear the
  snag warning; a silent leak sampler must not draw the dry-hull glyph.
- **Shape before colour.** Every state change alters a shape, mark or texture — never
  colour alone — so it survives sunlight, a cracked screen, and colourblind eyes.

### The state ladder

Every reading on this console is in exactly one of five states, and each state has exactly
one look, everywhere:

| State | Meaning | The one look | Operator's next move |
|---|---|---|---|
| **MEASURED** | a sensor is reporting | the number, tinted by its band | nothing |
| **ESTIMATED** | derived, not measured | the number wearing a visible tag (`~`, `EST`) | know it is a guess |
| **STALE** | a gap on a still-open socket | `--`, dim — the whole bar dashes together | nothing; it returns by itself |
| **CANNOT-TELL** | the sensor has stopped answering | `?`, amber, **wavy underline**, + an alert chip naming the part | waiting will not help; go and look |
| **ABSENT** | the part has never answered on this hull | `—`, grey — one unbroken rule, no `?`, no amber, **no fault chip** | nothing; this vehicle does not have it yet |

STALE and CANNOT-TELL differ **by construction**: a dash reads as a dropped frame that will
come back; a dead sensor dressed as a dropped frame is a sub flown on a number nobody is
taking. ABSENT and CANNOT-TELL differ because *absent is not null*: a hull that never sent
`current_a` must not be told its current sense "stopped".

**ABSENT is the state most of a half-built vehicle is in, and it is the whole reason the
distinction is worth a wire field.** Instruments are fitted to this boat one at a time, so
for weeks at a stretch the normal condition of most of them is "not wired yet" — and a
console that draws every one of them as a part that has just broken hands its operator four
errands that cannot be run, on a rail whose whole job is to be believed the day the flood
chip appears. The vehicle is the only layer that can tell the two apart and it says so:
`sensor_faults` names what is not answering, `sensors_absent` names which of those have
never answered at all (`api/protocol.py`; the verdict is `DeviceHealth.answered_ever`). A
hull that does not send the field, and the bench, land on CANNOT-TELL exactly as before —
**the loud state is the safe one to be wrong with**, because calling a real failure "never
fitted" hides it while calling an absence a failure costs only a wasted look.

**Only leaf parts are ever called absent**, and that is the same rule read again. An I2C
bus that would not open is not an unfitted instrument, it is an errand with a fix — and it
stands *behind* every chip on it, so calling it absent would silence the one name that
explains three quiet gauges at once. A reading goes ABSENT only when **every** part behind
it was never fitted; one bus fault on the path and the whole reading is CANNOT-TELL again,
loudly.

Absence buys **silence and nothing else**. Everything named absent is still named in
`sensor_faults`, every reading behind it is still null, and no number may ever appear
because a sensor is merely missing rather than broken — that is the same forbidden
cannot-tell default arrived at from the other side.

---

## 2. The vocabulary of states

The single table other docs cite. A mark used here may never be reused to mean anything
else.

### Marks on a reading

| Mark | Means |
|---|---|
| `?` amber, wavy underline | cannot-tell — the sensor stopped answering (also: ballast never homed / lost count — genuinely-not-known has one word) |
| `--` dim | stale — dropped frame on a live socket; returns by itself |
| `—` grey, no underline, no glow | **absent** — this hull has never had the instrument. It is not the stale `--` (a *pair* of dashes, arriving on the whole bar at once and leaving by itself) and not the `?` (amber, wavy, and an errand attached): one long unbroken rule, on one reading, while everything around it reads normally, and it stays until somebody fits the part. The tooltip names the part and says plainly that nothing has stopped |
| `~` + `EST` tag | estimated — the figure came from the throttle curve, not the paddlewheel |
| dotted underline (HDG) | compass answered and reports itself **uncalibrated** — there is a bearing; it is suspect |
| dashed underline (HDG) | the filter is ignoring the compass **on purpose** (gyro coast) |
| number tinted by band | measured, and the tint is a statement about the **sensor**, never about the link |

### Badges and chips

| Badge | Means | Why it is not its neighbours |
|---|---|---|
| *(blank)* | compass calibrated and in use | — |
| `MAG?` | compass uncalibrated | there IS a bearing; it is suspect |
| `GYRO` | compass ignored deliberately (thrust swamping it) | deliberate, not broken |
| `NO COMPASS` | no IMU has **ever** answered on this hull — ABSENT | nothing to calibrate or come back to, and **nothing to go and look at**: no fault chip is raised, and the bearing wears the absent rule rather than the cannot-tell `?` |
| `NO BEARING` | an IMU answered this dive and has **stopped** | no bearing at all, not even a bad one — and this one *is* an errand, with a chip naming the compass |
| `RAW COMPASS` | the estimator stopped; bearing fell back to the unfiltered compass | nothing is judging it — or watching for a snag |
| `SIMULATED · NO DATA FROM THE SUB` | the console's own model is producing every reading | clears itself when a genuine frame arrives |
| `SNAGGED` chip | high thrust, no measured speed, sustained | the shopping-trolley detector; LUT speed never counts as evidence |
| `NO SPEED` / `EST` (speed source) | nothing reports speed / the figure is the throttle curve | blank means the wheel measured it |
| `NOT FITTED` on an instrument-group head | every blank reading in that folded group is behind a part this hull has never had | it wears the badge's **plain** form, not the amber one — a group head is the only mark a folded cluster still shows, so four blanks may not hide, but a permanently-amber head from power-on until the part arrives is a mark nobody reads |
| alert chip naming the part | `DEPTH — MS5837 NOT ANSWERING` | a blank gauge with no cause reads as a dashboard glitch; a name is an errand — **and therefore never raised for a part the hull calls absent**, because an errand nobody can run teaches the operator to stop reading the rail |

### The leak ladder — four drops

`NORMAL` is a positive claim and must never be a fallback; anything this console has never
heard of lands on UNKNOWN.

| Stage | Drop glyph | Means |
|---|---|---|
| `NORMAL` | green, struck through | both probes sampled, both dry |
| `UNKNOWN` | amber, **broken outline**, `?` | nobody is sampling the probes |
| `WARN` | amber, half-filled | the low probe is wet — advisory; finish up |
| `FLOOD` | red, pulsing sub glyph + SURFACE prompt | water 2 cm up — come up now |

Wet outranks cannot-tell; UNKNOWN outranks NORMAL.

### The connection glyphs

One icon per link the operator can actually lose; nothing reports on weaker than direct
evidence (a socket in `connecting` is not evidence). Mechanism and evidence sources:
`.specs/design.md` §18.

**WI-FI** — never in the path of driving the sub:

| Glyph | State |
|---|---|
| arcs, green | joined *and* the network reaches the internet |
| arcs, amber steady | adapter present, joined to nothing |
| arcs, amber **blinking** | joined, but no internet — the two ambers differ by blink, never colour alone |
| arcs slashed, red | no wireless adapter at all |

**TETHER** — the shape answers "is there a vehicle", the colour answers "how is it":

| Glyph | State |
|---|---|
| sub, green | adapter + API + control link all up |
| sub, red **pulsing** | the sub is answering and reports a **leak** — a fault must never read as an absence |
| plug, amber blinking | API answers; control link not up yet |
| plug, amber steady | wired adapter present, nothing answering |
| cut cable, red | no wired adapter — the simulator is flying this |
| robot, red | no launcher; the adapters cannot be checked, and it says so |

**CAMERA** — two independent observers, because a dead Pi antenna and a dead camera look
identical from the Pi alone:

| Glyph state | Means |
|---|---|
| green | the Pi has the camera and is transmitting |
| amber | the Pi cannot see it but **this handheld** can see its access point |
| red | neither can see it |

### Chart layers (map data)

A layer is `present` / `off` / `absent` / `unavailable`, and the words are load-bearing:
**absent** means somebody was asked and said the data is not there; **CANNOT TELL**
(`unavailable`) means nobody could be asked at all. Off means not asked — but an absence
already on record stays a fact. A console that has simply never downloaded chart data is
**quiet and informative in the layer panel**, not alarmed on the map: the loud CANNOT TELL
is reserved for *asked-and-failed* and *had-it-and-lost-the-Pi*. Never a quietly empty map.

---

## 3. The colour language

**One colour, one meaning, forever.** A new piece of work claims an unused colour or reuses
an existing meaning exactly; repainting a survivor because the series count changed is
forbidden.

| Colour | Reserved meaning |
|---|---|
| the twelve depth bands (orange at the surface → purple at the bottom, `6+ m` clamp band) | **how deep** — and nothing else. Worn by the dive track, the ballast fill, the Depth/Pressure readouts and the depth cells, so "how deep" is one visual language learned once |
| amber | suspect / advisory / uncalibrated / cannot-tell — the "look closer" colour |
| red | fault demanding action; **pulsing red is reserved for a leak** |
| green | proven good on direct evidence — never "probably fine" |
| dim / grey | stale, or no claim — grey on a depth section means *no published figure*, not shallow; and an **absent** readout wears it because a hull that has never had the instrument is making no claim at all. Grey is reused here exactly, never repainted: what separates absent from stale is the **shape** (one rule, not two dashes) and the opacity (absent is not dimmed — it is not going anywhere) |
| cyan glow (`glow-cyan`) | the measured-depth readout's own ink |

**Who may wear the depth colours differs by mode, and that is the point**
(mechanism: `.specs/design.md` §19): in SIM one made-up number drives everything, so
everything wears one colour together; on a real dive, depth and pressure are coloured by
**their own sensor or not at all** — never tinted from ballast. An unchanging cyan number
beside a purple tank IS the alarm.

**Battery bands** (thresholds in `.specs/design.md` §22): colour comes only from the bands,
and the voltage number is always beside it — a band is a judgement, the number is the
measurement.

**Texture outranks hue for provenance.** Published-vs-measured is carried by texture:
NOMINAL depth is washed and **hatched** with a dashed thread; SURVEYED is **solid and
outlined**. Colour says how deep; texture says how much the number is worth.

**The LIDAR bank layer** wears amber `#E39A2E` (bank under 2 m above the local water level
— candidate launch zone, a geometric fact and not a promise of access) and earth-brown
`#453016` (higher bank / urban fabric), under the rule that **water pixels are never
painted** by elevation data. Those two hues belong to that layer now.

---

## 4. The glyph contract

Every glyph, number and control carries a **written explanation** in `title` and
`aria-label` — a real sentence, not a caption:

- It explains what the thing **means for this vehicle**, not what it is called. A culvert
  mark says *a place the tether must never follow the sub into*; a weir mark says *expect
  current* — and says that this is an inference from where the structures are, because
  nobody publishes flow data.
- It names the states the element can be in and what each looks like, so the tooltip is the
  manual.
- The explanation is captured into `data-help` at boot, because live-state renderers also
  write `title` and whoever writes last used to win — which quietly erased the explanations
  seconds after launch.
- The `demo-mode` suite enforces the contract: explanation present, a real sentence, and
  state carried by **shape as well as colour**.

The writing voice, everywhere on screen: plain words, no jargon, no apology. A control says
exactly what happens (`HOME THE BALLAST — drives the syringe slowly down to its empty stop
and re-zeroes the step count`). A hazard control states its consequences in its own label
(`EMERGENCY SURFACE — … Can damage the craft, so use it on a leak, not for convenience`).

---

## 5. Interaction rules

- **Declared dependencies, per control.** Markup carries `data-needs="link|cam|…"` and only
  the owning subsystem gates it. Two kinds of unavailable, and they look different:
  **simulated** (tinted, *fully interactive* — the console must be flyable on the bench)
  and **gated** (disabled, only where pretending would be a lie — REC with no camera).
  PIC is deliberately never gated.
- **Hazard actions are HOLD-to-fire**, with the consequence in the label. A tap must never
  be enough to blow ballast.
- **The expanded map is an all-stop; blind nav is not.** A submarine cannot be paused, so
  an open planning map means throttle zero — and therefore nothing may expand the map while
  blind nav is driving.
- **Overlays never capture piloting input.** The map, the log viewer and every HUD surface
  let stick and key input pass; a filter box stops its own keystrokes from reaching the
  helm and nothing else.
- **No retry buttons, no dialogs, no asking.** Degradation is automatic; the operator has a
  sub to fly.
- **Toggles persist** (layer switches, fold state) and restore on boot.
- **Hit targets are real.** Sized for wet thumbs on a handheld, and certified by asking the
  document what is at the control's centre — a synthetic `.click()` passes over a control
  that no finger can reach (this shipped once; the fold button was drawn, styled,
  aria-wired and unreachable).

---

## 6. Cartography rules

- **The map never draws a journey the sub did not make.** No synthesised position, heading
  or depth over a real link, ever; with no heading there is no track — the radar stays on
  the last angle the compass actually gave, marked as such.
- **Layer order is meaning.** Imagery → readability tint → reference grid → depth wash
  (nominal under surveyed, both under the vectors they give context to) → waterway
  centreline → chart marks → dive track and vehicle → HUD furniture. A wash laid over
  vectors would bury the very things it exists to contextualise.
- **Provenance wears texture** (§3): hatched-and-dashed for published claims, solid-and-
  outlined for what the sub itself measured. This session's own soundings are counted
  separately from the Pi's saved survey, so the two can never be confused.
- **Unmeasured water stays honest.** The satellite stays unaltered wherever nothing is
  measured; no pixel on the water may imply the system knows the depth (reserved rule for
  the LIDAR layer: elevation paints banks only, never water). Anywhere unsurveyed is drawn
  as unsurveyed — "no data" and "none here" are different sentences.
- **Tier-1 hazards cannot be switched off.** Locks, weirs, sluices, culverts, tunnel
  portals, outfalls: the marks a small tethered ROV dies by.
- **Attribution rides with the data.** The CRT OGL sentence and the imagery credit draw
  when their data draws — the licence wants the words wherever the data is used.
- **Live cells gate on measured telemetry.** A simulated dive must never paint
  measured-style survey cells (see the SIM badge rule: everything simulated says so).

---

## 7. Adding a new readout or map layer — the checklist

Walk every line; the vocabulary table (§2) and the colour table (§3) are the registries
being amended.

1. **Map it onto the state ladder** (§1): what does MEASURED / ESTIMATED / STALE /
   CANNOT-TELL / ABSENT each mean for this signal? If one is impossible, write down why.
2. **Verify the null survives the chain** — hardware → protocol → rov → nav → main →
   client ingest → render — and name an owner for every file on the path
   (`.specs/design.md` §24.2).
3. **Claim colour** in §3: an unused colour, or an existing meaning reused exactly.
4. **Choose marks** from §2; if a genuinely new mark is needed, add it to the table in the
   same change.
5. **Write the glyph text** per §4 — what it means for this vehicle, all states named.
6. **Wire the interaction** per §5 — `data-needs`, hold-to-fire if hazardous, persisted
   toggle if a layer.
7. **Respect the z-order and provenance texture** (§6) if it draws on the map.
8. **Test through the real ingest path** — feed frames, not state pokes; kill the sensor
   at the far end and assert what the *screen* shows; hit-test real coordinates.
9. **Re-bless the visual baselines deliberately** — drift is a finding, not a formality.
10. **Update the registries**: the §2/§3 tables here, the tooltip/demo explanations, and
    an appended entry in `.specs/tasks.md`. Counts are never written into prose — the
    runners print them.
