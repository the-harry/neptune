# NEPTUNE firmware — the ESP32 brainstem

Arduino C++ for the **ESP32-WROOM-32 DevKit** (30-pin). One sketch:
[`brainstem/brainstem.ino`](brainstem/brainstem.ino). It owns every sensor and
slow actuator on the vehicle and streams JSON lines up USB at 10 Hz; the Pi's
half of the cable is `api/brainstem.py`, whose docstring is the protocol
contract. Architecture and pin rationale: `docs/hardware.md` §8.

**The point of this firmware existing before the parcel does:** a bare devkit on
a breadboard, plugged into any machine running the API, lights the whole console
— honestly blank where nothing is fitted, alive per-instrument as connectors
seat, and fully animated in announced bench mode. The console gets tested the
day the board arrives, not the day the boat is finished.

---

## 1. Flashing (Arduino IDE)

1. **Arduino IDE** ≥ 2.x → Settings → Additional boards manager URLs:
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
2. Boards Manager → install **esp32 by Espressif Systems** (2.x and 3.x cores
   both work — the sketch carries the LEDC compatibility shim).
3. Library Manager → install **Adafruit BNO08x** (accept its Adafruit BusIO
   dependency). That is the only library; MS5837 and INA219 are raw-register
   `Wire` code in the sketch, ported from the Pi's known-good implementation so
   the loop never blocks on a conversion.
4. Board: **ESP32 Dev Module**. Port: whatever the devkit enumerates as
   (`/dev/cu.usbserial-*` on a Mac, `/dev/ttyUSB0` on Linux, `COM*` on
   Windows). Upload speed 921600 works on these boards; drop to 115200 if
   flashing is flaky.
5. Upload `brainstem/brainstem.ino`. Open Serial Monitor at **115200**: the
   first line is the `hello` JSON. Close the monitor before starting the API —
   one process owns a serial port at a time.

## 2. Bench walk — bare board, no sensors, five minutes

The arrival test, rehearsed before anything arrives:

1. Plug the devkit into the bench machine. Start the API
   (`cd api && .venv/bin/python main.py`). The backend log shows
   `brainstem: opened /dev/…` and the boot roll-call; the console drops its SIM
   badge — this is a **real** vehicle now, with almost nothing fitted:
   thrusters faulted and arming refused (no GPIO on a laptop), every I²C gauge
   cannot-tell with its chip named, leak zones latching (see the pull-up
   warning below — with no pull-ups fitted the zones read WET, which is itself
   a correct demonstration).
2. **Announced simulation:** from a second terminal, echo
   `{"t":"cmd","id":1,"name":"mock","value":1}` at the port — or use the
   firmware test hook from python:
   `python -c "from brainstem import open_link; l=open_link(); print(l.request('mock',1))"`.
   Every gauge fills with coherent simulated readings and the console shows the
   SIM presentation, because the vehicle says `mode:"bench"` in every frame.
   Simulation that announces itself is the only acceptable kind.
3. **The pull test:** `kill` / `revive` per chip
   (`{"name":"kill","value":"ms5837"}`) and watch depth+pressure blank together
   with `ms5837` named, then return. Same names, same behaviour as
   `MockHardware._kill_sensor` — the whole cannot-tell chain, exercised over
   the real serial path.
4. **Ballast loop:** `{"name":"mock","value":1}` then `{"name":"trim_home"}` —
   the simulated bag purges, goes silent, homes to 0 ml; `pump_ml` value 50
   runs to target and emits `pump_done`. The level on the console goes from
   `?` to a number exactly as the syringe used to.

## 3. Breadboard wiring (the parcel)

Pin map (BCM-equivalent GPIO numbers; the full table with rationale is
`docs/hardware.md` §8):

