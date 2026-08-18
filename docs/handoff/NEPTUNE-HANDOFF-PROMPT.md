# NEPTUNE ROV — MASTER HANDOFF PROMPT
> Paste this at the start of any new conversation to fully restore project context.
> Last updated: 2026-08-18 (late — repo reconciliation session). ~£500 committed. All major hardware ordered.
> **This file's canonical home is now the software repo: `docs/handoff/` in github.com/the-harry/neptune.** The zip is the transport; edit the repo copy. See §20 for the software side.

## HOW TO USE THIS DOCUMENT (instructions to the agent)
You are picking up a long-running DIY RC submarine/ROV project called **NEPTUNE**, built by Harry (UK/London, backend dev, ADHD — wants terse, high-signal answers, bold headers, no filler). Everything below is settled unless marked OPEN. Do not re-litigate closed decisions unless new facts appear. When Harry reports progress (deliveries, bench tests, builds), update the relevant STATUS lines. Companion design documents already exist (list at bottom) — reference them, don't recreate them. Prices are what was paid. The project uses AliExpress/eBay/local shops; Harry screenshots listings for review — evaluate against the specs and principles here.

## 1 · MISSION & OPERATING DOCTRINE
- Tethered inspection/survey ROV for **UK canals and lakes** (1–3 m typical, design-good to ~10 m; hull good for 30 m+, tether is the real limit).
- Missions: camera survey, dead-reckoned mapping, bathymetry (sonar overlay on map).
- Topside = **ROG Ally** running browser dashboard. Camera feed fullscreen primary; map as **GTA-style circular radar** bottom-left (MapLibre, satellite imagery Esri World Imagery, live tiles when online, PMTiles offline).
- Flying doctrine: trim to neutral/slightly positive with ballast bag; **fly depth with thrusters+pitch**; bag = bias, bottom-parking = bag slightly full; drop weight = emergency only. **Every failure ends at the surface.**
- Safety rule (governs all design): anything whose failure makes the boat unrecoverable must fail toward "boat comes home slowly," never "state changes violently."

## 2 · HULL (CLOSED)
- **130 mm OD × 400 mm cast acrylic tube**, 5 mm wall (ID ~120, usable length ~350 after flanges). AQUROV £50.79.
- **Front: acrylic dome assembly 130 mm** (Zhifeng £69.79) — ships WITH integrated flange/ring/O-rings (verify on arrival). Camera lens goes at dome centre of curvature.
- **Rear: ROVWAKER 130 mm watertight flange £56.39 (qty 1 — correct, dome has its own)** + **DIY blank cap from 10 mm acrylic sheet** (Harry has sheet). Face-seal bolted disc; step-drill, washers under heads, no countersinks.
- Rear cap penetrations (see integration doc FIG3): 6 potted epoxy pigtails (thrusters ×2, lights, burn, leak-AFT, sensor spare), potted syringe→now pump tube, **PG7 gland for tether**, **M6 nylon vent screw + O-ring** (vacuum test port), **MS5837 potted gel-face-out**.
- Seal regime: silicone grease every O-ring every closure; inspect/wipe ritual; vacuum test 10 min before dives; desiccant sachet inside; overnight weighted bucket test with paper-towel telltale.
- Displacement ≈ 4.8 L → ~3 kg lead to carry.
- **RULE: drill the frame freely, drill the hull NEVER.** Hull carries nothing; rides in saddles.
- OPEN: message 3 hull sellers to confirm 130 mm cross-compatibility (ROVMAKER-standard 130×5 tube).

## 3 · FRAME & MOUNTING (CLOSED — simplified v1)
- **Single 32 mm waste-pipe rail spine** (B&Q, pending local buy) — everything clamps to it.
- Hull: **2 EVA yoga-block saddles** (blocks bought £2.73×2, knife-cut 130 arc, slightly undersize to compress) + **2× 150 mm jubilee straps** looping hull+saddle+rail, EVA pad under band. Straps over flange zones ~60 mm from ends, never mid-span. Witness mark tape across tube/saddle to spot creep. Re-tension after first cold dive.
- Thrusters: pods clip **directly to rail stern end**, 2× Ø60 rubber-lined P-clips per pod (4 total — cart bumped to 2×2pcs), M5+nyloc through drilled rail. Axes parallel to keel, props aft of cap plane. Optional crossbar retrofit if turning is lazy.
- Lead: **gym-stack system** — M6 304 threaded rod posts (300mm 2pcs bought £3.58, cut ~80 mm), slotted-hole lead plates (template-drilled), washer + **wing nut (10pcs M6 £2.11) + nyloc (20pcs £1.40) jam-locked**. Fixed stacks under rail at each saddle (~1 kg ea, LOW = self-righting); micro fore-aft trim via slotted holes.
- Fastener ladder: M2.5–M3 boards · M4 brackets/P-clip tabs · M5 structure · M6 kg-loads. All thruster-path fasteners nyloc'd or threadlocked (threadlock = pending local buy). 260pc washer kit £3.73, M2-M6 304 assortment box bought.
- External cabling: zip ties **through drilled rail holes** every 100–150 mm (green/purple 100pc packs bought — not UV-black, replace seasonally), service loops at every endpoint, chafe sleeves at clamp edges, route under rails. NO superglue wet (debonds). Tug test: pull any cable mid-span → force dies at an anchor, never at a pot.

