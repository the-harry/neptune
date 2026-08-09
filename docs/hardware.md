# NEPTUNE — the hardware document

The one taken **shopping** and to the **workbench**. Every part, pin, threshold and
calibration constant here is mirrored from the code that reads it —
`api/hardware.py`, `api/config.py`, `api/nav/config.py`. Nothing on this page is
aspirational: if it is not in the v1 lists below, the software does not know about it,
and the v2 section is quarantined at the end precisely so it cannot be bought by
accident.

**The honesty rule this build is arranged around.** A sensor that is not answering reports
*cannot-tell*, never a plausible number — and **"not answering" includes a sensor that was
here and stopped**, which is the case you will actually meet at the waterside. §6.2 is what
each chip's death looks like, §6.3 is how to tell it from a dropped frame, and
`.specs/design.md` §24 is why both are written the way they are.

A "safe" default is not safe when the default is itself a reading: `0.0` heading is **due
north**, `0.0` depth is **the surface**, `mag_cal` 0 is *"a compass answered and reports
itself uncalibrated"*, and a leak state of `NORMAL` is a **positive claim that the hull is
dry**. None of those means *unknown* to the person holding the console.

`settings.hardware_wired` (`NEPTUNE_HW_WIRED`, default **true**) is what takes the vehicle
off the bench simulator; with it false, or with `gpiozero` absent, `NEPTUNE_HW=auto` falls
back and the dashboard keeps its SIM badge. Have it on when the **first** module goes on the
pins — §10 step 3 — not when the last one does. That is safe because every chip carries a
liveness verdict: one that is not answering blanks its own gauges and names itself, so an
unwired module reads as unwired rather than as a comfortable number. A backend reporting
`mock: false` while every sensor returned a constant would be strictly worse than an honest
simulation — the console would present `0.0 V`, `heading 0` and "at the surface" as
instrument readings.

One absent module does not hold up the rest. The five GPIO groups come up independently and
a group that fails is named rather than fatal, so a vehicle with one sensor soldered is a
vehicle you can bring up, read honestly, and add to. §10 explains what that buys a staged
build, and what it still does not cover.

**Fitting one instrument tonight? Go straight to §11.** There is a card per v1 instrument:
its pins and flags, what the console shows at every stage of that module's life, how to
drive it from the Pi before the hardware exists, and what the design cannot tell apart.
§10 is the order they go on in; §11 is what to do once you are holding one.

How any of this **looks on screen** is not this page's business: `docs/playbook.md` is the
presentation contract (§1 the state ladder, §2 the marks, badges and leak ladder), and this
page cites it rather than restating it.

---

## 1. Bill of materials

### 1.1 Already owned — do not re-buy

| Qty | Item | Purpose |
|---|---|---|
| 1 | Raspberry Pi 3B+ | the vehicle computer. Ethernet is the tether, `wlan0` is the camera link |
| 1 | microSD card | Pi OS + the API |
| 1 | WOLFANG action camera | the video plane, over its own Wi-Fi AP |
| 2 | small brushed DC thrusters (2-wire) | propulsion — confirmed brushed, so an H-bridge and not an ESC |
| 2 | H-bridge motor driver modules | one per thruster: IN1/IN2 set direction, EN takes the PWM |

### 1.2 v1 shopping list

| Qty | Item | Purpose — what breaks without it |
|---|---|---|
| 1 | NEMA 17 stepper motor | drives the ballast syringe plunger. Open-loop: position comes from counted steps, nothing else |
| 1 | A4988 stepper driver (+ heatsink) | STEP/DIR/EN for the NEMA 17, with an adjustable current limit |
| 2 | microswitch, **NC + COM contacts** | ballast end stops: one at EMPTY (the homing datum), one at FULL (the span check) |
| 1 | Cat5e **outdoor/direct-burial** cable, 30–50 m | the tether: Ethernet to topside, and the only thing the sub is on the end of |
| 6 | cable glands (M12/PG7, to suit) | tether, thruster L, thruster R, green ring, white spots, and one spare for the paddlewheel plan B |
| 1 | BNO085 IMU breakout | fused yaw (the compass), magnetometer calibration status 0–3, pitch/roll, gyro yaw rate, linear acceleration |
| 1 | MS5837-30BA pressure sensor | **measured** depth. Depth is never integrated — this part is the only thing that knows how deep the sub is |
| 2 | A3144 hall-effect sensor | paddlewheel pulse pickup (one fitted, **one spare** — a dead hall sensor at the waterside otherwise ends the day) |
| 8 | 4 × 2 mm neodymium magnets | two go in opposing paddles of the wheel; the rest are spares and pole-test pieces |
| 1 | rotary encoder, ~600 PPR, **quadrature** | tether spool payout. Quadrature so rewinding *reduces* payout instead of adding to it |
| 1 | INA219 current/voltage breakout | pack voltage for the battery bands, and pack current — free from the same chip, and the only way to size the fuse honestly |
| 3 | IRLZ44N logic-level MOSFET module | green channel, white channel, **one spare** (a blown module is a dark sub) |
| 1 | 5 V LED strip, 1 m | the green ring around the hull — the "I am here" light |
| 2 | 3 W white star LED (+ CC driver or series resistor) | the bow spots, switched together as the single `white` channel |
| 1 | 2S Li-ion pack with BMS | 8.4 V full, 7.4 V nominal — **not** a 24 V scale; §7.1 has the bands |
| 1 | buck converter, 5 V / 3 A | the Pi 3B+ rail and every 5 V sensor. The Pi wants 5 V ±5 % at up to 2.5 A peak |
| 1 | buck converter, adjustable 3–5 V | the motor rail, set to the thrusters' rated voltage |
| 1 | fuse holder + fuses (start at 7.5 A) | between the pack and everything. Sized on what the INA219 actually reports |
| 1 | master switch (rated for the pack current) | the thing you can reach when something is wrong |
| 1 | XT60 pair | pack connector |
| — | JST connectors, tinned wire, perfboard, heat-shrink, marine epoxy | probes, harness, potting, strain relief |

### 1.3 Software the Pi needs

| Library | Used for | Note |
|---|---|---|
| `gpiozero` | thrusters, stepper, limit switches, leak probes, pulse/edge counting | imported **lazily inside `RealHardware`** — this code is written on a machine with no GPIO |
| `smbus2` | the I²C bus: MS5837 and INA219 | same lazy rule |
| a BNO085 driver | ROTATION_VECTOR, GYROSCOPE, LINEAR_ACCELERATION reports and the mag-cal status | whichever driver is pinned in `api/requirements.txt` — pin it there, do not `pip install` it ad hoc, or the Pi that gets reimaged is a different vehicle |

`python bootstrap.py` reports each of these present/absent and never installs one.

---

## 2. Pin map (BCM)

Mirrors `RealHardware`'s pin constants. **Fill the wire-colour column in by hand** the
day you make the harness — the colour is the only part of this table that lives on the
bench and not in the repo, and a harness whose colours are only in someone's head is one
nobody else can debug.

| BCM | Header pin | Function | Mode / pull | Wire colour |
|---|---|---|---|---|
| GPIO12 | 32 | thruster **L** enable | PWM ~2 kHz — **software as built**, on the PWM0-capable pin (§3.1) | |
| GPIO5 | 29 | thruster **L** IN1 | out | |
| GPIO6 | 31 | thruster **L** IN2 | out | |
| GPIO13 | 33 | thruster **R** enable | PWM ~2 kHz — **software as built**, on the PWM1-capable pin (§3.1) | |
| GPIO16 | 36 | thruster **R** IN1 | out | |
| GPIO26 | 37 | thruster **R** IN2 | out | |
| GPIO20 | 38 | light **white** (2× bow spots, one channel) | software PWM ~200 Hz | |
| GPIO21 | 40 | light **green** (hull ring) | software PWM ~200 Hz | |
| GPIO23 | 16 | ballast **STEP** | out | |
| GPIO24 | 18 | ballast **DIR** | out | |
| GPIO25 | 22 | ballast **/EN** (A4988, **active low**) | out | |
| GPIO22 | 15 | limit switch **EMPTY** end | in, pull-up, NC-to-GND | |
| GPIO27 | 13 | limit switch **FULL** end | in, pull-up, NC-to-GND | |
| GPIO17 | 11 | leak probe **WARN** (lowest point) | in, pull-up — wet pulls LOW | |
| GPIO4 | 7 | leak probe **FLOOD** (+2 cm) | in, pull-up — wet pulls LOW | |
| GPIO10 | 19 | paddlewheel hall pulse | in, pull-up, edge interrupt | |
| GPIO9 | 21 | spool encoder **A** | in, pull-up, edge interrupt | |
| GPIO11 | 23 | spool encoder **B** | in, pull-up, edge interrupt | |
| GPIO2 | 3 | I²C1 **SDA** | 3.3 V bus | |
| GPIO3 | 5 | I²C1 **SCL** | 3.3 V bus | |
| — | 2, 4 | 5 V from the buck | in *(the Pi is fed here, not from a phone charger)* | |
| — | 1, 17 | 3.3 V out | sensor logic only | |
| — | 6, 9, 14, 20, 25, 30, 34, 39 | GND | **common with every other ground** | |

**GPIO18 and GPIO19 are deliberately left unused.** See §3.

**GPIO9/10/11 are the SPI0 pins.** Leave SPI **disabled** (the default). Enable it and the
kernel drives the very pins the encoder and paddlewheel sit on, which presents as a spool
that counts by itself while the sub is stationary.

**GPIO4 is the default 1-Wire pin.** If `dtoverlay=w1-gpio` is ever added for a temperature
probe, it takes GPIO4 and the FLOOD probe silently stops being read — the one probe whose
silence is indistinguishable from "dry".

### 2.1 I²C addresses — one bus, no conflicts

| Device | Address | Alternates | Logic |
|---|---|---|---|
| BNO085 IMU | **0x4A** | 0x4B if the address pin is pulled high | 3.3 V (use a breakout with a regulator if feeding it 5 V) |
| MS5837-30BA depth | **0x76** | none — fixed | **3.3 V only.** 5 V destroys it |
| INA219 power monitor | **0x40** | 0x41 / 0x44 / 0x45 via A0/A1 | 3.3 V logic, bus voltage up to 26 V |

Enable I²C (`raspi-config` → Interface Options, or `dtparam=i2c_arm=on`) and prove all
three before writing a line of code:

```
$ i2cdetect -y 1
     ... 40 ... 4a ... 76
```

Three addresses or the wiring is wrong. Keep the I²C runs **short** — under ~30 cm inside
the hull. Long I²C does not fail cleanly; it fails intermittently, at depth, once.

### 2.2 How often each device is actually read

One background sensor thread polls the buses into cached fields; every `read_*` method
returns the cache, because they are called from the asyncio hot path and must not block.

| Source | Rate | Why that rate |
|---|---|---|
| BNO085 | 50 Hz (the loop rate) | heading feeds the filter; slower and the gyro integration gets coarse |
| MS5837 | ~10 Hz, high oversampling | depth changes slowly; oversampling buys resolution instead of speed |
| INA219 | ~2 Hz | a battery band does not need to be fast, and the bus is shared |
| Leak probes | 10 Hz | five consecutive wet samples (~0.5 s) latch a stage — see §6.1 |
| Paddlewheel, spool encoder | **interrupts** | never polled. A polling loop on the event loop is how edges get missed |

---

## 3. The PWM channel trap (read before wiring anything)

The Pi has **two** hardware PWM channels, and each is exposed on two pins:

| Channel | Pins | Taken by |
|---|---|---|
| PWM0 | GPIO12, GPIO18 | **thruster L** on GPIO12 |
| PWM1 | GPIO13, GPIO19 | **thruster R** on GPIO13 |

You cannot run four independent hardware PWMs on this machine. Anything on GPIO18 shares a
channel — and therefore a frequency and a duty register — with GPIO12; the same for
GPIO19 and GPIO13. Wiring a light to GPIO18 does not produce a dim light; it produces a
thruster whose speed changes when you dim the light. As built the trap is *dormant* rather
than absent — nothing is on the PWM peripheral yet (§3.1) — which is exactly why it is easy
to walk into: the wiring mistake would cost nothing until the day someone promotes the
thrusters, and then it would look like a motor fault.

So: **the thrusters claim both channels** (`thruster_pwm_hz`, 2 kHz — above audible whine
for a brushed motor and well inside what the H-bridges switch cleanly), **the lights sit on
ordinary pins** (`light_pwm_hz`, 200 Hz — flicker-free for LEDs and cheap enough in
software), and **GPIO18/19 are left unused** so nobody can reintroduce the conflict by
picking "the next free pin".

### 3.1 As built, every PWM output is SOFTWARE PWM — the thrusters included

Reserving a channel is not the same as using it, and those two thruster rows are the one
place in this document where a builder could wire a board for something the software does not
actually do — so it is spelled out rather than left to the table. `RealHardware`
builds **every** `PWMOutputDevice` — thrusters and lights alike — through gpiozero's
**default pin factory**, and that factory bit-bangs PWM from a Python thread on *every* pin,
GPIO12 and GPIO13 included. Nothing in the repo selects a different one; `api/hardware.py`
says as much in the comment above the light devices. Wire for software PWM at the thrusters.

**What that costs.** Thread-timed pulses jitter whenever the Pi is busy — and this Pi runs
the control loop, the sensor thread, the 400 steps/s stepper thread and go2rtc. The jitter
lands on the duty, so the symptom is a thruster that hums and twitches at a perfectly steady
stick, worst at low duty where a hundred microseconds of slip is a large slice of the pulse.
It is neither dangerous nor a wiring fault, which is worth knowing before you spend an
evening re-crimping a harness that is fine.