| GPIO | Function | Note |
|---|---|---|
| 21 / 22 | I²C SDA / SCL | BNO085 (0x4A), MS5837 (0x76), INA219 ×2 (0x40 pack, **0x41 rail — bridge A0**) |
| 19 | BNO085 INT | wired, not yet used (driver polls) |
| 34 / 35 / 39 | leak FWD / MID / AFT | **input-only pins: NO internal pull-ups. Fit an external 100 kΩ from each pin to 3V3** or every zone reads permanently wet. Probe comb goes pin↔GND; water pulls LOW |
| 36 | NTC | divider: 3V3 — NTC — pin — 10 kΩ — GND |
| 27 | ballast flow (YF-TM02 inline) | internal pull-up; open-collector pulls LOW per pulse |
| 16 | speed flow (YF-TM02 #2) | same. **If the bench test shows the sensor drives its output to 5 V, add the 10k/20k divider** — an ESP32 pin is not 5 V-tolerant |
| 25 / 26 | PAS quadrature A / B | internal pull-ups; direction comes from phase |
| 18 / 17 | pump IN1 / IN2 | **see §4 — direction needs an H-bridge** |
| 23 | white lamp gate (IRLZ44N) | LEDC 8 kHz |
| 13 | red beacon gate (IRLZ44N) | 0.2 s / 1.8 s pattern |
| 14 / 33 | burn ARM / FIRE gates | the two-pin interlock; 220R gate resistors + 10k pulldowns on both MOSFETs |
| 32 | burn continuity sense (optional) | set `HAS_BURN_SENSE 1` when the divider is fitted |

Sensor supply: BNO085/MS5837/INA219 breakouts on **3V3**; YF-TM02 and PAS on
**5 V (VIN/USB)** with their open-collector outputs safe on the pull-ups — but
*verify the output stage on the bench first* (handoff §15 items 2–3).

## 4. Two hardware facts the firmware forces into the open

- **Pump direction needs an H-bridge.** A peristaltic pump reverses by
  reversing its motor. The handoff's power tree drew a single IRLZ44N, which
  can only ever FILL the bag — no purge-home, no pump-out, no reflex
  bag-empty. IN1/IN2 on 18/17 map straight onto a DRV8871 (a third one, ~£2,
  is the natural part). If the single-MOSFET version is what gets built, set
  `PIN_PUMP_IN2` to `-1` and the firmware refuses empty-direction commands out
  loud instead of pretending. Watch-listed in `docs/hardware.md` §19.
- **`PUMP_ML_PER_PULSE` is a placeholder** (0.2 ml/pulse) until the bench
  trickle test measures the YF-TM02 (handoff §15 item 2). Every millilitre
  figure scales by it; calibrate before trusting a trim number.

## 5. Design notes

- **Nothing in `loop()` blocks.** Flow/PAS pulses are counted in IRAM ISRs
  with microsecond timestamps (period math, not counting — resolution improves
  as the vehicle slows); the MS5837's 17.2 ms conversions are a state machine;
  the burn pulse, beacon pattern and pump runs are all timed by
  `millis()` subtraction.
- **The honesty doctrine is ported, not approximated.** Per-chip
  streak-or-silence liveness, never-answered-is-faulted, a dead chip's
  readings null in the same frame that names it, `NORMAL`-class reassurance
  only from probes actually sampled, wet-at-boot captured, latches one-way
  with `leak_reset` refused while a zone is wet *now*.
- **Reflexes live here** because they must survive a hung Pi: 2-of-3 leak
  agreement → beacon + bag-empty + `reflex_surface` flag; sustained pack
  undervolt (< 9.0 V, the 3S floor) → `undervolt` flag. The burn FIRE is
  refused unless ARM agrees — two commands, two pins, one check.
- **The ring buffer is the third blackbox witness**: the last 48 discrete
  events (latches, homings, firings, mode changes), RAM-resident, dumped by
  the `ring` command. It survives a hung Pi; a power cut takes it, and the
  `hello`'s reset-reason at least says the cut happened.
- **ISRs instead of the PCNT peripheral, deliberately.** PCNT's API changed
  incompatibly between core 2.x and 3.x; at these pulse rates (≤ a few hundred
  Hz) interrupt counting is exact, and a sketch that compiles on whichever
  core the bench has beats a peripheral nobody can flash. Revisit only if
  bench data ever shows missed pulses.