## 4 · PROPULSION (CLOSED)
- **2× 390 brushed "underwater thruster" motors** (POBOTRAE £23.99 L+R pair with props): 7.4–8.4 V, 29 mm dia, **2.3 mm shaft**, 25 mm shaft length, ~12000 rpm no-load, waterproof gasket bulkhead discs included, CW/CCW props (left pushes fwd with CCW, right with CW).
- Run at **8 V via LM2596** (3pcs bought £1.82), NOT 11 V direct.
- Drivers: **DRV8871 ×2 (owned)** — current-limit at 3.6 A accepted for v1 (390 stall exceeds it; limit prevents damage, costs peak grunt). BTS7960 = upgrade path if needed.
- Props: kit's own 3-blade first (matched baseline); **D50 4-blade paddles bought (£2.70 pair)** as upgrade — need **2.3→4 mm brass grub-screw couplers (POBOTRAE £0.81 ×2 — CHECK if that's 2 pieces; want 4)**. Ritual: file flat on shaft, threadlock grubs.
- Shrouds/pods: **60.3 mm × 425 mm PVC pipe** (eBay £8.99, in basket w/ lead) → 2× 75 mm shroud rings; motor held by **acrylic X-mount strips** screwed to kit disc, X legs bolted/zip-tied to tube wall. Cylindrical guard only (no thrust bonus); printed OpenThruster Duct 37 + Kaplan = endgame (FreeCAD sources: github.com/DisCoLabIITK/OpenThruster, data.mendeley.com/datasets/yfg487zbkp/1). Interface standard: "60-70 mm cylinder in two P-clips" = any future pod swaps in.
- Thrust budget: drag ~3–6 N at 0.5 m/s; pods deliver comfortably more. Old 130-size motors retired.

## 5 · BALLAST (CLOSED — major evolution, syringe/stepper DELETED)
Three independent layers:
1. **Fixed lead ~2 kg** (gym stacks, adjusts at bathtub) → boat barely positive alone (+150 g).
2. **Drop weight 1–1.5 kg** at CG on **continuous-loop nylon bridle**: one mono loop over fore+aft frame pins, under weight bar, both ends to ONE tensioned tail through **single nichrome burn point** → level release, no pitch kick. Weight = bolted lead-slice stack (2× M5 stainless through-bolts = structure + bridle path; epoxy only as anti-rattle + paint seal). Convex seat/pins, NEVER a pocket (silt+suction).
3. **Trim vernier: 500 ml TPU soft flask** (NEWBOLER £2.49–2.50 ×2 bought) part-filled in printed **drip tray** on floor deck at CG (velcro, neck-up self-purging) + **12 V peristaltic pump** (£4.96–5.21, qty 1 — spare recommended, tube is a consumable ~1yr) + **YF-TM02 flow sensor inline** (£1.99 ×2 bought) counting ml. Pump = no wet seals, self-priming, pinch-valve = no backflow, 1–1.5 bar ≈ 10–15 m depth capable. ±250 g authority; system: lake ⇄ potted tube ⇄ pump ⇄ bag. 4.1 mm PP barbs bought (£1.51). Bag leak signature: leak-FWD + ballast_ml drift WITHOUT depth correlation (vs hull leak = depth-correlated) — blackbox can distinguish from the bank.
- Deleted: syringes, 15 mm linear stepper, A4988s (stepper re-tasks to camera tilt someday).
- mk2 pencilled: air-blow tank (external vessel + air pump + solenoid) = fast emergency blow; the drip-tray bay and reserved sled volume anticipate it.
- **The dive stack**: hull lift +5 kg → fixed lead −2 → drop −1..1.5 → bag ±0.25 → thrusters do the rest.