**GPIO12/13 are still the right pins, and this is why.** They are the only two the PWM
peripheral can ever reach (12 = PWM0, 13 = PWM1), so keeping the motors — the one load that
genuinely cares about clean PWM — on them makes the upgrade below a software change instead
of a rewiring job. It is the same reason GPIO18/19 stay empty: they are the *other* halves of
those two channels, and the trap above is only dormant while everything is software.

**The upgrade, when the jitter starts to matter: the pigpio pin factory.** pigpio times its
pulses with DMA inside a daemon rather than from a Python thread, so the duty stops depending
on how busy the Pi is. No wires move:

```
sudo apt install -y pigpio                       # the daemon
sudo systemctl enable --now pigpiod
/opt/neptune/api/.venv/bin/pip install pigpio    # gpiozero imports the client library, and
                                                 # the API runs out of that venv, not /usr
# then one line in deploy/systemd/neptune-api.service:
Environment=GPIOZERO_PIN_FACTORY=pigpio
sudo systemctl restart neptune-api
```

Order the unit `After=pigpiod.service` while you are in there. With the variable set and the
daemon down, every `PWMOutputDevice` fails to construct, `RealHardware.__init__` raises and
`NEPTUNE_HW=auto` lands on the bench simulator — an honest failure, but the SIM badge on the
dashboard is the only notice you get that the vehicle is not driving anything.

Be exact about what the factory buys, because "pigpio" and "hardware PWM" get used
interchangeably and are not the same thing: gpiozero drives `PWMOutputDevice` through
pigpio's **DMA-timed** PWM, not the SoC's PWM peripheral — gpiozero has no API that reaches
pigpio's `hardware_PWM()`. Peripheral-generated PWM on GPIO12/13 needs that call made
directly, or a `pwm-2chan` dtoverlay, i.e. code this repo does not contain. DMA timing has
been steady enough that nobody has had to. And the factory changes **all four** PWM outputs,
not two, so re-run steps 4 and 6 of the bring-up order (§10) after setting it.

---

## 4. Wiring notes, per subsystem

### 4.1 Thrusters — one H-bridge each

Brushed motors, two wires each, no ESC and no arming pulse.

| H-bridge | To |
|---|---|
| `IN1` / `IN2` | the two direction GPIOs (L: 5/6, R: 16/26) |
| `EN` (or `PWM`) | the PWM pin (L: 12, R: 13 — the PWM-capable ones, driven in software as built: §3.1). Remove the jumper if the board ships EN tied high |
| motor supply `VM` | the **motor rail** (§7), not the Pi's 5 V |
| logic supply `VCC` | 5 V |
| `GND` | common with the Pi |
| `OUT1`/`OUT2` | the motor |

The sign of the command sets IN1/IN2; the magnitude sets the EN duty. Below
`thruster_deadband` (0.05) the duty is forced to **0**, not to a trickle: a tiny command
cannot turn a prop but does make the bridge sing, and a whining idle sounds exactly like a
fault to whoever is holding the tether. `set_armed(False)` and `safe()` both force duty 0.

If a thruster runs backwards, **swap OUT1/OUT2 at the bridge** — do not fix it in
software, or the next person reads the pin map and gets a sub that spins.

### 4.2 Ballast — A4988 + NEMA 17 + two limit switches

| A4988 | To |
|---|---|
| `STEP` | GPIO23 |
| `DIR` | GPIO24 |
| `/ENABLE` | GPIO25 — **active low**: LOW energises the motor |
| `RESET` | tied to `SLEEP` (both must be high or the driver never steps) |
| `MS1/MS2/MS3` | all **low** = full step. See the warning below |
| `VDD` | 5 V logic |
| `VMOT` / `GND` | **battery-direct**, with a **100 µF electrolytic across VMOT** sited at the driver |
| `1A/1B`, `2A/2B` | the two motor coils — find the pairs with a multimeter, not by wire colour |

**The 100 µF capacitor is not optional.** The LC spike on first power-up routinely kills
A4988s that were wired "correctly" without it. And **never unplug the motor with power
on** — that is the other way they die.

**Microstepping changes the span.** `ballast_span_steps` (4000) is a count in whatever
microstep mode the jumpers select. At full step a NEMA 17 is 200 steps/rev, so 4000 steps
is 20 revolutions — with a typical 8 mm/rev leadscrew, 160 mm of plunger travel. At
`ballast_step_rate` (400 steps/s) a full stroke takes about **10 seconds**. If yours takes
40, you are microstepping at 1/4 and the configured span is wrong by 4×, and the syringe UI
is silently rescaled with nothing looking broken.

**Current-limit trim procedure** (do this before the plunger is attached):

1. Set the microstep jumpers first. Changing them afterwards changes nothing electrical but
   invalidates the span you measured in §8.3.
2. Identify the sense resistors `Rs` on *your* board — printed `R100` (0.1 Ω), `R050`
   (0.05 Ω) or `R200` (0.2 Ω). Clones vary and the formula is worthless without it.
3. Target a current the motor and the job actually need. A syringe is a light load: **0.6–0.8 A**
   is plenty for a 1.5 A NEMA 17 and keeps the driver cool enough to trust in a sealed hull.
4. `Vref = I × 8 × Rs`. At 0.7 A with 0.1 Ω sense resistors, `Vref = 0.56 V`.
5. Power the logic **and** VMOT, do not send any STEP pulses, and measure DC volts between
   the **trim-pot wiper** and GND. Turn the pot slowly with an insulated screwdriver — a
   metal one shorting the pot to the heatsink is the classic way to end this step early.
6. Run the full stroke a few times and feel the motor. Too hot to hold = back it off; skipped
   steps or a stall = up a little. On an open-loop axis a skipped step is not a glitch, it is
   the reported level quietly drifting away from where the plunger actually is.

**Both limit switches are wired NORMALLY-CLOSED to ground**, using the internal pull-ups:

```
GPIO22 ──┬── switch COM ── NC contact ── GND        (EMPTY end)
GPIO27 ──┴── switch COM ── NC contact ── GND        (FULL end)
         internal pull-up enabled on both pins
```

At rest the closed contact holds the pin **LOW** = *not at the limit*. Reaching the limit
opens the contact and the pull-up takes the pin **HIGH** = *triggered*. A cut lead, a
pulled crimp or a corroded contact all read HIGH too — so a broken switch fails to a
**stop**, not to a silent absence. Wired the obvious way round (NO-to-ground), a broken
switch reads "never triggered" and the plunger drives itself into the end of the barrel.

Hitting either switch stops motion **in that direction** always, even mid-command.
`ballast_home()` drives toward EMPTY until that switch closes, zeroes the counter and sets
homed. If the FULL switch then closes at a count differing from `ballast_span_steps` by
more than `ballast_span_tolerance` (5 %), steps were skipped: it is logged, surfaced as
`ballast_needs_rehome` in telemetry, and the level it produces is not to be believed until
homing repeats.

### 4.3 Lights — one MOSFET module per channel

Exactly two channels: `green` (the 5 V hull ring) and `white` (both 3 W bow spots, switched
**together** as one channel).

| Module | To |
|---|---|
| `SIG` / `PWM` in | GPIO21 (green) or GPIO20 (white) |
| module `GND` (signal side) | **the Pi's ground.** A floating gate reference is the classic "the LEDs flicker at random" fault |
| `V+` in / `V+` out | the supply rail through to the LED positive |
| `V−` out | the LED negative — the module switches the **low side** |
| `V−` in | the rail's negative, common with everything |

The green strip runs from the 5 V buck; take its feed as its own pair from the buck output
rather than daisy-chaining through the Pi's wires, so 200 Hz switching ripple does not end
up on the rail that keeps the computer alive.

The 3 W stars need current limiting: either a series resistor sized against the measured
rail, or a constant-current driver. **If you use a CC driver, PWM its dimming input, not its
supply** — chopping a driver's input at 200 Hz gets you inrush, flicker and a driver that
runs hot for no visible reason.

`set_light_level()` maps 0..1 to duty and treats anything ≤ 0.02 as off, so the dashboard's
"on" state and a duty of zero cannot disagree.

### 4.4 INA219 — the shunt goes HIGH SIDE, first in the chain

```
pack + ──── fuse ──── [ INA219  VIN+ ▸ shunt ▸ VIN− ] ──── master switch ──── everything
```

`VIN+` faces the fuse, `VIN−` faces the load. High side, and **before** the switch's
downstream side, so it measures the whole vehicle: Pi, thrusters, stepper and lights
together. On the low side it would miss anything grounding elsewhere and read a comfortable
lie. Its `SDA/SCL/GND/VCC` join the ordinary 3.3 V I²C bus, and its ground **must** be common
with the Pi's or the measurement is meaningless.

The stock 0.1 Ω shunt with the default calibration reads to **±3.2 A**. If this vehicle's
peak draw exceeds that, the reading clips — and *a clipped reading is not a maximum*. Fit a
0.01 Ω shunt and recalibrate rather than quietly living with the ceiling.

`read_current_a()` returns `None` where there is no current sense. It never returns 0.0:
"nothing is drawing power" is not true of a running vehicle, and a zero would be believed.

---

## 5. Build notes

### 5.1 Leak probes

Two probes, both on a scrap of perfboard, each an **interleaved comb of tinned wire** with
about 1 mm between the fingers — one comb to the GPIO, the other to GND. Canal water
bridges the gap easily; the internal pull-up holds the pin high when dry, and wet pulls it
**LOW**.

- **WARN** probe at the **lowest point** of the hull, where the first millimetre of water
  collects. Advisory: *water is collecting, finish up*.
- **FLOOD** probe **2 cm higher**: *come up now*. (Both stages' glyphs: `docs/playbook.md` §2.)

Solder the joints, coat *the joints* in epoxy, and leave the comb itself bare. A probe you
have conformal-coated is a probe that will never get wet.

Both stages are debounced: `leak_debounce_samples` (5) consecutive wet samples at 10 Hz —
about half a second — before a stage latches. Condensation, a splash on launch and a droplet
running down the inside of the hull all touch a probe briefly; real ingress does not stop.
The debounce is what makes the FLOOD alarm worth believing.

**The failure this design would otherwise hide.** A dead probe reads dry forever, and dry is
the answer you were hoping for. Two things are done about it:

- The code reports impossible combinations at arm time (`leak_probe_fault`): FLOOD wet while
  WARN is dry is physically impossible — water reaching the upper probe passed the lower one —
  and both wet on a dry deck means the combs are bridged by something that is not canal water.
- **Nothing in hardware can tell "dry" from "disconnected" on a bare digital input.** An open
  circuit and a dry comb are the same circuit. So the pre-dive test is not optional — and it
  has to be the *right* test.

**Do not test it with a wet finger.** The arithmetic says it cannot work. The Pi's internal
pull-up is roughly 50 kΩ and the input reads LOW below about 0.8 V, so the probe path has to
present

$$R_{probe} < R_{pull}\cdot\frac{0.8}{3.3-0.8} \approx 50\text{k}\cdot 0.32 \approx 16\ \text{k}\Omega$$

Dry skin is 1–10 MΩ and damp skin is still hundreds of kΩ — two to three orders of magnitude
away. A finger across the combs proves **nothing**, and reads exactly like a dead probe.

**Water is not the problem; contact area is.** Tap water is about 500 µS/cm, so ρ ≈ 20 Ω·m,
and with the comb's 1 mm gap the facing area needed to cross the threshold is

$$A = \frac{\rho\, d}{R} = \frac{20 \times 0.001}{16000} \approx 1.25\ \text{mm}^2$$

roughly **2.5 mm of adjacent comb** submerged. A proper comb with 20 mm fingers reads a few
hundred ohms in tap water — two orders of magnitude *inside* the threshold. So if a submerged
probe does not trigger, the water is not the fault: the metal is not touching it, or the
circuit is not closed.

**The test that actually proves the path: short the two probe ends together**, dry, with a
jumper or by touching them. That is zero ohms through the real wiring — GPIO lead, contact,
ground lead, header pin — and it *must* produce WARN within about half a second. If a direct
short does nothing, no amount of water ever will, and the fault is in the leads or the pin
seating. Only once the short works is a sponge across the comb a meaningful test of the comb
itself.

> **Short first. It is five seconds and it bisects the whole chain.** This vehicle's leak
> probes have an open wiring fault, still unfixed, and it cost a bench hour before anyone
> shorted the ends: the comb was dipped in tap water repeatedly and the console showed
> `NORMAL` every time, with `sudo pinctrl get 17` confirming the pin high throughout — every
> layer above the pin reporting correctly. Comb geometry, pull-up strength and water
> conductivity were each investigated; none of them was the fault. The short says so at once.

### 5.2 Paddlewheel

A printed wheel 2–3 cm across, with **two magnets set in opposing paddles**, and one A3144
hall sensor reading it.

**Mount the sensor inside, against the hull wall.** The A3144 reads a 4 × 2 mm neodymium
magnet straight through a few millimetres of plastic, so the wheel goes outboard on a stub
axle and **no new hole is made in the pressure hull**. Every hole is a leak that has not
happened yet.

**Plan B if the wall is too thick or the signal too weak:** pot the sensor outboard in
marine epoxy and bring its three wires in through the **spare cable gland** (that is what
the sixth gland in the BOM is for). Sensor outside, hole shared with an existing gland.

Two placement rules, both learned the hard way by people who did not follow them:

- **Away from the prop wash.** Wash spins the wheel whether or not the sub is moving, so a
  sub pinned against a shopping trolley at full thrust reports a healthy 0.6 m/s and the map
  marches cheerfully forward. That is the exact scenario the snag detector exists to catch,
  and a badly sited wheel defeats it. Mount it in clean flow — forward of the thrusters, off
  the centreline, out of the boundary layer.
- **More than 20 cm from the BNO085.** Spinning magnets and a magnetometer are enemies. This
  failure is silent: the heading does not jump, `mag_cal` just degrades and the whole track
  leans. Measure the distance; do not eyeball it.

Wiring: A3144 to **5 V** and GND, output to GPIO10 with the internal pull-up. The output is
**open-collector** — it only ever pulls down, so with the pin's own 3.3 V pull-up it is safe
against a 3.3 V input. **Never add an external pull-up to 5 V on that line**; that puts 5 V
into a 3.3 V GPIO.

The A3144 is a **unipolar** switch: it responds to one magnet face only. If the wheel spins
and nothing counts, flip the magnets before you suspect anything else.

Two physical limits the code already encodes, and you should not try to fix in hardware:

- **The wheel cannot sense direction.** The sign of the speed comes from the commanded
  throttle, not from the wheel.
- **It stalls below about 0.1 m/s.** No pulses travels as `None` — *nothing measured the
  speed* — never as 0.0 m/s: "slower than I can see" and "stopped" are different claims, and
  only the throttle can tell them apart. (`None` is cannot-tell, not stale; the two have
  different marks, `docs/playbook.md` §1.) No pulses with high thrust is the **snag** signal.

### 5.3 Spool encoder

The ~600 PPR quadrature encoder rides the tether drum; A and B go to GPIO9 and GPIO11.

**Check its output stage before connecting it.** Industrial encoders are commonly 5–24 V
with push-pull outputs, and a push-pull 5 V output wired straight to a Pi input damages the
Pi. Use an **open-collector (NPN)** type pulled up to 3.3 V, or put a 10 k/15 k divider on
each channel.

Quadrature is the point: rewinding **reduces** payout. No monotonic maximum is applied,
because payout is an **upper bound on range** (spec §5.5) and reeling cable back in
genuinely tightens that bound. `read_payout_m()` returning 0.0 means *no bound
known*, not *the sub is at the origin*.

### 5.4 Tether strain relief

**The RJ45 never takes load.** Ever. It is a plastic clip designed to hold a plug in a socket
in an office.

- A rope or the cable's own jacket, clamped, takes the load: a clove hitch onto a hull
  eye-bolt, or a proper cable clamp, sited so the pull goes into the hull and not into the
  connector.
- Inside the hull, leave a **slack loop** past the gland so a tug on the tether pulls on the
  gland and the rope, never on the plug.
- Outside, another slack loop and a **drip loop** below the gland so water running down the
  cable falls off instead of arriving at the seal.
- Tighten glands onto the cable jacket, not onto the conductors, and check them after the
  first dive — the jacket relaxes.

---

## 6. Safety signals, and what a dying chip looks like from the bench

### 6.1 Two-stage leak

Four states, because `NORMAL` is a positive claim and not the absence of news. The glyphs
are `docs/playbook.md` §2 (the leak ladder); the electrical facts are here.

| Stage | Probe condition | On the wire | Meaning |
|---|---|---|---|
| `NORMAL` | both dry, **and both actually sampled** | `leak: false` | both probes were read and both were dry |
| `WARN` | low probe wet ≥ 5 samples | `leak: true`, `leak_state: "WARN"` | water is collecting; finish up, non-blocking |
| `FLOOD` | upper probe wet ≥ 5 samples | `leak: true`, `leak_state: "FLOOD"` | come up now |
| `UNKNOWN` | nobody is sampling the probes | `leak: true`, `leak_state: "UNKNOWN"` | the hull is **not being watched** — unverified, not dry |

`read_leak()` returns `FLOOD` if the flood probe is wet regardless of the warn probe, else
`WARN` if the warn probe is wet, else `NORMAL` **only if the probes are actually being
sampled**, else `UNKNOWN`. The two probes are independent — that is why the
impossible-combination check in §5.1 works.

**Why the fourth state exists.** *Both probes were read, and both were dry* is the strongest
reassurance this vehicle gives, so it needs a liveness gate like every other reading. Without
one, a sampler that stops answers `NORMAL` at full telemetry rate for the rest of the dive —
every other gauge correctly blank and naming its chip, and the one readout that decides
whether a dive is recoverable staying green on evidence nobody is collecting. `_leak_tick()`
therefore sits in its **own try-block, on no bus at all**, above the I²C work: a raise from a
bus chip cannot take the probe sampling down with it, and a probe pin that will not read
faults under its own name (`leak-probes`, §6.2).

**Wet outranks cannot-tell.** Water that has already reached a probe is an established
fact and the sampler stopping afterwards does not un-establish it, so a latched `WARN` or
`FLOOD` never decays to `UNKNOWN`. Only the *reassurance* needs liveness. The gate sits
between `WARN` and `NORMAL`, and nowhere else.

**Clearing a latch: the `leak_reset` command.** Latching is one-way and stays one-way — a
probe drying out is not evidence the hull is sound. But one-way with no way back leaves the
only escape a service restart, which at the water's edge means SSH-ing into a submarine and
on the bench means every dip test poisons the rest of the session. So it is a command, through
the same recv → validate → apply → ack lifecycle as `arm` and `surface`, and it lands in the
blackbox with a correlation id: dismissing the strongest claim this vehicle makes should be
findable in the log afterwards.

**The rule that makes it safe: it clears the memory of water, never water that is there now.**
The guard reads the **live pins**, not the debouncers — a debouncer *is* a memory, and a
memory is precisely what the call is asking to erase, so the refusal has to rest on the water
rather than on the bookkeeping about the water. With either probe wet the vehicle refuses and
returns a sentence saying which one; the ack carries that reason, so the console can show it
instead of a button that appears to do nothing.

It clears the wet-at-boot verdict too, deliberately. A probe wet in a hull sealed dry pins
`read_leak()` to `UNKNOWN` for the life of the process — correctly — but that is exactly the
state a human returns from having opened the hull and looked. Clearing only the latches would
leave a bench-wet boot stuck with no way back short of a restart.

Re-arms are counted and reported as `leak_rearms` in telemetry, because a `NORMAL` an operator
restored by hand and a `NORMAL` that was never in doubt are different claims, and the console
is entitled to say which one it is showing. The reset is offered on `FLOOD` as well as `WARN`:
the vehicle decides, and hiding the control would strand an operator who has genuinely pumped
out and dried the bilge.

A leak is the sub telling you something; a **link dropout** is the sub telling you nothing.
Confusing the two sends the operator to the wrong action, which is why the two wear different
shapes rather than different colours (`docs/playbook.md` §2).

### 6.2 What each chip looks like when it dies

The rule the whole build is arranged around (see the header, and `.specs/design.md` §24) is
that a reading whose sensor has stopped shows **cannot-tell**, never a plausible number —
and *stopped* covers both never-wired and **wired-then-stopped**. That second case is the
one you will actually meet on a bench: a connector that vibrates loose, a BNO085 that
browns out when the thrusters spike, an I²C line that corrodes.

So each chip has a liveness verdict, and every readback behind it is gated on that verdict.
A chip counts as **not answering** when either of two things is true, because there are two
ways a bus dies:

- **consecutive raises** — one NAK on a canal-side loom is noise, `fail_streak` in a row is
  not; or
- **silence** — nothing has to *raise* for a device to stop answering. A conversion state
  machine that never reaches its collect stage, a driver that returns without writing, a
  sensor thread that took an exception and ended: none of them produce an error and all of
  them leave the cache frozen. So the last good read must also be **recent**.

A device that has never produced a good read is faulted too — there is nothing behind its
cache at all.

| Chip / subsystem | Streak · silence | What stops | On the wire |
|---|---|---|---|
| **BNO085** (0x4A) | 5 · 1.0 s | heading, mag-cal, gyro rate, linear accel, pitch/roll | `heading`, `heading_card`, `mag_cal` → `null`; `"bno085"` in `sensor_faults` |
| **MS5837** (0x76) | 2 · 2.5 s | depth and pressure — they are one chip | `depth`, `pressure` → `null`; `"ms5837"` in `sensor_faults` |
| **INA219** (0x40) | 2 · 5.0 s | pack voltage and pack current — same chip, so they die together | `battery_v`, `current_a` → `null`; `"ina219"` in `sensor_faults` |
| **leak probes** (GPIO17/4) | 3 · 1.0 s | the *reassurance* only; latched wet states survive | `leak_state` → `"UNKNOWN"`, `leak` → `true`; `"leak-probes"` in `sensor_faults` |
| **sensor thread** | 1 · 1.0 s | everything, because it is what fills every cache | the chips above age out and fault behind it; `"sensor-thread"` in `sensor_faults` |
| **I²C bus** | latched at open | all three chips at once | `"i2c"` in `sensor_faults` — one fault named, not three |

The windows are sized to how often each device is actually polled (§2.2), which is why they
differ. The MS5837's streak is deliberately **short**: its failure path backs off to one
retry a second, so a long streak would let the backoff decide how long a dead depth sensor
keeps showing its last depth.

Every one of those nulls reaches the operator as cannot-tell — `?`, amber, wavy, with an
alert chip naming the part. The marks, the badges and the chip wording are
`docs/playbook.md` §1 and §2; what this page owes them is the `sensor_faults` designation,
which is deliberately the **same name printed on the wiring diagram**, so the console names
the object a human is about to unplug. An **empty** list is not a clean bill of health: a
backend that cannot track liveness reports empty, so the `null` on each individual reading
is the authoritative claim and the list only supplies the cause.

**What is NOT covered by any of this.** Liveness gating applies to the I²C chips, the leak
sampler and the sensor thread. The remaining GPIO inputs have no equivalent, because
nothing in a bare digital input can distinguish a working quiet pin from a dead one:

- **Leak probes** — a dead probe reads *dry* forever, and the liveness verdict above only
  catches a sampler that has stopped, not a probe that is disconnected. Covered instead by
  the impossible-combination check (§5.1) and the short-then-dip test, which is not optional.
- **Paddlewheel** — no pulses travels as `None`, never `0.0 m/s`; no pulses under sustained
  thrust is the **snag** signal. A dead hall sensor and a stopped wheel look identical, which
  is why there is a spare in the BOM.
- **Spool encoder** — `read_payout_m()` returning `0.0` means *no bound known*, not *the
  sub is at the origin*.

### 6.3 Telling a dead sensor from a dropped frame

These are the two failures a bench session will confuse, and they want opposite reactions:
a dropped frame comes back on its own, a dead chip needs you to go and touch a cable. The
two marks that carry them — `--` dim for STALE, `?` amber wavy for CANNOT-TELL — are
`docs/playbook.md` §1. What matters at the bench is how to tell them apart **without
reading a single character**:

- **STALE moves as a group.** A dropped frame dashes *every* reading at once, because one
  link carries all of them. A dead chip blanks only the gauges behind that chip — depth and
  pressure together and nothing else is an MS5837; the bearing alone is a BNO085.
- **The last number is never shown for a dead sensor.** A frozen reading and a steady one
  look identical: an MS5837 that stops at 4.33 m would otherwise leave the console painting
  a confident, colour-banded 4.3 m while the sub descends to 8 m.

**Reproducing all of it with no hardware**, which is how these paths are exercised at all:

```python
hw._kill_sensor("ms5837")     # depth and pressure stop answering
...                           # drive the sim on; the simulated sub keeps sinking
hw.read_pressure()            # -> None
hw.sensor_faults()            # -> ("ms5837",)
hw._revive_sensor("ms5837")   # the connector is pushed back on
```

Killable names are `bno085`, `ina219`, `leak-probes`, `ms5837`, `sensor-thread` — the same
names `sensor_faults()` reports, and an unknown one raises rather than quietly killing
nothing. The simulation **keeps running underneath**, so the truth drifts away from the last
value the vehicle read: that is what lets a check prove the readout neither followed the
water down nor sat frozen. Test the recovery too — a gauge that goes blank and *stays*
blank after the connector is reseated is its own fault, and one nobody finds until a dive.

### 6.4 The five diagnostic readings, and what a healthy one looks like

Turn rate, forward acceleration, pitch, roll and pack current are **diagnostic**, not
navigational. Nothing safety-critical branches on attitude and the forward accelerometer is
never integrated twice into a position — they are on screen because they are the readings
that tell you *whether the vehicle is doing what you told it to*.

They are what you fly the sub on **at the bench**, before there is any water to check them
against. All five come off chips that already had a purpose here, so none of them costs a
part: four from the BNO085, one from the INA219.

Every one of them has a **real zero**, and every one of those zeroes is the calm answer —
`0.0 deg/s` is *"not turning"*, `0.0 m/s²` is *"coasting"*, `(0.0, 0.0)` is *"level"*,
`0.0 A` is *"drawing nothing"*. That is the whole reason each ships as `Optional` with no
default: a dead IMU defaulting to zeroes is a vehicle sitting perfectly still, perfectly
level, and perfectly believable.

| Reading | Chip | Sign convention | At rest, healthy | Under way, healthy |
|---|---|---|---|---|
| **turn rate** `gyro_z_dps` | BNO085 | **+ = clockwise**, compass convention | within ±0.5 °/s of zero | tracks the steer; sign matches the direction the bow swings |
| **forward accel** `accel_fwd_ms2` | BNO085, *linear* accel (gravity already removed) | **+ = ahead** | within ±0.2 m/s² of zero | a brief spike on throttle-up, back to ~0 at steady speed |
| **pitch** `pitch_deg` | BNO085 | **+ = nose up** | whatever the trim is; note it and expect it back | noses down as the ballast fills, up as it empties |
| **roll** `roll_deg` | BNO085 | **+ = starboard down** | within ±2° of zero on a trimmed hull | heels *into* a turn, and returns |
| **pack current** `current_a` | INA219, high-side shunt (§4.4) | always positive; this vehicle never charges | idle draw only — see below | rises with thrust, and it is the *comparison* that matters |

#### What each one catches

**Turn rate — the reading that proves the compass.** The BNO085's fused yaw is polluted by
the thrusters' own magnetic field; that is the entire reason `GYRO ONLY` exists. Turn rate
is the independent witness. Steer hard over on the bench and watch both:

- **heading moves, turn rate does not** → the bearing is being pushed by something that is
  not rotation. Magnetic interference, almost always the thrusters or a steel fastener too
  close to the IMU. Move the IMU, not the threshold.
- **turn rate moves, heading does not** → the magnetometer is dead or saturated. Check
  `mag_cal` on the same screen; if it is `0`, run the calibration dance in §8.5.
- **turn rate is non-zero on a bench that is not moving** → uncorrected gyro bias. A few
  tenths of a degree per second is normal and the heading filter estimates it out; a
  standing 2 °/s is a chip that never finished its startup calibration, and it will walk
  the dead-reckoned track sideways at roughly 2° per second of dive.
- **sign is backwards** → `NAV_IMU_YAW_OFFSET_DEG` does not fix this. The mounting axis is
  wrong; turn the board, do not negate the number in software, or every other axis stays
  wrong and you will find out about it underwater.

**Forward acceleration — the reading that catches a bad mount.** It is *linear*
acceleration, so gravity is already removed by the BNO085's fusion. On a level, still hull
it must sit at approximately zero.

- **a standing offset at rest** (a persistent ±1–3 m/s², and the classic value is about
  **9.8**, or a fraction of it) means gravity is leaking into the forward axis: the board is
  mounted on the wrong face, or the fusion has not converged. It is the fastest way to catch
  a 90° mounting error before it becomes a mystery in the dive log.
- **thrust applied, no acceleration, no paddlewheel pulses** → the sub is held. That is the
  **snag** signature, and this is the reading that separates *"the wheel sensor died"* from
  *"the vehicle genuinely is not moving"*: a dead hall sensor stops the pulses and leaves the
  accelerometer alone, while a snag stops both. The wheel alone cannot tell those apart,
  which is why there is a spare in the BOM.
- **acceleration with no thrust** → current, or someone pulling the tether.

**Pitch and roll — the readings that catch a build problem, not a software one.** Attitude
is advisory here, and the useful information is almost always in the *offset*, not the
value:

- **a standing roll at rest** is weight or buoyancy asymmetry. Fix it with lead and foam,
  not with a number: a hull that flies with a permanent heel loses thrust to the wrong axis
  and the camera never looks where the operator points it.
- **roll that grows during a dive and does not come back** is a compartment taking water, or
  something heavy that has broken loose inside. Check the leak stage (§6.1) on the same
  screen; roll drifting while both probes stay dry means the mass moved, not the water.
- **nose-down pitch that gets worse as the dive goes on**, with the ballast steady, is water
  in the bow. Come up.
- **pitch swinging with the ballast** is correct and expected — the syringe is not on the
  centre of buoyancy. Note the two values (§8.3 gives you a full stroke to measure them
  over) so the abnormal one is recognisable.

**Pack current — the reading that finds a fouled prop.** This is a canal-cleaning vehicle;
the propellers *will* pick up fishing line, bag handles and weed, and the fouled state is
almost invisible from topside. The camera looks forward, not at the thrusters, and a wrapped
prop still spins and still makes noise.

The number that matters is not the absolute draw, it is **current against speed**:

| What you see | What it means |
|---|---|
| draw up, paddlewheel speed down, same throttle | **something is on a prop.** The motor is working harder and moving the boat less — the definition of fouling. Stop before the driver heats up |
| draw up, speed up | you are just going faster. Nothing wrong |
| draw **down**, speed down | the opposite fault: a thruster has stopped. A disconnected motor, a stripped coupling, or an H-bridge that has gone open |
| a step change in draw with no command | a short, a stalled ballast stepper, or a lamp that has flooded and is now conducting through the water |
| draw high and rising at rest, with no thrust | a stalled motor or a shorted lamp. Kill it — a stall is the fastest way to cook a small brushed motor |

Take the numbers once on a clean hull and write them on this page; they are vehicle-specific
and nobody else's are worth anything to you. For the shape to expect,
`MockHardware.read_current_a()` in `api/hardware.py` models it as
`0.35 + 2.5·(|L|+|R|)/2 + lights`, where white contributes 0.8 A and green 0.5 A at full:

| State | Modelled draw |
|---|---|
| idle: Pi, IMU and electronics, no thrust, lamps off | **0.35 A** |
| both thrusters at full | **2.85 A** |
| white bow spots at full, no thrust | 1.15 A (**+0.80 A**) |
| both lamps at full, no thrust | 1.65 A (**+0.50 A** for the green ring) |
| **worst case** — full thrust and both lamps | **4.15 A** |

The thrusters dominate everything else, which is exactly why this number is worth a readout.
Those are **modelled** figures and not measurements — no part of this vehicle has been
bought — so treat the shape as real and the digits as placeholders.

> **The stock shunt clips before this vehicle does.** The worst case above is past §4.4's
> ±3.2 A ceiling, and a clipped reading is not a maximum — it is a ceiling wearing a
> measurement's clothes, hiding the one thing the sensor was fitted to see. Swapping to a
> 0.01 Ω shunt is a **two-place change**: `INA219_SHUNT_OHMS` in `api/hardware.py` and §4.4's
> figure, together. Changed in hardware alone it scales every amp by exactly the wrong
> factor and nothing anywhere looks broken.

#### When they say nothing

All four IMU readings are gated on the BNO085's liveness verdict, exactly like `heading` and
`mag_cal` — so they blank **together**, in the same frame, with `"bno085"` in
`sensor_faults`. Six readouts going to `?` at once with one chip named is the signature of a
dead IMU; one of them alone is not a thing that can happen, and if you ever see it, suspect
the console rather than the hull.

Pack current is gated on the INA219 and blanks **with the pack voltage**, for the same
reason: one chip, one verdict. Amps present with volts missing is likewise impossible; the
mock refuses to model it, because a test that passed against that combination would be
proving something about the mock rather than about the vehicle.

Reproduce all of it with no hardware, the same way as §6.3:

```python
hw._kill_sensor("bno085")     # heading, heading_card, mag_cal, gyro, accel, pitch, roll
hw._kill_sensor("ina219")     # pack volts AND amps — never one without the other
```

---

## 7. Power tree

```
  2S Li-ion pack, 8.4 V full / 7.4 V nominal, with BMS
        │  XT60
        ▼
   ┌─ FUSE ─────────────────────────┐   start at 7.5 A; size it on what the INA219 reports
   │                                │
   ▼                                │
  INA219 shunt  (VIN+ ▸ shunt ▸ VIN−)   HIGH SIDE, before everything else — §4.4
        │
        ▼
   MASTER SWITCH
        │
        ├──► buck 5 V / 3 A ──┬──► Raspberry Pi 3B+  (header pins 2/4)
        │                     ├──► BNO085 · MS5837 · INA219 logic  (via the Pi's 3V3 where the
        │                     │     breakout has no regulator — MS5837 is 3.3 V ONLY)
        │                     ├──► A3144 paddlewheel sensor · spool encoder
        │                     └──► green LED ring (own pair from the buck output)
        │
        ├──► buck adj. 3–5 V ──► thruster H-bridge motor supply (VM), set to the motors' rating
        │
        └──► battery-direct ──┬──► A4988 VMOT  (+100 µF at the driver)
                              └──► white bow spots via their resistor / CC driver

  EVERY ground is common: Pi GND, buck −, H-bridge GND, A4988 GND, MOSFET V− in, sensor GND.
  Star them at the switch output rather than daisy-chaining.
```

**Feeding the Pi through header pins 2/4 bypasses its USB input protection.** That is the
normal way to run a battery-powered Pi, but it means the buck's output has to be clean and
the only current limit in that path is the fuse in this diagram. Do not skip the fuse, and
do not share the buck with a load that can pull the rail down — a brown-out here is the
control system going away with the sub in the water.

If your thrusters are rated at pack voltage, feed the H-bridges **battery-direct** and the
adjustable buck comes out of the build. Read the motor's own rating first — over-volting a
small brushed motor buys speed for a few dives and then a dead thruster.

Never feed the Pi from the motor rail. A brown-out during a thruster surge takes the whole
control system down with the sub in the water.

### 7.1 Battery thresholds

Mirrors `api/config.py`. The bands are the only source of the pack's colour and the voltage
number is always shown beside it — a band is a judgement, the number is the measurement
(`docs/playbook.md` §3).

| Band | Volts | Setting | Env | What it means |
|---|---|---|---|---|
| Full | 8.4 | `battery_full_v` | `NEPTUNE_BATT_FULL` | the top of the scale |
| Dive on | ≥ 7.0 | `battery_warn_v` | `NEPTUNE_BATT_WARN` | proven good |
| Head back | < 7.0 | `battery_warn_v` | `NEPTUNE_BATT_WARN` | finish the pass |
| Surface | < 6.6 | `battery_crit_v` | `NEPTUNE_BATT_CRIT` | come up now |
| Hard floor | 6.0 | `battery_floor_v` | `NEPTUNE_BATT_FLOOR` | 3.0 V/cell — below this the cells are **damaged**, not merely flat |

Nothing in software enforces the floor. It is the number the operator must never reach,
which is exactly why it is written down instead of left to folklore.

**This pack is 2S: 8.4 V full, 7.4 V nominal.** Anything comparing against a 20–25 V scale
is describing a different vehicle and will read "full" forever on this one.

---

## 8. Calibration procedures

Every default in `api/nav/config.py` is a **placeholder** — they exist so the code runs on
the bench, not because anyone measured them. Shipping them unchanged means the map is
confidently wrong, which is worse than an obvious failure. Each procedure below produces the
number that replaces one.

| Constant | Env | Placeholder | Procedure |
|---|---|---|---|
| `m_per_pulse` | `NAV_M_PER_PULSE` | 0.05 | §8.1 |
| `m_per_spool_tick` | `NAV_M_PER_SPOOL_TICK` | 0.0005 | §8.2 |
| `ballast_span_steps` | `NEPTUNE_BALLAST_SPAN_STEPS` | 4000 | §8.3 |
| `surface_pressure_psi` | `NEPTUNE_SURFACE_PSI` | 14.7 | §8.4 |
| `psi_per_meter` | `NEPTUNE_PSI_PER_M` | 1.42 | §8.4 |
| `imu_yaw_offset_deg` | `NAV_IMU_YAW_OFFSET_DEG` | 0.0 | §8.5 |
| `filter_backend` | `NAV_FILTER` | `dr` | §8.6 |

### 8.1 Paddlewheel metres-per-pulse

Reported speed is *linear* in `m_per_pulse`, so one measured run and one multiplication
gets it. A guess here scales every measured speed — and therefore every distance — by a
constant error.

1. Mark a measured length along a canal wall. 20 m works; longer is better.
2. Leave `NAV_M_PER_PULSE` at whatever it currently is and restart the API.
3. Run the length **on the surface at a fixed throttle** (0.5 is a good one), stopwatch
   running. Do it **in both directions and average**: canals flow, the wheel measures
   water-relative speed, and one direction alone bakes the current into the constant.
4. True speed `v_true = distance / time`.
5. From the dive journal for that run, take the mean `speed_ms` over the samples where
   `speed_src` is paddle-backed (`paddle` or `kf-paddle`). Call it `v_rep`. Ignore any
   sample on the LUT — that is the model, not the wheel.
6. **New value = old value × (v_true / v_rep).** Set `NAV_M_PER_PULSE` and repeat one run to
   confirm it lands within a few percent.

Do this in the same session as `python -m nav.cli speed-cal --distance 20 --pairs …` — it is
the same set of runs, and the throttle→speed LUT is the fallback that has to agree with the
wheel when the wheel goes stale.

### 8.2 Spool metres-per-tick

The 0.0005 default assumes a ~0.3 m drum circumference over 600 ticks, which is arithmetic
rather than measurement.

1. With the sub on the bench, note `payout_m` (or restart so it reads 0).
2. Pay out a **marked length** — 10 m — by hand, off the drum, not by driving.
3. **New value = old value × (10 / reported payout).**
4. Measure at **mid-spool**. The effective circumference grows as cable layers build up, so
   a constant taken at a full drum over-reads at an empty one.

Payout is only ever used as an upper bound on range, so an over-estimate loosens the clamp
and never invents precision. Get it approximately right; do not agonise.

### 8.3 Ballast span in steps

1. Set the A4988 microstep jumpers and trim the current (§4.2) **first**. Both invalidate a
   span measured before them.
2. Attach the plunger and check the direction: `fill` must drive **toward** the FULL switch.
   If it does not, swap one coil pair at the driver.
3. Run `ballast_home()`. The plunger drives toward EMPTY until that switch opens the circuit;
   the counter zeroes and `ballast_homed` becomes true.
4. Command `fill` and let it run until the FULL switch triggers. **Record the step count.**
5. Put that number in `NEPTUNE_BALLAST_SPAN_STEPS`. Level is `steps / span`, so a wrong span
   silently rescales the whole syringe UI with nothing looking broken.
6. Repeat the home→full cycle twice more. If the count varies by more than
   `NEPTUNE_BALLAST_SPAN_TOL` (5 %), the motor is skipping: lower `NEPTUNE_BALLAST_STEP_RATE`
   or raise the current limit a little, and re-measure. That same 5 % is what raises
   `ballast_needs_rehome` in flight.

### 8.4 Surface pressure zeroing

1. Float the sub at the surface, sensor submerged, with the water still.
2. Read `pressure_psi` from telemetry and put it in `NEPTUNE_SURFACE_PSI`. It is not 14.7 —
   it is today's atmosphere plus however deep the sensor sits when the sub floats.
3. `NEPTUNE_PSI_PER_M` stays at **1.42** for fresh water (1 m of fresh water = 9.81 kPa =
   1.42 psi). Only change it for brackish water, and then only with a measurement.
4. Check it against a tape: hold the sub at a measured 2 m and confirm the readout. Depth is
   **measured, never integrated**, so this one constant is the entire depth channel.

### 8.5 BNO085: the mag-cal dance and the mounting offset

**Calibration.** Run it *in the water, away from the dock* — a steel piling calibrates the
magnetometer to the piling.

```
python -m nav.cli mag-cal
```

Move the sub through slow figure-8s and full rotations until `mag_cal` reads **3 (GOOD)** and
holds for five consecutive reads. The pre-dive check requires **≥ 2**; below that, heading is
flagged suspect everywhere it is shown, and the filter will coast on the gyro rather than
believe the compass.

**Mounting offset.** `NAV_IMU_YAW_OFFSET_DEG` is added after the ENU→compass conversion
(`heading = (90 − yaw_enu) mod 360`) and accounts for how the board actually sits in the
hull. 0.0 asserts the board's X axis points dead ahead, which it will not once it is epoxied
in. An uncorrected offset does not look like a bug — the track just leans consistently off
true.

1. Point the sub along a **known bearing**: a canal wall whose bearing you can read off the
   map, or a hand compass held well away from the hull and from anything steel.
2. **Thrusters off.** Their magnetic field is exactly what the filter exists to reject, and
   you are trying to measure the mounting, not the interference.
3. Read `heading_deg` from `/api/nav/state`.
4. `offset = wrap180(known_bearing − reported_heading)`. Set it and re-check: reported should
   now match the known bearing within a couple of degrees.
5. Re-do it after **any** re-mounting of the board, however slight.

### 8.6 Judging `NAV_FILTER` promotion with data

The estimator default is `dr` and stays there until a real dive says otherwise. Promoting the
filter is a decision to be made against data, not taste — so the replay harness runs a
recorded dive through both estimators and prints them side by side:

```
python -m nav.cli replay data/dives/dive-20260806-141032.jsonl --filter both
```

It reports, per backend: track divergence over time, final-position delta, percentage of time
**gyro-only**, percentage of time on each **speed source**, and any **snag** events.

Promote to `NAV_FILTER=filtered` when, on your own logs:

- on a dive that includes hard thruster use near steel (where the compass is polluted), the
  filtered track error against a known finishing point is **lower** than `dr`'s; and
- on a clean, gentle dive, filtered is **not worse** than `dr` beyond a small tolerance; and
- the gyro-only percentage matches what actually happened — if it says 90 % on a dive spent
  drifting at idle, the trust gate is mis-tuned and the number to fix is that, not the map.

It is an environment variable, not a code change: `NAV_FILTER=filtered`. Which also means it
is one restart to go back, and going back after a bad dive is not a failure — it is the
harness doing its job.

---

## 9. v2 — NOT in this build

Kept as interfaces with stub implementations so the protocol and the client do not have to
change later. **Do not buy these yet and do not fit them.** Every one of them is a hole in
the hull, a MOSFET and a failure mode the v1 vehicle does not need to survive its first
dive.

| Qty | Item | Interface that already exists | State in v1 |
|---|---|---|---|
| 1 | electromagnet + 1 MOSFET module | `set_magnet()` / `get_magnet()` | stubbed |
| 2 | pan/tilt micro servos | `set_camera(pan, tilt)` | **documented no-op**; the protocol fields remain and the right stick pans the map instead |
| 1 | nichrome burn-wire drop-weight + 1 MOSFET | `release_dropweight()` | logs a **loud "not fitted in v1"** warning and does nothing |

**v1 recovery is: empty the ballast and physically pull the tether.** That is the last link
of the fallback chain, it needs no software at all, and it is why the drop-weight can wait.

---

## 10. Bring-up order

Each step is provable on its own. Doing them out of order is how a fault in one subsystem
gets attributed to another. This build is **staged** — modules go on the pins one at a time,
over several evenings, not all at once on a Saturday — so the order below is also the order
the console lights up in.

### The wired flag is on from the first module, not held back to the last

`settings.hardware_wired` — the `NEPTUNE_HW_WIRED` environment variable, default **true** — is
the single switch that takes the vehicle off the bench simulator and onto the loom. It is an
assertion a human makes, because nothing in software can see a connector; `gpiozero` still has
to import as well, so a bench machine lands on the mock whatever this says. Being an
environment variable, it is stated in a place that does not need a commit — there is no source
edit to make on the vehicle.

**It defaults on, so the vehicle is on the loom from step 3, with the first module on the
pins.** Do not turn it off and wait for the last one. What makes early safe is §6.2: every
chip carries a liveness verdict, every readback behind it is gated on that verdict, and a
chip that has **never** answered is faulted exactly like one that answered and stopped. A
module that is not yet fitted therefore reads as not fitted, per chip, all the way to the
console — its gauges cannot-tell, `sensor_faults` names it, and `RealHardware.__init__` says so
on the way up:

```
RealHardware active (GPIO + I2C); not answering yet: <the chips that have not answered>
```

Which turns the flag from a certificate into an **instrument**. On from step 3, every later
step gets a free acceptance test that costs nothing to run: seat the connector, and watch that
module's gauge go from `?` to a number and its name leave `sensor_faults` in the same frame. A
module that comes alive the moment it is plugged in has proven its wiring, its address, its
power and its whole path to the operator in one motion. A module that does not says so on the
evening you wired it, rather than on the evening you meet six faults at once with nothing to
bisect.

**Two things being on the loom early does not do**, both worth knowing before you trust the
screen mid-build:

- **Actuators have no liveness verdict at all**, and cannot have one: nothing in a GPIO
  output can tell a wired H-bridge from an empty pin. A not-yet-wired thruster, stepper or
  lamp accepts every command and reports nothing wrong. Steps 4, 5 and 6 are proven by
  watching the hardware move, not by reading the console.
- **A not-yet-wired leak probe reads *dry*.** The pin's own pull-up holds it high and the
  sampler is running, so `read_leak()` answers `NORMAL` — a positive claim that the hull is
  dry, made about probes that are not there. An open circuit and a dry comb are the *same*
  circuit, so every layer above the pin reports dry, honestly. `NORMAL` means nothing until
  step 7's short-then-dip test has passed; this is the one reading that being on the loom
  early makes *less* honest, which is exactly why that test is not optional. §5.1 has the
  bench evidence.

**A group that does not come up does not take the vehicle with it.** The five GPIO groups —
**thrusters, lights, ballast, leak, pulses** — come up independently. A group that raises
leaves its devices unset, latches a fault under its own name, and the methods that would drive
it refuse rather than crash. `is_mock` stays `false`, because the vehicle *is* real — a real
vehicle with three sensors fitted, and `sensor_faults` says which three. One straight run of
constructors would instead make the first sensor you solder untestable, since bringing it up
would claim the other eleven were there too. So the boot line is worth reading in full:

```
RealHardware active (GPIO + I2C); not answering yet: bno085,i2c,ina219,ms5837
```

**The one group that is not allowed to degrade quietly is the thrusters.** Everything else
missing costs a reading; that one costs control of a vehicle in water, and the failure would
have been silent — `armed: true`, stick accepted, nothing moves. `set_armed()` refuses to arm
without the H-bridges and says so in `sensor_faults`.

And if the SIM badge does **not** go out, stop and read the log: `RealHardware.__init__`
raised before it reached the groups — a missing `gpiozero`, or `hardware_wired` false —
`NEPTUNE_HW=auto` has landed on the bench simulator, and everything on the screen is
simulated. That badge is the only notice you get. (`NEPTUNE_HW=real` refuses to fall back at
all, which is the louder version of the same check.)

### The order

1. Pi boots, I²C enabled (`raspi-config` → Interface Options, or `dtparam=i2c_arm=on`), and
   `i2cdetect -y 1` runs. On a staged build it will list **nothing** at this point, and that
   is the correct answer — §2.1's three-address check is step 8's acceptance test, not this
   one. What is being proven here is that the bus exists and the kernel drives it.
2. `python bootstrap.py` — Python, the checkout and the Pi-only hardware libraries all
   present. It installs none of them; absence is a finding, not a task for the tool.
3. **Power tree, INA219, and the flag.** Fuse fitted, shunt high side and first in the chain
   (§4.4), master switch in, and the **INA219 reading pack voltage on the bench** before any
   actuator exists. This is the first honest number the vehicle produces, which is why it is
   the module the flag rides in on: confirm `NEPTUNE_HW_WIRED` is true (it is by default) and
   run `NEPTUNE_HW=real`. *Proven when:* the SIM badge is gone, the boot line names the chips
   that are not yet fitted, pack voltage tracks a pack you can also read with a multimeter,
   and **every other sensor gauge reads cannot-tell**. That screen — one real number among a
   dozen honest blanks — is the picture the rest of the bring-up fills in, one connector at a
   time.
4. **Thrusters:** direction, deadband, and that `safe()` really stops them. Out of the water.
   Proven at the shafts, not on the screen — see the actuator caveat above.
5. **Ballast:** current limit (§4.2), `ballast_home()`, span (§8.3), and both limit switches
   proven by triggering them **by hand** and watching motion stop. The level stays `?` until
   the first successful home, which is the syringe being honest about an open-loop axis
   rather than a fault.
6. **Lights:** both channels, dim to zero and back.
7. **Leak probes:** §5.1's test in its order — **short the probe ends first**, then dip,
   both stages, both orders. Only from here on does a `NORMAL` on the console mean anything
   at all.
8. **Sensors onto the bus, one connector at a time.** BNO085, then MS5837, then the
   paddlewheel and the spool encoder. `i2cdetect -y 1` now shows `40 4a 76` — three addresses
   or the wiring is wrong. Then the readings: heading turns the right way, depth reads
   surface pressure, the paddlewheel counts when the wheel is spun by hand, the encoder
   counts **both** ways. Fit them one at a time so that each chip's `?` becoming a number,
   and its name leaving `sensor_faults` in that same frame, is unambiguous evidence about
   *that* connector.
9. **Prove the cannot-tell path, one chip at a time** (§6.2). Out of the water, with the
   console open, pull each I²C chip's connector in turn and confirm three things: only
   *that* chip's gauges go to `?`, `sensor_faults` names it, and no gauge keeps showing the
   last number it had. Then plug it back in and confirm the reading **returns** — a gauge
   that blanks correctly and never recovers is its own fault. Do the same for the leak
   probes and confirm the frame carries `leak_state: "UNKNOWN"` rather than `NORMAL`.
   This step is not optional and it is not a formality: every layer can pass its own tests
   while nobody has put a dead sensor in one end and looked at the other. The staged bring-up
   has been running the *positive* half of this test all along — a chip arriving — but that
   only proves a `?` can become a number. This step is the half that proves a number can
   become a `?` again, which is the direction that kills dives.
10. **Nothing is standing in.** With the whole loom on, confirm the vehicle is
    reading rather than modelling: `sensor_faults` empty with every gauge carrying a number,
    and the payout figure moving when cable is pulled off the drum **by hand** — not only
    when the props spin. While no spool answers, `api/nav/sensors.py` fabricates payout from
    throttle × time × 1.2 (an over-estimate: admissible as a *bound*, never as a measurement)
    and the dive journal writes it under the encoder's own name, so through every stage above
    that is what the number has been. See the pre-first-dive batch in `.specs/tasks.md`.
11. Calibrate (§8), in the water, in the order the sections are written.

---

## 11. Bring-up cards — one instrument at a time

§10 is the *order*. This is the **card you take to the bench for the one module you are
fitting tonight**: its pins, the flags, what the console does at each stage of that
module's life, how to make it move before you own the sensor, and — the part whose
absence costs evenings — what this design genuinely **cannot** tell apart.

Every card is written to be walkable end to end with nothing soldered, because the
vehicle is being built one module at a time and the alternative is discovering the
gaps with hardware in your hands.

**The cards do not restate how the console speaks.** `docs/playbook.md` is the
presentation contract and these cite it: **§1** is the state ladder (MEASURED ·
ESTIMATED · STALE · **CANNOT-TELL** · **ABSENT**) and the single look each one has,
**§2** is the marks, badges, alert chips and the four-drop leak ladder, **§3** is the
colour language, **§4** is the rule that every glyph carries a written explanation. So
"cannot-tell" below means precisely §1's `?` — amber, wavy underline, with a chip on
the rail naming the part — and "absent" means §1's quiet reading that accuses nobody.

### 11.0 What every card assumes

**The flags, set once, not per module.** `NEPTUNE_HW` picks the backend: `auto` (what
`deploy/systemd/neptune-api.service` sets) tries the real one and falls back to the
bench simulator; `real` refuses to fall back, which is the louder version of the same
check; `mock` is the simulator on purpose. `NEPTUNE_HW_WIRED` defaults **true** and
§10 explains why it goes on with the *first* module rather than the last. **No card
below changes either of them and none needs a third variable.** If a card's readings
are all suspiciously smooth, look for the SIM badge first: `auto` has fallen back and
you are flying the model (§10).

**A GPIO group coming up proves the pin was free, and nothing else.** The five groups
— `thrusters`, `lights`, `ballast`, `leak`, `pulses` — construct gpiozero devices on
pins the kernel is not already using, which succeeds on a bare Pi with no loom at all.
So a boot line that names only I²C chips is not a report that the GPIO side is wired:

```
RealHardware active (GPIO + I2C); not answering yet: bno085,i2c,ina219,ms5837
```

That line says the four I²C-backed readings have not answered yet and that no group
raised. It says nothing whatever about whether anything is connected to GPIO17.

**Reading a pin without disturbing the software that owns it.** `sudo pinctrl get 17,4`
prints each pin's function and level even while gpiozero holds it. Two facts about it
on a Pi 3B+ are worth more than they look:

- **The pull column cannot be read on this Pi and always prints `--`.** The BCM2837's
  pull control is a write-only sequence with no register exposing the current setting,
  so the tool has nothing to report — it prints `--` for GPIO2/GPIO3 as well, and
  those sit under the board's own fitted I²C pull-ups. **Read the level (`hi` / `lo`)
  and ignore the pull.** A `--` beside a pin you know has a pull-up is the tool being
  unable to look; reading it as a missing pull-up is a whole evening spent on a fault
  that is not there.
- **`sudo pinctrl set <pin> ip pu|pd` never makes the pin an output.** It leaves the
  pin an input and swaps which internal resistor is on it, so it is safe whether or
  not hardware is attached: it cannot drive current into a sensor, a switch or a
  driver. `pd` therefore reads LOW and `pu` reads HIGH, which is how every input on
  this vehicle is faked. **Restore with `ip pu`** — every input in §2's pin map uses
  the internal pull-up, so `pu` is always the way back.

**Outputs can be watched the same way.** Every PWM output on this vehicle is software
PWM (§3.1), so `sudo pinctrl get 21` sampled repeatedly reads steady `lo` at duty
zero, steady `hi` at full, and a scatter of both at anything between — which is a
complete proof that a command reached a gate, with no MOSFET, H-bridge or lamp fitted.

**Where a not-yet-fitted module lands on the ladder.** CANNOT-TELL and ABSENT are
different rungs because the operator's next move differs: cannot-tell is an errand
(go and look at that cable), absent is not (this hull does not have the part). Only
the vehicle can tell them apart, and it does: `sensors_absent` on the telemetry frame
is **a strict subset of `sensor_faults`** naming the parts that have produced nothing
since power-on. A name in `sensor_faults` but *not* in `sensors_absent` answered this
power cycle and stopped, which is the errand. Both are cannot-tell for the *reading*;
only one is a fault, and §1's rule for the other is that the readout accuses nobody.

**LEAF PARTS ONLY.** `sensors_absent` is drawn from the per-device liveness verdicts —
`bno085`, `ms5837`, `ina219`, `leak-probes`, `sensor-thread` — and from nothing else.
The latched subsystem faults are deliberately excluded, and the exclusion is the point
rather than an oversight: **absence buys silence, and silence about a fixable fault is
the failure this whole chain exists to prevent.**

Three consequences worth carrying to the bench:

- **`i2c` IS NEVER ABSENT, and while the bus is down nothing on it is either.** A bus
  that would not open is an errand with a fix — enable I²C in `raspi-config` — so it
  stands in front of every chip behind it. On a Pi with I²C not yet enabled, the
  depth, heading and pack readings are **cannot-tell with an `I2C BUS DOWN` chip**, not
  quiet. That is correct and it is what you will see: one errand named once, rather
  than three chips implying three separate broken parts. ABSENT starts doing its work
  on those chips the moment the bus opens and an individual chip is still unwired,
  which is exactly the one-at-a-time case.
- **An empty `sensors_absent` means "this backend cannot tell them apart", not
  "nothing is absent"**, and everything unfitted then reads as a part that broke.
  That default is the loud one on purpose: reporting a fault as an absence silences a
  real failure, reporting an absence as a fault costs one wasted walk.
- **A group that comes up on empty pins is in neither list.** All five GPIO groups
  construct fine with no loom (above), so `leak`, `pulses`, `ballast`, `thrusters` and
  `lights` say nothing at all until something actually raises — and a group that *does*
  raise is a latched subsystem fault, so it is named in `sensor_faults` and never in
  `sensors_absent`. Absence is only ever reported for a leaf part with a liveness
  verdict; the rest is on the walk.

Each card says which rung its module lands on and why.

**Actuators have no rung at all.** Nothing in a GPIO output can distinguish a wired
H-bridge from an empty pin, so the thruster, ballast and light cards are proven by
watching hardware move (or by watching the gate pin, above) and never by reading a
number back off the console. That is a property of the wiring, not a gap in the
software, and it is stated on each of those cards rather than left to be discovered.

---

### 11.1 Leak probes — GPIO17 (WARN) and GPIO4 (FLOOD)

**Fit this one first and walk it in full.** It is the only reading on the vehicle
whose unwired state is *reassuring*, and this vehicle's first attempt at it was
abandoned after a bench session spent on water, comb geometry and pull-up arithmetic
while the fault was in the leads (§5.1). The card is ordered so that the cheapest test
that bisects the whole chain comes first.

| | |
|---|---|
| Pins | **GPIO17** (header 11) = WARN, at the lowest point · **GPIO4** (header 7) = FLOOD, 2 cm higher. Both `in, pull-up`; **wet = LOW** |
| Bus | none — two wires and a pin, deliberately not on I²C (§6.1) |
| Group | `leak` |
| Flags | `NEPTUNE_HW=auto` (or `real`), `NEPTUNE_HW_WIRED=true` — the defaults. Nothing else |
| On the wire | `leak` (bool), `leak_state` (`NORMAL`/`WARN`/`FLOOD`/`UNKNOWN`), `leak_probe_fault`, `leak_rearms`; `leak-probes` in `sensor_faults` |
| Timing | `leak_debounce_samples` (5) consecutive wet samples at 10 Hz — about half a second — per probe, independently |
| Build | §5.1 (the combs, the epoxy, the arithmetic) · §6.1 (the four stages and the re-arm command) |

#### What the dashboard shows

| Stage | What you see (`docs/playbook.md` §2, the leak ladder) |
|---|---|
| **not yet wired** | `NORMAL` — the **green struck-through drop**, tooltip "both probes dry", and the pre-dive check passes with "both probes sane". **The console is not lying and this is not a bug**: an open circuit and a dry comb are the same circuit, so every layer above the pin reports dry, honestly. `NORMAL` means nothing until the walk below has passed. This reading is the one thing that being on the loom early makes *less* honest (§10) |
| **first good read** | there is no distinct arrival. The probes do not announce themselves; the only evidence they exist is the walk below producing `WARN` on demand |
| **triggered** | **WARN** — amber half-filled drop, `WATER COLLECTING · FINISH UP` on the rail, and the drop becomes a **button** (it is only focusable off `NORMAL`). **FLOOD** — red solid drop, the full-screen edge pulse, `FLOOD · SURFACE NOW`, and the tether glyph becomes the red pulsing sub (playbook §2, connection glyphs), so a leak can never be read as a link dropout |
| **dead or unplugged** | **a disconnected probe is not detectable and shows `NORMAL`** — see below. What *is* detected is the sampler stopping: `leak_state: UNKNOWN`, the amber **broken-outline drop with a `?`**, and `HULL STATE UNKNOWN · LEAK PROBES STOPPED` as a **critical** chip. Wet outranks cannot-tell, so a standing WARN or FLOOD never decays to UNKNOWN |
| **revived** | latching is one-way: drying the probe leaves the stage where it was. Press the drop (`leak_reset`). While either probe is wet the vehicle **refuses** and the ack carries the sentence naming which probe, so the button never appears to do nothing. Dry, it clears, the drop returns to green, and `leak_rearms` increments — the console can then say this `NORMAL` was restored by hand rather than never having been in doubt |

**ABSENT does not apply to the hull-integrity readout, and must not be made to.**
`leak_state` is a required string with `UNKNOWN` spelled out as its own cannot-tell
(§6.1), and the drop shapes on that string alone. If the `leak` GROUP ever fails to
construct that is a latched subsystem fault — named in `sensor_faults`, never in
`sensors_absent`, which carries leaf parts only — and the drop must stay the
broken-outline `?` with its critical chip on the rail regardless. Note the deliberate
asymmetry: `leak-probes` (the sampler) *is* a leaf part and can be absent, and even
then the drop stays loud. Every other reading's absence is a fact that asks nothing of anybody;
*nobody is watching the hull* is never that fact.

#### Prove it with no probes, no water and no wiring

`pd` puts a pull-down on the pin, which is exactly what water does. It drives nothing,
so it is safe with the probes fitted or absent.

```
sudo pinctrl get 17,4                 # baseline: both should read `hi` (dry / open)
sudo pinctrl set 17 ip pd             # the WARN probe is now "wet"
                                      # -> WARN on the console within about half a second
sudo pinctrl set 17 ip pu             # dry again; the WARN latch STAYS — that is correct
                                      # -> press the drop to re-arm; it goes green
```

**FLOOD must be driven in the physical order — 17 first, then 4.** Water cannot reach
the upper probe without covering the lower one, so upper-wet-with-lower-dry is the
impossible combination the design checks for, and driving GPIO4 on its own raises
`leak_probe_fault: warn+flood` alongside the FLOOD — correctly, and confusingly if you
were not expecting it:

```
sudo pinctrl set 17 ip pd             # lower probe wet   -> WARN
sudo pinctrl set 4 ip pd              # upper probe too   -> FLOOD, no probe fault
sudo pinctrl set 17 ip pu ; sudo pinctrl set 4 ip pu      # dry; both latches stand
                                      # -> re-arm from the drop
```

Driving GPIO4 alone is worth doing **on purpose, once**, to see the probe-fault chip
and prove that half of §5.1 works. Then re-arm.

#### The five-minute walk, in the order that bisects fastest

1. **Pins, before anything is connected.** `sudo pinctrl get 17,4` reads `hi`, `hi`.
2. **Drive the pin from the Pi** (above) and watch the console reach WARN. This tests
   every layer *above* the pin — sampler, debounce, telemetry, ingest, render — in one
   move. If it fails here, no wiring will fix it and the fault is not in the hull.
3. **Short the two probe ends together, dry**, with a jumper or by touching them. That
   is zero ohms through the real wiring — GPIO lead, contact, ground lead, header pin
   — and it *must* give WARN. If step 2 passed and this does not, the fault is in the
   leads or the pin seating, which is exactly the fault this vehicle has (§5.1).
4. **Only now, dip the comb.** With steps 2 and 3 passing, a dip that does nothing is
   a statement about the comb itself.
5. **Repeat for FLOOD, in the physical order**, and re-arm at the end so the vehicle
   is left certified rather than latched.

**Never test with a wet finger.** §5.1 has the arithmetic: skin is two to three orders
of magnitude too resistive to pull the pin down, so a finger across the combs proves
nothing and reads exactly like a dead probe.

#### What cannot be distinguished, and why

- **Dry and disconnected are the same circuit.** A bare digital input with a pull-up
  reads HIGH when the comb is dry, when a lead has come off, when a crimp has pulled,
  and when nothing was ever fitted. There is no version of this design that tells them
  apart, so **the walk above is not optional** and `NORMAL` is only worth what the last
  walk was worth. The two things the vehicle *can* catch are in §5.1: a probe wet at
  power-on in a hull sealed dry, and the physically impossible upper-wet-lower-dry.
  Both surface as `leak_probe_fault`, and either of them stops `NORMAL` being offered
  at all — the hull state drops to `UNKNOWN` rather than certifying itself on a probe
  the vehicle has already named as broken.
- **The debounce cannot tell a splash from the start of ingress.** It is not trying
  to: half a second of continuous wet is the whole test, and that is what makes the
  FLOOD alarm worth believing (§5.1).
- **A re-arm cannot be undone or zeroed.** `leak_rearms` counts up for the life of the
  process; only a service restart puts it back to nought. That is deliberate — the
  count is the console's evidence that a reassurance was restored by hand.

---

### 11.2 MS5837-30BA — depth and pressure, I²C `0x76`

| | |
|---|---|
| Bus | I²C1 — GPIO2 (SDA, header 3) / GPIO3 (SCL, header 5), address **0x76**, fixed. **3.3 V only**; 5 V destroys it |
| Group | none — I²C, brought up by `_open_depth()`, not by a GPIO group |
| Flags | the shared two. **I²C must be enabled** (`raspi-config` → Interface Options, or `dtparam=i2c_arm=on`), or `/dev/i2c-1` does not exist and the bus faults as a whole |
| On the wire | `depth`, `pressure` (null together, always — depth is arithmetic on pressure); `ms5837` in `sensor_faults` |
| Liveness | 2 consecutive raises, or 2.5 s of silence (§6.2). The streak is deliberately short: the failure path backs off to one retry a second |
| Calibration | §8.4 — `NEPTUNE_SURFACE_PSI` is *today's* atmosphere plus the sensor's float depth, never 14.7 |

#### What the dashboard shows

| Stage | What you see |
|---|---|
| **not yet wired** | **Depends on whether the BUS is up, and this is the step people get wrong.** With I²C **not enabled** (`/dev/i2c-1` missing) you get **cannot-tell** — `?`, amber, wavy — and `NO DEPTH & PRESSURE · I2C BUS DOWN` on the rail. That is correct and it is *not* the chip accusing you: the bus is an errand with a fix (`raspi-config`), so it is named once and stands in front of every chip behind it. **Expect the chip on the rail here; the card is not lying to you.** With the bus **up** and this chip simply not fitted, the reading is ABSENT — quiet, no chip, nothing to go and look at (playbook §1), because a leaf part that has never answered is named in `sensors_absent`. Once the chip **has** answered and then stops, it is cannot-tell with `NO DEPTH & PRESSURE · DEPTH SENSOR STOPPED` |
| **first good read** | the number appears **tinted by its depth band** (playbook §3 — the depth colours mean how deep and nothing else) and `ms5837` leaves `sensor_faults` in the same frame. That one frame proves the wiring, the address, the 3.3 V feed and the whole path at once |
| **actuating** | not an actuator. Under way the number tracks the water; at the surface it must read the surface pressure you set in §8.4 |
| **dead or unplugged** | both readings go to `?` **in the same frame** and the last number is never shown. Depth and pressure blanking together with nothing else blanking is the signature of this chip (§6.3) |
| **revived** | reseat the connector and both fill in by themselves. A gauge that blanks correctly and never recovers is its own fault — §10 step 9 exists for that half |

#### Prove it without the sensor

There is no Pi-side trick for an I²C chip: nothing on the Pi can answer at `0x76`, and
`I2C_BUS` is a constant in `api/hardware.py` rather than an environment variable, so
the bus cannot be pointed elsewhere without a code change. The card therefore splits:

- **The console half is fully walkable on the bench**, with no Pi at all, using
  `NEPTUNE_HW=mock` and the kill/revive fixture in §6.3 (`hw._kill_sensor("ms5837")`
  … `hw._revive_sensor("ms5837")`). The simulation keeps running underneath, which is
  what lets you prove the readout neither followed the water down nor sat frozen.
- **The vehicle half is proven by arrival.** Fit the chip with the console open and
  watch `?` become a number and `ms5837` leave `sensor_faults` in one frame (§10 step
  8). Before that, `i2cdetect -y 1` listing `76` is the wiring test.

#### What cannot be distinguished, and why

- **Depth and pressure are one instrument, so they can never disagree**, and a console
  showing one without the other would be describing a failure the hardware cannot
  have. Do not read that as two independent confirmations.
- **A chip that answers with rubbish is not a chip that stopped.** Liveness catches
  raises and silence (§6.2); it cannot catch a sensor that returns plausible, wrong
  numbers. That is what §8.4's tape check against a measured 2 m is for.

---

### 11.3 BNO085 IMU — the compass, I²C `0x4A`

| | |
|---|---|
| Bus | I²C1, address **0x4A** (0x4B if the address pin is pulled high). 3.3 V logic |
| Group | none — I²C, `_open_imu()` |
| Flags | the shared two, plus I²C enabled |
| On the wire | `heading`, `heading_card`, `mag_cal`, `gyro_z_dps`, `accel_fwd_ms2`, `pitch_deg`, `roll_deg` — **all six readings null together**, because they are one chip; `bno085` in `sensor_faults` |
| Liveness | 5 consecutive raises, or 1.0 s of silence (§6.2) — it is polled at the 50 Hz loop rate |
| Calibration | §8.5 — the mag-cal dance *and* `NAV_IMU_YAW_OFFSET_DEG`, re-done after any re-mounting |
| Placement | more than 20 cm from the paddlewheel magnets (§5.2), or `mag_cal` degrades silently and the whole track leans |

#### What the dashboard shows

| Stage | What you see |
|---|---|
| **not yet wired** | **With I²C up** and the chip simply not fitted: there is no bearing and the badge reads **`NO COMPASS`** — playbook §2's word for *no IMU ever answered* — because `bno085` is a leaf part named in `sensors_absent`, so nothing accuses anyone and no chip goes on the rail. **With I²C not enabled** you get **`NO BEARING`** and `I2C BUS DOWN` on the rail instead: the bus is never absent, it is an errand, and a reading is only ABSENT when every part behind it was never fitted. Expect the chip until you enable the bus. Contrast **`NO BEARING`**, which is the same missing number reported as an errand and is what a chip that answered and stopped produces. `MAG?`, `GYRO`, `NO COMPASS` and `NO BEARING` are four badges rather than one because the operator's next move differs in each; collapsing any pair sends them the wrong way. The four inertial readings go quiet behind that one badge with the ATTITUDE group flag marking them, rather than raising four more chips for one connector. **With no heading there is no track** (`.specs/design.md` §24.3): the map does not advance and the radar holds the last angle a compass actually gave |
| **first good read** | the bearing appears; `mag_cal` arrives as a number. Below 2 it is `MAG?` — a bearing that exists and is suspect (dotted underline), not a missing one. Run §8.5 |
| **actuating** | steer on the bench and watch heading against turn rate — §6.4 is the whole diagnostic, and it is the pair that separates magnetic interference from a dead magnetometer |
| **dead or unplugged** | all six go to `?` in one frame, the badge changes to **`NO BEARING`** (it answered this dive and stopped), and the rail carries `NO BEARING · COMPASS STOPPED`. `mag_cal` must go null with it: a frozen `3` beside a frozen bearing is the strongest trust mark the system has, attached to a chip that is silent |
| **revived** | the bearing returns. The badge going from `NO BEARING` back to blank (or `MAG?`) is the proof |

#### Prove it without the sensor

Same split as §11.2: bench-side with `NEPTUNE_HW=mock` and
`hw._kill_sensor("bno085")` / `_revive_sensor`, which blanks exactly the six readings
and no others; vehicle-side by arrival, with `i2cdetect -y 1` showing `4a`.

#### What cannot be distinguished, and why

- **`NO COMPASS` and `NO BEARING` are the same null on the wire**, and nothing on the
  handheld can tell them apart: never-answered versus answered-and-stopped is a fact
  only the vehicle holds, which is what `sensors_absent` exists to carry (§11.0). A
  hull too old to send it puts an unfitted IMU on the loud badge — the safe direction
  to be wrong in, and worth knowing if you are flying a console newer than the Pi.
- **A polluted heading and a wrong mounting offset look identical from one reading.**
  Turn rate is the independent witness (§6.4); `NAV_IMU_YAW_OFFSET_DEG` does not fix a
  reversed sign, because a reversed sign is a mounting axis (§8.5).

---

### 11.4 INA219 — pack voltage and pack current, I²C `0x40`

| | |
|---|---|
| Bus | I²C1, address **0x40** (0x41 / 0x44 / 0x45 via A0/A1). Shunt **high side, first in the chain** — §4.4 |
| Group | none — I²C, `_open_power()` |
| Flags | the shared two, plus I²C enabled |
| On the wire | `battery_v`, `current_a` — one chip, so they null together; `ina219` in `sensor_faults` |
| Liveness | 2 consecutive raises, or 5.0 s of silence — polled at ~2 Hz (§6.2) |
| Bands | §7.1 — 2S: 8.4 full / 7.0 warn / 6.6 critical / 6.0 floor |

#### What the dashboard shows

| Stage | What you see |
|---|---|
| **not yet wired** | ABSENT — the quiet reading with no chip, because `ina219` has never answered (§11.0). Once it has answered and then stops, cannot-tell: `NO PACK VOLTAGE & CURRENT · PACK MONITOR STOPPED` on the rail with the DRAW tile blank beside it. **What you must never see, in either state, is `BATTERY 0.0V · SURFACE`** — a red critical alarm invented entirely by an absent sensor. If that appears, something upstream is substituting 0.0 for a null and the chain has a hole in it (`.specs/design.md` §24.2) |
| **first good read** | the voltage appears, coloured **only** by §7.1's bands with the number always beside it (playbook §3: a band is a judgement, the number is the measurement). This is the first honest number the vehicle produces and the module the wired flag rides in on (§10 step 3) |
| **actuating** | not an actuator, but it is the reading that watches the actuators: §6.4's current-against-speed table is what finds a fouled prop |
| **dead or unplugged** | volts and amps blank **together**, in the same frame, with one chip named. Amps present with volts missing is impossible on this hardware; the mock refuses to model it |
| **revived** | both fill in together |

#### Prove it without the sensor

Bench-side, `hw._kill_sensor("ina219")` blanks the pair (§6.4). Vehicle-side, arrival:
`i2cdetect -y 1` shows `40`, and pack voltage must agree with a multimeter across the
pack — that comparison, not the presence of a number, is the acceptance test.

#### What cannot be distinguished, and why

- **A clipped reading is not a maximum.** The stock 0.1 Ω shunt reads to ±3.2 A and
  §6.4's modelled worst case is past it, so a pinned reading looks like a measurement
  and is a ceiling. Changing the shunt is a two-place change — `INA219_SHUNT_OHMS` and
  §4.4 — and doing it in hardware alone scales every amp by exactly the wrong factor
  with nothing looking broken.
- **Low-side wiring cannot be detected in software.** A shunt on the wrong side of the
  load reads a comfortable, plausible lie. §4.4's diagram is the only check.

---

### 11.5 Paddlewheel — GPIO10

| | |
|---|---|
| Pin | **GPIO10** (header 19), `in, pull-up`, **edge interrupt**. A3144 hall sensor to 5 V and GND, output to the pin |
| Group | `pulses` |
| Flags | the shared two |
| On the wire | `speed_ms`, `speed_src`, `snagged` — all three are **navigation's** answers, not the hardware's (`api/main.py`, `fill_nav_fields`) |
| Liveness | **none, and none is possible** — see below |
| Calibration | §8.1 — `NAV_M_PER_PULSE`, measured over a marked length in both directions |
| Build | §5.2 — sensor inside the hull wall, out of the prop wash, more than 20 cm from the IMU |

#### What the dashboard shows

| Stage | What you see |
|---|---|
| **not yet wired** | the SPEED tile shows `?` with a tag: **`NO SPEED`** when nothing reported a speed, **`NO DATUM`** when navigation has no launch point to measure against. Those are different sentences and only one of them is about the wheel. Both are cannot-tell shapes (playbook §1) rather than the stale dash, because neither comes back on its own |
| **first good read** | the number arrives as a plain figure to the centimetre with **no tag** — blank means the wheel measured it. An estimate from the throttle curve wears `~` and `EST` and never dresses as a measurement (playbook §2) |
| **actuating** | speed rises with throttle. Thrust up with no measured speed, sustained, is the **`SNAGGED`** chip — the shopping-trolley detector, and LUT speed never counts as evidence for it |
| **dead or unplugged** | **indistinguishable from a stopped wheel.** No pulses travels as `null` — *nothing measured the speed* — never as `0.0 m/s`, because "slower than I can see" and "stopped" are different claims (§5.2). The forward accelerometer (§6.4) is the reading that separates a dead hall sensor from a genuinely pinned sub: a snag stops both, a dead sensor stops only the pulses |
| **revived** | the number returns as soon as pulses do. There is no fault name to clear |

#### Prove it without the wheel

Each `pd` is one falling edge, which is one magnet passing. Two magnets sit in
opposing paddles, so two pulses is one revolution:

```
sudo pinctrl set 10 ip pd ; sudo pinctrl set 10 ip pu     # one pulse
```

Repeat at a steady rate to fake a steady speed. **You will see nothing on the SPEED
tile until a launch point is set**: with no origin the tile reads `?` / `NO DATUM`
whatever the wheel does, because speed is navigation's answer and navigation has
nothing to measure against. That is the single most likely way to conclude a working
paddlewheel is dead. Set the origin first, then pulse the pin.

The A3144 is a **unipolar** switch. If the wheel spins and nothing counts, flip the
magnets before suspecting anything else (§5.2).

#### What cannot be distinguished, and why

- **A dead hall sensor reads exactly like a stationary wheel**, and nothing in a bare
  pulse input can separate them. There is no liveness verdict here and there cannot
  be one — which is why the BOM carries a spare sensor, and why §6.4's forward
  acceleration is on screen at all.
- **The wheel cannot sense direction.** The sign of the speed comes from the commanded
  throttle. It also stalls below about 0.1 m/s, and that stall is reported as
  cannot-tell rather than as zero.
- **Prop wash defeats it silently.** A wheel in the wash spins whether or not the sub
  is moving, so a sub pinned at full thrust reports a healthy cruise and the snag
  detector never fires. Siting is the only fix (§5.2).
- **An unfitted wheel cannot reach ABSENT either.** The ladder's quiet rung needs a
  *part* named behind the reading, and speed has none — no chip, no liveness verdict,
  nothing to declare never-fitted. So a wheel that does not exist and one that has
  stopped both land on the same `NO SPEED`, and the tag is as specific as this
  instrument gets.

---

### 11.6 Spool encoder — GPIO9 (A) and GPIO11 (B)

| | |
|---|---|
| Pins | **GPIO9** (header 21) = A, **GPIO11** (header 23) = B. Both `in, pull-up`, both edges of both channels |
| Group | `pulses` |
| Flags | the shared two. **Leave SPI disabled** (the default): enabling it makes the kernel drive GPIO9/10/11 and presents as a spool that counts by itself on a stationary sub (§2) |
| On the wire | `payout_m` on the nav frame (`/ws/nav`) and in the dive journal — not on the telemetry frame |
| Liveness | none, as with the paddlewheel |
| Calibration | §8.2 — `NAV_M_PER_SPOOL_TICK`, measured at **mid-spool** |
| Electrical | check the output stage first (§5.3): a push-pull 5 V encoder wired to a Pi input damages the Pi |

#### What the dashboard shows

| Stage | What you see |
|---|---|
| **not yet wired** | payout reads **0.0, which means *no bound known*, not *the sub is at the origin*** — and while no spool answers, `api/nav/sensors.py` fabricates payout from throttle × time × 1.2 and the dive journal writes it under the encoder's own name (§10 step 10). So the figure on screen before this module exists is a *bound*, never a measurement |
| **first good read** | the number moves when cable is pulled off the drum **by hand**, with the props stopped. That is the acceptance test, and it is specifically the one the fabricated bound cannot pass |
| **actuating** | not an actuator. Rewinding **reduces** payout — the quadrature is the whole point, because reeling cable in genuinely tightens the range bound |
| **dead or unplugged** | indistinguishable from a stationary drum. The number simply stops moving |
| **revived** | counting resumes; there is no fault name to clear |

#### Prove it without the encoder

One quadrature cycle is four edges in Gray order. Both channels are pulled up, so
complementing both bits maps the cycle onto itself — the phase shifts, the direction
does not, and this works whichever way the pull-ups sit:

```
sudo pinctrl set 9 ip pd
sudo pinctrl set 11 ip pd
sudo pinctrl set 9 ip pu
sudo pinctrl set 11 ip pu          # one cycle — repeat to pay out
```

Reverse the order (11 first) to reel in and watch the number come back down. As with
the paddlewheel, **payout only surfaces once a dive is running**, so set the origin
and start the dive before driving the pins, or nothing will appear anywhere. If the
number moves the wrong way, swap A and B at the connector rather than negating
anything in software.

#### What cannot be distinguished, and why

- **Payout is a bound, not a position.** `read_payout_m()` returning 0.0 says *no
  bound known*; it never says the sub is at the origin. Negative ticks — a drum turned
  past its start, or A and B swapped — clamp at zero rather than reporting negative
  tether.
- **A stalled encoder and a stationary drum are the same silence**, exactly as with
  the paddlewheel, and for the same reason: no liveness is possible on a bare pulse
  input. It cannot reach ABSENT either — there is no part named behind payout to be
  declared never-fitted — so an encoder that does not exist reads as a bound of zero,
  which is why the acceptance test is the *number moving by hand* and not the number
  being present.

---

### 11.7 Ballast — A4988 stepper (GPIO23/24/25) and both limit switches (GPIO22/27)

| | |
|---|---|
| Pins | **GPIO23** STEP · **GPIO24** DIR (high = fill) · **GPIO25** /EN, **active low** · **GPIO22** limit EMPTY · **GPIO27** limit FULL. Both switches `in, pull-up`, **NC-to-GND** |
| Group | `ballast` — the axis bookkeeping is built either way, so the readbacks always have something coherent to answer with |
| Flags | the shared two |
| On the wire | `ballast_level` (null until homed), `ballast_target`, `ballast_homed`, `ballast_needs_rehome`; `ballast-limits` in `sensor_faults` |
| Settings | `NEPTUNE_BALLAST_SPAN_STEPS` (§8.3) · `NEPTUNE_BALLAST_STEP_RATE` · `NEPTUNE_BALLAST_SPAN_TOL` |
| Wiring | §4.2 — the 100 µF across VMOT is not optional, and the current-limit trim comes before the plunger |

**The switch sense is inverted on purpose.** At rest the closed NC contact holds the
pin **LOW** = *not at the limit*; reaching the limit opens the contact and the pull-up
takes it **HIGH** = *triggered*. A cut lead, a pulled crimp and an unfitted switch all
read HIGH too, so a broken switch fails to a **stop** rather than to a silent absence
(§4.2).

#### What the dashboard shows

| Stage | What you see |
|---|---|
| **not yet wired** | `ballast_level` is **null** and the syringe reads `?` with `BALLAST NOT HOMED · PRESS HOME` on the rail. That is the axis being honest about an open-loop stepper, not a fault. **And both limit pins read HIGH, which is "both triggered"** — physically impossible — so the first move command latches `ballast-limits`, refuses to move and refuses to home. Homing on a phantom switch would zero the counter wherever the plunger happens to be, which is worse than not homing |
| **first good read** | the level appears only after a successful home: the counter zeroes against the EMPTY switch and `ballast_homed` goes true. `0.0` then means *empty*, and it is a measurement rather than a placeholder for the first time |
| **actuating** | the fill grows in the depth colours (playbook §3), the target marker sits where you set it, and motion stops **in that direction** the instant a switch triggers, mid-command |
| **dead or unplugged** | a stepper that is not moving looks exactly like one that is — there is no position sensor. The one thing that *is* caught is the FULL switch closing at a count that disagrees with the span by more than the tolerance: `ballast_needs_rehome`, `BALLAST LOST COUNT · RE-HOME`, and the level is not to be believed until homing repeats |
| **revived** | re-home. `ballast_home()` drives toward EMPTY, zeroes the counter and clears `needs_rehome` — the count is freshly referenced against real metal, which is what the flag was asking for |

#### Prove it with no motor and no switches

`pd` = the switch contact is closed = **not** at that limit. `pu` = the contact is open
= **triggered**. So the whole homing path walks from the keyboard:

```
sudo pinctrl set 22 ip pd ; sudo pinctrl set 27 ip pd   # neither limit reached
                                                        # -> the ballast-limits fault clears
                                                        #    on the next move command
#   press HOME on the console: the axis walks toward EMPTY
sudo pinctrl set 22 ip pu                               # the EMPTY switch closes
                                                        # -> counter zeroes, ballast_homed
                                                        #    true, the syringe stops saying ?
#   command FILL and let it run
sudo pinctrl set 27 ip pu                               # the FULL switch closes
#   trigger it EARLY, well before a full stroke, to see BALLAST LOST COUNT · RE-HOME
sudo pinctrl set 22 ip pu ; sudo pinctrl set 27 ip pu   # back to the unwired state,
                                                        # i.e. both reading triggered
```

The STEP and DIR pins can be watched at the same time: `sudo pinctrl get 23,24,25`
shows /EN low (driver enabled) while a move is running and DIR high for fill.

Walking this leaves the axis homed on a count about a plunger that does not exist, and
only a restart of the API clears that — harmless on a bench, but do not read the level
afterwards as anything. Re-home once the motor is fitted, and take the real span with §8.3 —
`ballast_span_steps` is a count in whatever microstep mode the jumpers select, and a
wrong span silently rescales the whole syringe UI with nothing looking broken.

#### What cannot be distinguished, and why

- **The axis is open-loop: the position is a number being kept, not a thing anything
  can see.** A skipped step under load is invisible until a limit switch disagrees
  with the count, which is why the FULL-switch check exists and why the level is null
  rather than 0.0 before the first home.
- **A broken switch and a triggered switch are the same reading**, deliberately —
  that is what NC-to-ground buys. The cost is that an unfitted pair reads as *both*
  triggered, which the code catches only because both at once is impossible.
- **Nothing can see whether the plunger is attached.** The counter walks happily with
  the motor uncoupled. §8.3 step 2 — checking that `fill` drives *toward* the FULL
  switch — is the only proof the mechanism is connected the way the software thinks.

---

### 11.8 Thrusters — GPIO12/5/6 (left) and GPIO13/16/26 (right)

| | |
|---|---|
| Pins | **GPIO12** EN (L) · **GPIO5** IN1 · **GPIO6** IN2 · **GPIO13** EN (R) · **GPIO16** IN1 · **GPIO26** IN2. Software PWM at `thruster_pwm_hz` (§3.1) |
| Group | `thrusters` — **the one group that is not allowed to degrade quietly** |
| Flags | the shared two |
| On the wire | `armed`, `left`, `right` — `left`/`right` are the **commanded** mix, echoed back. There is no feedback path |
| Settings | `NEPTUNE_THRUSTER_DEADBAND` (0.05) · `NEPTUNE_THRUSTER_PWM_HZ` |
| Wiring | §4.1 · the PWM channel trap is §3, and GPIO18/19 stay empty so nobody can reintroduce it |

#### What the dashboard shows

| Stage | What you see |
|---|---|
| **not yet wired** | **nothing is wrong on screen, and nothing can be.** The `thrusters` group comes up perfectly well on six empty pins, so the vehicle arms, the sticks move, `left`/`right` echo them and no gauge blanks — an unwired output has no reading to withhold. The one case that *is* caught is the group failing to construct at all (a pin already taken): `set_armed(True)` then **refuses**, the vehicle stays disarmed and `thrusters` appears in `sensor_faults`, because arming a sub whose H-bridges never came up hands the operator a live console and a vehicle that cannot answer it |
| **first good read** | there isn't one. Proof happens at the shafts (§10 step 4) or on the gate pins (below) |
| **actuating** | the direction pins take the sign and the EN duty takes the magnitude. Below the deadband both direction pins go low and duty is forced to **0**, not to a trickle — a whining idle sounds exactly like a fault to whoever is holding the tether |
| **dead or unplugged** | undetectable from the console. A disconnected motor, a stripped coupling or an open H-bridge shows up as **draw down, speed down at the same throttle** (§6.4) — the INA219 and the paddlewheel are the instruments that watch the thrusters, not the thrusters themselves |
| **revived** | equally undetectable. Watch the shaft |

#### Prove it without an H-bridge

Arm the vehicle, push the stick past the deadband, and read the pins:

```
sudo pinctrl get 5,6,12          # left bridge: one direction pin hi, EN chopping
sudo pinctrl get 16,26,13        # right bridge
```

Sampled repeatedly, an EN pin reads steady `lo` at rest, steady `hi` at full and a
scatter of both in between — that is software PWM, and it is a complete proof that the
command reached the pin with nothing fitted. Reverse the stick and watch IN1/IN2 swap.
**Disarm and confirm both EN pins go steady `lo`**, then hold STOP and confirm the
same; those two are the checks that matter, and they are checkable here without a
single motor in the boat.

If a thruster runs backwards once the motors are on, **swap OUT1/OUT2 at the bridge**
— never in software, or the next person reads the pin map and gets a sub that spins
(§4.1).

#### What cannot be distinguished, and why

- **No GPIO output can tell a wired H-bridge from an empty pin.** There is no liveness
  verdict for any actuator and there cannot be one. The console will accept every
  command and report nothing wrong on a vehicle with no motors at all.
- **Thruster jitter is not a wiring fault.** Every PWM output here is thread-timed
  (§3.1), so the duty slips whenever the Pi is busy and a steady stick can produce a
  humming, twitching motor — worst at low duty. Knowing that before you re-crimp a
  harness that is fine is the entire reason §3.1 is written out.

---

### 11.9 The two light channels — GPIO21 (green ring) and GPIO20 (white spots)

| | |
|---|---|
| Pins | **GPIO21** (header 40) = green hull ring · **GPIO20** (header 38) = white, **both 3 W bow spots switched as one channel**. Software PWM at `light_pwm_hz` (200 Hz) |
| Group | `lights` |
| Flags | the shared two |
| On the wire | `light_green`, `light_white`, `light_green_level`, `light_white_level` — the **commanded** state, recorded whether or not a lamp is on the pin |
| Wiring | §4.3 — one MOSFET module per channel, module signal ground to the Pi's ground, and the green strip fed as its own pair from the buck |

#### What the dashboard shows

| Stage | What you see |
|---|---|
| **not yet wired** | the lamp buttons work and show exactly what you asked for. The commanded state is recorded even with no lamp on the pin, deliberately: dropping the command instead would leave the switch flicking back on its own with no explanation anywhere. If the *group* failed to come up, `lights` appears in `sensor_faults` saying the command went nowhere |
| **first good read** | there isn't one — this is an actuator. The lamp lighting is the test |
| **actuating** | the button reflects on/off and level. `set_light_level()` treats anything ≤ 0.02 as off, so the dashboard's "on" state and a duty of zero cannot disagree |
| **dead or unplugged** | undetectable. A blown MOSFET module, a flooded lamp and a working one look identical from the console. A lamp that has flooded and is conducting through the water shows as **a step change in pack draw with no command** (§6.4) |
| **revived** | equally undetectable from the console |

#### Prove it without a lamp

The gate pin carries the whole answer:

```
sudo pinctrl get 21              # green gate: steady `lo` with the lamp off
#   turn the green channel on at full from the console
sudo pinctrl get 21              # steady `hi`
#   set the level to half
sudo pinctrl get 21              # sampled repeatedly: a mix of `hi` and `lo`
```

Repeat for GPIO20. Steady-off, steady-on and a scatter at part duty is the full
signature of a working software-PWM channel with nothing fitted, and it separates
"the command never reached the pin" from "the module or the lamp is dead" before any
soldering iron comes out.

#### What cannot be distinguished, and why

- **Same rule as the thrusters: an output has no readback.** The console reports what
  it commanded, always, and can never report what the LED did.
- **A flickering lamp is a grounding fault, not a duty fault.** A floating gate
  reference is the classic cause (§4.3), and it looks like a software problem from
  every angle except the multimeter.
- **PWM a constant-current driver's dimming input, never its supply.** Chopping the
  input at 200 Hz buys inrush, flicker and a driver that runs hot for no visible
  reason — and none of that is distinguishable on screen from a lamp that is simply
  dim.