## 6 · POWER (CLOSED; PACK RECOVERY IS URGENT)
- **Pack: rebuilt ThinkPad T430 clone** — 9× INR18650 2.0 Ah (**6.0 Ah real**, label's 7800 lied), factory-welded 3S3P block kept intact, SMBus BMS removed. STATUS: measured deeply discharged (ladder 0/3.0/6.0/9.3 V — welds intact, groups near-balanced, 3.3 V group watch-listed), Kapton-wrapped. **Charge SOON — cells degrade below 3 V.**
- **New BMS: DollaTek 2pcs 3S 12V 40A "with equilibrium charging"** £5.99 (Amazon, arrived?) — common port, balanced. **Wire ascending: B−(black,0V) → B1(wobbly yellow,3.0) → B2(marker yellow,6.0) → B+(red,9.3)** — order violation kills the chip. Big pads want 18 AWG, tin separately. Balance leads = tape-labelled from multimeter test, NOT colour/marker trust.
- Charger: **adaptive brick ONLY IF it says exactly 12.6 V CC-CV** (12.0 V = 80% + not a charger) — else buy 12.6 V 2 A brick. Charger meets boat at the same XT60 (common port), one-plug-at-a-time.
- First charge: supervised, fireproof surface, ~0.5 A until >3.3 V/group, watch the high group converge (divergence = broken parallel weld → sag-test with car bulb).
- Distribution (integration doc FIG4): pack → BMS → **10 A main fuse (16 AWG inline, short leads)** → 50 A isolator (£3.46) → XT60 → INA219#1 → rails: LM2596→8 V→7.5 A fuse→INA219#2→DRV8871s · 5 V 3 A mini buck (£0.74)→3 A fuse→Pi · lights 11 V→5 A fuse→IRLZ44N ×2 · burn 11 V→5 A fuse→2×1R-series(=2R 20W)→ARM+FIRE gates · pump→IRLZ44N.
- Fuses: **180 pc micro+mini+standard box £5.20** + **CHANZON 5pc mini ATM inline holders £5.19 ×2** (10 holders: 5 boat + spares). Boat standard = mini.
- Resistors: cement 10-packs bought — **10W 1R** (burn: 2 in series = 2R/20W, ~2 A gentle profile), **5W 22R** (white strings), **5W 68R** (red string); **300 pc 1/4W 30-value kit £2.43** (220R gates, 10k pulldowns, 4.7k I²C, 10k+20k flow divider, NTC partner).
- Wire: 18 AWG 10 m £7.14 (backbone/thrusters/burn — cut thruster runs FIRST, full length + slack) · 22 AWG 10 m £3.26 (lights/pump/bucks) · 28 AWG (signals, earlier cart). IRLZ44N ×10 £1.45. NTC 10K 10pcs £1.12. XT60 5-pair £5.27 (**exactly enough, zero spare — 2nd pack = nice-to-have**). JST-XH pre-crimped kit £9.79 (also = LiPo balance standard).

## 7 · ELECTRONICS ARCHITECTURE (CLOSED — brainstem/commander split)
- **Pi = commander**: camera (wlan0 → WOLFANG AP 192.72.1.1), thruster DRV8871 PWM (GPIO 23/24, 5/6 — its ONLY GPIO use), nav solution, blackbox master, eth0 → tether → Ally. 
- **ESP32 WROOM-32 DevKit = brainstem** (2pcs £7.26 bought; C3s/8266s REJECTED — pins/PCNT/boot-glitch): connects to Pi by **USB only** (serial+power, 15 cm tied cable, udev pin by serial). Owns: I²C1 (21/22: BNO085+INT 19, MS5837, INA219 ×2) · leak ×3 (34/35/39 input-only) · NTC (36) · flow PCNT (27) · PAS quadrature (25/26) · pump LEDC (18) · white lamp LEDC 8 kHz (23) · red beacon (13) · **burn ARM (14) + FIRE (33) two-pin interlock** · = 15 of ~24 pins.
- **Reflexes live on ESP32** (work when Pi is dead): leak → beacon + surface flag; undervolt warn; burn fires only if ARM+FIRE + local state agree. Protocol: unprompted 10 Hz JSONL telemetry up; tiny command vocab down (pump_ml, trim_home, lamp, beacon, arm_burn/fire_burn) with sequence-number acks → joins blackbox c_id chain. ESP32 ring-buffer = third blackbox witness. ESP32 WiFi stays OFF.
- Ballast loop closes entirely on ESP32 (pump_ml:50 → PWM + count flow pulses → stop → ack measured).
- ESP32 #2 = flashed spare / future **sonar front-end** (RMT 200 kHz burst, I²S-ADC envelope) — second /dev/ttyESP on Pi.
- 8266 fleet = bench rig / topside monitor / pure witness ONLY (never gates/actuators — boot glitch).
- Boards on **brass standoffs** everywhere; driver board + power board stacked as **mezzanine** (20 mm standoffs, LM2596 fins up on top board). 10pcs PCB protoboard £1.80/£1.71.

## 8 · SLED (CLOSED — see sled-two-plates.png + neptune-3d-master.pdf FIG C/D)
- Two decks from Harry's 2× 300 mm acrylic plates: **solvent-weld (dichloromethane cement, Harry has it) 300+50 butt joint + doubler strip** = 350 mm; doubler doubles as Ø119 support rib; seam placed under static mounts, NOT under battery strap anchors. Ventilate, faces dead flat, no clamping pressure, solvent NEVER near hull.
- **Floor deck (low, ~35-40 mm below centreline)**: battery rear-biased strapped (BMS on rear face, NTC taped mid-pack) · 10 A fuse+isolator at pack output · ballast bag in drip tray fwd at CG · **acrylic partition rib** (= spill wall + support rib + seam doubler, vent notch, dressed tube/wire slot) · leak-FWD in tray, leak-MID at battery low point.
- **Mid deck (at centreline)**: **camera on rigid cantilever arm** fwd (lens at bore centre + dome CoC; set-and-forget tilt on slotted side-screw pivots; verify insertion sweep clears flange) · BNO085 at nose (max distance from motors/lead/pump) · Pi centre · ESP32 beside (USB) · driver+power mezzanine · pump+flow rear.
- **Connector bus bar** (10 mm acrylic, aft face, plugs point AFT — finger-only full disconnect with cap off): **XT60 ×3** (charge top-corner isolated / thruster P / thruster S — panel-mount XT60E/PW or epoxy-in; **colour-band the trio**, charger-into-thruster = the trap) · **JST-XH backplane** = perfboard strip with soldered shrouded headers on M3 standoffs (burn, leak-AFT, MS5837, lamp 3-pin W+/R+/GND, spares) · **RJ45 keystone** (tether). Service loop + zip-tie anchor behind every joint.
- Leak-AFT mounts to HULL (low point at cap), not sled. MS5837 at cap, I²C fwd via panel JST.
- Sled self-centres via Ø119 ribs; sits low; only the camera LENS has a mandatory coordinate (bore centre). Rattle test: roll hull on bench, no shift.
- Teardown target: **≤5 min to Pi-in-hand** (vent → cap → 7 plugs + charge → slide). If anything blocks, move it.

## 9 · LIGHTS (CLOSED)
- **Repurposed headlamp housing** (aimable, saddle-mounted, greased seals, switch bypassed; bucket-test overnight). Mounted top-forward. Driver board REMOVED → direct drive.
- **Dummy cells** feed it from main pack (battery bay = pass-through). Whites: series strings + 22R 5W each @~300 mA, **ESP32 LEDC 8 kHz PWM** (above camera banding), dashboard slider, **tied to camera AWB toggle** (AUTO/DAYLIGHT lit, INCANDESCENT dark). Reds: 3-series + 68R @100 mA, beacon blink 0.2 s/1.8 s in surface routine. 3 W star LEDs need metal backing; nail polish on tabs never lens.
- LiFePO4 headlamp cells (3.65 V/2 V labels — NOT Li-ion, never mix) → future **independent self-flashing emergency beacon** (astable/self-flash LED + pull-pin) = survives total main-pack death. Their charger ≠ Li-ion charger.

## 10 · SENSORS & NAV (CLOSED)
- BNO085 (£16.39) IMU · MS5837-30BA (£5.99–6.00) depth · INA219 ×2 (£1.01+£1.07/1.13) pack+thruster rail · leak 5-pack £1.64 (3 zones: FWD tray / MID battery / AFT cap — zone = which seal to suspect; 2-of-3 gates auto-surface) · NTC pack temp.
- **Speed log: YF-TM02 ram-flow** (2nd unit) external low on frame, printed intake horn (belled inlet, 20 mm straight throat), inlet ≥15 mm if trimmed, never cut into body taper, calibrate AFTER any trim. Floor spec UNVERIFIED (£1.99 gamble) — **bench test: blow/trickle test for 0.05 L/min floor**. Backup ordered: **PAS e-bike pedal ring** (£3.21–3.22) = 12 PPR ×4 quadrature + DIRECTION sensing (only sensor that knows backward-vs-forward) — **bench test for minimum-cadence gating before committing**.
- Speed processing: pigpio/PCNT edge timestamps, **period math not counting**, LUT fusion (v_est += (v_pulse−v_est)·α on pulse; decay to LUT between), stale timeout 2× expected interval. v_meas vs v_est divergence = current detector; zero-flow at high throttle = sensor fault flag not fact.
- Dead reckoning per nav spec: heading + speed LUT integration, NEVER double-integrate accel; MS5837 depth measured; tether-payout 3D clamp; canal centreline snapping (Shapely on Pi); phone GNSS origin capture (HTTPS).
- Compass calibration **in-water, motors+lead fitted** (steel studs/motors = hard iron).

## 11 · CAMERA & SOFTWARE (CLOSED — specs are law, in outputs docs)
- WOLFANG 4K action cam, AP 192.72.1.1, PWD 12345678, RTSP rtsp://192.72.1.1/liveRTSP/av4, CGI /cgi-bin/Config.cgi (single-threaded: one global lock, Connection:close, explicit timeouts; Video=record is a TOGGLE — poll status; slow ops ~1–2.2 s; never SD format during probing; defaults: PowerSaving OFF, 1080P30, VideoClipTime 3-5 min, StatusLights OFF, AWB tied to lamp).
- Pi: go2rtc (:1984, #video=copy#audio=drop, WebRTC), FastAPI (:8000), nginx same-origin HTTPS PWA (file:// origin broke geolocation/backend — solved).
- Dashboard: camera primary fullscreen, circular radar map (real map rotated by bearing, not CSS), one draggable launch pin, SAVE OFFLINE = current view, offline-first client (only video/telemetry/commands need Pi; NEVER queue vehicle commands).
- Blackbox: dual-sided JSONL, CLOCK_MONOTONIC primary + clock_step events, /dev/kmsg interleave, vcgencmd get_throttled, MARK button, rovlog CLI (check/merge/diverge/timeline/bundle), 8-stage c_id lifecycle + ESP32 ack stage, IndexedDB client ring buffer, SNTP-over-WS clock sync (Ally = time source; sync-on-connect emits clock_step + re-stamps camera TimeSettings; RTC optional not load-bearing).
- Rovo/quorum/other work topics exist but are separate from NEPTUNE.

## 12 · SONAR (IN PROGRESS — the active thread)
- Goal: **depth data → Pi → Ally map overlay** (bathymetry dots on radar track + waterfall strip). NO consumer display wanted.
- Tier 1 (day one, free): log MS5837 depth at every bottom landing → map points. Build the map-layer plumbing against this.
- **Tier 2 (the plan, ~£13): TL88/LUCKY FF1108-1-class WIRED portable fishfinder** — AliExpress https://www.aliexpress.com/w/wholesale-TL88-fish-finder.html £10–14 (REJECT the £25.66 yellow listing — same unit, double price; REJECT wireless/castable variants). Kludge: transducer P-clips to rail facing down (cable pots through cap or spare position); driver board moves to sled fed from 5 V; **tap the echo-envelope node** (probe tracks between receive amp and MCU with multimeter while bucket-pinging — node rises as bottom nears; fallback: tap TX pulse via 100k divider for timing zero) → 10k series (+3.3 V zener) → ESP32 #2 ADC → threshold first echo → alt = Δt×1482/2 → {t, alt_m} over USB. LCD stays connected = ground truth during dev. Worst case: rustic topside mode (head unit on bank via tether spare pairs). Effort: 1 evening tap-hunt + 1 evening firmware.
- **Toslon 640/Skipper 600 eBay bid PLACED, max £8** (~£11.50 landed): bait-boat TX half, NO receiver, proprietary 2.4 GHz — NOT a data path; pure parts donor (good 80 ft transducer, maybe-NMEA GPS puck, compass board). If won: transducer may upgrade the TL88 puck. If lost past £8: let go.
- Tier 3 (skip unless £45 becomes worth one evening): Ping-class UART echosounder module.
- Physics: 200 kHz/45° in 1–3 m = 20–50 cm bottom footprint = depth-strip surveying, not imaging. JSN-SR04T air-rangers DON'T work submerged.

## 13 · COMPLETE HARDWARE LIST (every part, all orders; status: ✅ordered/arrived ⏳in-basket 🔲to-buy 📦owned/salvaged)
**HULL & SEALING**
✅ Acrylic tube 130 OD×400, 5 mm cast (AQUROV) £50.79 · ✅ Dome assembly 130 mm w/ flange+O-rings (Zhifeng) £69.79 · ✅ Watertight flange 130 mm (ROVWAKER) £56.39 · 📦 10 mm acrylic sheet (rear cap + bus bar + X-mounts + partition) · ✅ Gland assortment PG7–PG16 25pc £9.39 · ✅ PG7 £2.60 + M12-6.5 £2.80 (earlier) · ✅ Silicone grease 10 g £2.53 · 🔲 M6 nylon vent screw + O-ring · 🔲 epoxy (verify stock) · 🔲 spare O-rings flange-size (if kit lacks)
**FRAME & MOUNTING**
🔲 32 mm waste pipe + fittings (B&Q) · ✅ Yoga blocks ×2 £2.73ea≈ (saddles) · ✅ Jubilee 150 mm ×2 £2.66+£2.50 · ✅ P-clips Ø60 rubber-lined 2pc ×2 packs £6.90ea = 4 clips · ✅ Washer kit 260pc £3.73 · ✅ M2–M6 304 assortment box (earlier) · ✅ M6 wing nuts 10pc £2.11 · ✅ M6 nylocs 20pc £1.40 · ✅ M6 304 rod 300 mm ×2 £3.58 · 🔲 medium threadlock · ✅ zip ties 3×100 green + 3×150 purple 100pc £1.44+£1.72 (🔲 UV-black 200pk later)
**BALLAST**
⏳ Lead 3 kg sheet offcuts (eBay devonroofer) £17.49 · ✅ TPU soft flask 500 ml ×2 £2.49+£2.50 · ✅ Peristaltic pump 12 V £4.96 (🔲 2nd = spare) · ✅ YF-TM02 flow ×2 £1.99 (one inline ballast, one speed log) · ✅ PP barbs 4.1 mm 5pc £1.51 · 🔲 silicone/PU pump tube 2 m (check pump kit) · drop-weight & bridle from lead + M5 bolts + nylon/nichrome below
**RELEASE (burn wire)**
🔲 Nichrome 0.3 mm (vape shop) · 🔲 nylon mono 0.5 mm + paracord (fishing shop) · ✅ 10W 1R cement ×10 £2.43 (2 series = 2R/20W) · ✅ IRLZ44N ×10 £1.45 · fuse-wire card 📦 → 5 A stock
**PROPULSION**
✅ 390 thruster pair L+R w/ props, gaskets, discs (POBOTRAE) £23.99 · ✅ D50 4-blade prop pair £2.70 · ✅ Couplers 2.3→4 mm ×2 £0.81ea (⚠ verify pieces-per-pack; want 4) · ⏳ 60.3 mm PVC 425 mm (eBay) £8.99+£4.99 · 📦 DRV8871 ×2 · ✅ LM2596 ×3 £1.82 (8 V rail) · 🔲 BTS7960 ×2 only if 3.6 A limit proves short
**POWER**
📦 Rebuilt 3S3P pack 11.1 V 6 Ah (ThinkPad INR18650 ×9, Kapton-wrapped, DEEPLY DISCHARGED — charge first) · ✅ DollaTek 3S 40A balance BMS ×2 £5.99 (Amazon) · 🔲 12.6 V 2 A CC-CV brick IF adaptive charger ≠ exactly 12.6 V (verify label) · ✅ 50 A isolator £3.46 · ✅ Mini fuse holders 5pc ×2 £5.19ea · ✅ Fuse box 180pc micro/mini/std £5.20 · ✅ XT60 5-pair £5.27 (zero spare; 🔲 2nd pack nice-to-have) · ✅ 5 V 3 A mini buck £0.74 · ✅ 18 AWG 10 m £7.14 · ✅ 22 AWG 10 m £3.26 · ✅ 28 AWG 5 m (earlier) £1.61
**ELECTRONICS**
✅ ESP32 WROOM-32 DevKit ×2 £7.26 · 📦 Raspberry Pi · ✅ BNO085 £16.39 · ✅ MS5837-30BA £6.00 · ✅ INA219 ×2 £1.07+£1.01 · ✅ Leak sensor boards 5pc £1.64 · ✅ NTC 10K 10pc £1.12 · ✅ PAS pedal ring £3.21 · ✅ Micro limit switches 10pc £1.36 · ✅ Protoboards 10pc £1.80 · ✅ JST-XH pre-crimped kit £9.79 · ✅ Resistor kit 1/4W 30val 300pc £2.43 · ✅ Cement 5W 22R ×10 £1.80 + 5W 68R ×10 £1.80 · 🔲 M3 brass standoff box · 📦 8266 fleet + C3s (bench/witness only) · 📦 Dupont jumpers (bench only) · surplus: A4988 ×2, 15 mm linear stepper £15.59 (→camera-tilt donor)
**LIGHTS**
📦 Headlamp housing (greased, switch bypassed, dummy cells) · ✅ 3W star LEDs 10pc £1.89 (earlier) · resistors from cement packs · 📦 LiFePO4 pair → independent emergency flasher (own charger!)
**CAMERA & TOPSIDE**
📦 WOLFANG 4K action cam · 📦 ROG Ally · 📦 flat Cat5e ~7–10 m + 🔲 paracord member · 🔲 RJ45 keystone jack (bus bar)
**SONAR**
🔲 TL88/FF1108-1 wired portable ~£13 (AliExpress TL88 search; NOT wireless, NOT the £25.66 listing) · ⏳ Toslon 640/Skipper 600 eBay bid ≤£8 (parts donor: transducer+GPS) · Tier-3 fallback: Ping-class UART module £40–60

## 14 · STILL TO BUY / DO LOCALLY
Nichrome 0.3 mm (vape shop) · 0.5 mm nylon mono + paracord (fishing shop) · 32 mm waste pipe + fittings (B&Q) · epoxy (check stock) · M6 nylon vent screw + O-ring · **medium threadlock** (in NO basket — critical) · TL88 fishfinder ~£13 · optional: 2nd XT60 pack, 2nd pump, UV-black zip ties, 12.6 V 2 A brick (if adaptive charger ≠ exactly 12.6 V — **verify label**), spare O-rings in flange size (if kit lacks), M3 brass standoff box.

## 15 · BENCH-TEST CHECKLIST (on delivery, before build)
1. **URGENT: BMS onto pack + supervised recovery charge** (groups at ~3.0 V, deteriorating).
2. YF-TM02 floor test (blow/trickle — does it read ~0.05 L/min?). Output type check (open-collector vs drives-to-VCC → divider).
3. PAS minimum-cadence gating (hand-spin slow — pulses?).
4. 390 shaft = 2.3 mm confirm (slide kit prop on); coupler pack = how many pieces?
5. Burn-wire bucket fire ×3 (2×1R series, 1.5 s pulse; current on bench PSU → config expected value).
6. Dome/flange/tube fit-up on arrival + message-seller compatibility answers.
7. Adaptive charger label: exactly 12.6 V?
8. Prop/coupler/threadlock assembly, flats filed.
9. Headlamp housing greased + weighted bucket overnight.
10. TL88 (when bought): bucket ping + tap-hunt evening.

## 16 · BUILD SEQUENCE (after bench)
Cap penetrations potting session → vacuum test cap-on-jar → sled decks weld + build (floor → mid → mezzanine → bus bar) → slide-in/insertion-sweep test → EVA saddles + straps → lead template + stacks → pods (X-mounts, shrouds, P-clips) → lamp mount → speed sensor + horn → external cable dressing → **BATHTUB CEREMONY** (integration doc §8: 7 steps — float attitude, tray lead to just-awash, drop weight seat, bag full-range sweep, vac test, burn-wire flight-bridle ×3, record all numbers into config/session_start) → shallow short first dives, towel checks → compass cal in-water → speed LUT calibration runs.

## 17 · DESIGN PRINCIPLES (recurring, cite when evaluating anything new)
- Titles inflate; labels/variant-selectors tell the truth (flow sensors, fuse kits, resistor ranges).
- Buy the assortment box once; never be missing a £0.30 part mid-project.
- Everything's a consumable: buy 2 (pumps, couplers, flasks, ESP32s, O-rings).
- Static seals only across the pressure boundary (wires, tubes, nylon — never shafts).
- Mechanical fixings wet-side; adhesives fail in water (except potting epoxy + solvent-welded acrylic).
- One connector family per role; key/colour anything identical side-by-side.
- Friction connections = bench; latching/soldered = boat (no Dupont jumpers aboard).
- Bandwidth: pump=minutes, bag=bias, thrusters=seconds, drop=instant.
- Cheap donor with working electronics > bare component + blind DIY (fishfinder kludge).
- Every subsystem: bench-test alone → integrate → blackbox-log its health signature.

## 18 · PROJECT ASSETS — BUNDLED WITH THIS PROMPT (agent: SAVE ALL LOCALLY FIRST)
**STATUS 2026-08-18: all 21 files now live in the software repo at `docs/handoff/` (github.com/the-harry/neptune) — that is the canonical home; edit there.** In a session with repo access, use the repo copies directly. In a session without it (mobile), the zip transport rule stands: extract/save every file to the session workspace before answering anything, and do NOT recreate from scratch — these are the canonical versions. If Harry re-uploads the zip, unpack it first.
Specs (agent-buildable, law):
- camera-api-reference-and-defaults.md — full WOLFANG CGI reference + recommended defaults
- sub-camera-api-spec.md — Pi camera service (go2rtc/FastAPI/nginx/mock-first)
- sub-navigation-map-spec.md — dead reckoning, radar map, PMTiles, HUD rules
- nav-map-corrective-spec.md / -2 / -3 / -4 — agent misbuild corrections (camera-primary layout, real-map radar, satellite tiles, launch pin, file:// fix)
- client-offline-first-spec.md — PWA offline rules; never queue vehicle commands
- rov-blackbox-spec.md + rov-blackbox-two-sided-addendum.md — dual JSONL flight recorder, c_id lifecycle, clock sync, rovlog CLI
PDFs (design docs):
- drop-weight-release-design.pdf — 6 pp burn-wire release (bridle in handoff §5 supersedes its single-loop geometry)
- neptune-integration-doc.pdf — 5 pp GA/cap/power/GPIO/bathtub §8 (PARTIALLY STALE: pre-pump/ESP32; redraw pending)
- neptune-3d-master.pdf — 6 pp, FIGs A–D assembled+exploded boat & sled, 30-part keys — CURRENT architecture
Drawings (PNG):
- eva-saddle-diagram.png — saddle cross-section + side view (bottom-block + strap + pad)
- eva-saddle-weights.png — fixed lead + drop weight placement
- pclip-thruster-mount.png — 4-clip pod mounting, 3 views
- sled-two-plates.png — both deck plans, component placement
- neptune-3d-assembled.png / neptune-3d-exploded.png — first-pass boat 3D
- thruster-cassette.png — SUPERSEDED by X-mount/P-clip design (keep for history)
- NEPTUNE-HANDOFF-PROMPT.md — this file (keep updated; it is the project's memory)
Conversation transcript (if present): /mnt/transcripts/2026-08-18-02-17-41-neptune-rov-full-build.txt

## 19 · OPEN QUESTIONS / WATCH LIST
- Toslon auction outcome (≤£8).
- Seller confirmations: 130 mm three-way compatibility.
- Coupler order: 2 pieces or 2 packs?
- Adaptive charger 12.6 V verification.
- YF-TM02 floor + PAS gating results → which is primary speed log.
- DRV8871 3.6 A limit adequate for 390s in practice? (else BTS7960.)
- Integration-doc FIG4/FIG5/GPIO-table redraw to match ESP32+pump architecture (offered, not yet done).
- Harry's hull/frame geometry "differs from what I have in mind" (his note) — revisit at build.
- **Software↔hardware reconciliation: docs REWRITTEN to this bought architecture (2026-08-18/19 repo session — `docs/hardware.md` is now the vehicle's own document, drawings embedded; its §20 is the software-gap ledger); CODE is the next phase** — see §20 below.

## 20 · REPO / SOFTWARE STATUS (added 2026-08-18, repo session)
The control software exists and is substantial: **github.com/the-harry/neptune** (`master`), built and adversarially reviewed across many rounds. Topside PWA dashboard (video, radar map, offline areas, CRT hazard layers, blackbox, screen/still capture, one-touch test runner on the Ally), Pi FastAPI backend (watchdog, sensor-liveness doctrine — a dead sensor shows cannot-tell, never a plausible number), nav (dead reckoning, snapping, soundings, calibration CLI), camera control plane per the specs in this bundle. Both test suites green; totals are printed by the runners, never written down.
**The software still implements the PRE-campaign vehicle** — 2S pack bands, syringe+stepper ballast, paddlewheel speed, every sensor on Pi GPIO, burn wire stubbed as v2. The bought boat (§2–§12 above) diverges on all of those. The repo's `docs/hardware.md` is now **this architecture's own document** (rewritten 2026-08-18/19, drawings embedded); its **§20 is the software-gap ledger**, and the changelog entry is `.specs/tasks.md` ("The parts are bought, and they are not the parts the software describes"). Integration order of attack: ESP32 serial protocol + firmware → `RealHardware` as serial client → 3S bands → pump `ballast_ml` (purge-home) → flow/PAS speed with direction → 3-zone leak with 2-of-3 reflex → burn-wire ARM/FIRE command with its own c_id stage → soundings bottom-contact wording. Bench gates first (§15): pack recovery charge is URGENT.
