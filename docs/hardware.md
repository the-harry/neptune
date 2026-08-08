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

`RealHardware._gpio_available()` returns a hardcoded `wired = False`, so `NEPTUNE_HW=auto`
falls back to the bench simulator and the dashboard keeps its SIM badge. Flip that flag when
the **first** module goes on the pins — §10 step 3 — not when the last one does. A backend
reporting `mock: false` while every sensor returned a constant would be strictly worse than
an honest simulation, because the console would present `0.0 V`, `heading 0`, "at the
surface" as instrument readings; that is the failure the flag was put there to prevent, and
it is why this page used to say flip it last. Since `bd743ad` it is not the failure you get:
a chip that is not answering blanks its own gauges and names itself, so an unwired module
reads as unwired instead of as a comfortable number. §10 explains what that buys a staged
build, and what it still does not cover.

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
| 1 | 2S Li-ion pack with BMS | 8.4 V full, 7.4 V nominal. **The 24 V scale is dead** — see §7 |
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
| BNO085 | 20–50 Hz | heading feeds the filter; slower and the gyro integration gets coarse |
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
  collects. Amber, advisory: *water is collecting, finish up*.
- **FLOOD** probe **2 cm higher**. Red, pulsing, plus a SURFACE prompt.

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
- **Nothing in hardware can tell "dry" from "disconnected" on a bare digital input.** So the
  pre-dive dip test is not optional: touch a wet finger or a damp sponge across each comb
  before the lid goes on and watch the state change on the dashboard. That takes five seconds
  and is the only proof the probe is alive.

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
- **It stalls below about 0.1 m/s.** No pulses is reported as *stale* (`None`), never as
  0.0 m/s — "slower than I can see" and "stopped" are different claims, and only the throttle
  can tell them apart. No pulses with high thrust is the **snag** signal.

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

| Stage | Probe | Dashboard | Meaning |
|---|---|---|---|
| `NORMAL` | both dry, **and both actually read** | nothing | — |
| `WARN` | low probe wet ≥ 5 samples | amber, the sub glyph **changes shape** | water is collecting; finish up, non-blocking |
| `FLOOD` | upper probe wet ≥ 5 samples | red **pulsing sub** + SURFACE prompt | come up now |
| `UNKNOWN` | nobody is sampling the probes | `leak: true` on the wire | the hull is **not being watched** — treat as unverified, not as dry |

`read_leak()` returns `FLOOD` if the flood probe is wet regardless of the warn probe, else
`WARN` if the warn probe is wet, else `NORMAL` **only if the probes are actually being
sampled**, else `UNKNOWN`. The two probes are independent — that is why the
impossible-combination check in §5.1 works.

**Why there is a fourth state.** `NORMAL` is not the absence of news, it is a positive
safety claim: *both probes were read, and both were dry*. Leak detection was the only
reading on this vehicle with no liveness gate at all, and the probes were sampled inside
the same try-block as the I²C ticks — so one unexpected raise from a bus chip skipped the
rest of the tick, the probes stopped being sampled entirely, and the vehicle went on
answering `NORMAL` at full telemetry rate. Every other gauge correctly blanked and named
its chip; the hull-integrity readout, the one that decides whether the dive is
recoverable, stayed green on evidence nobody was collecting.

**Wet outranks cannot-tell.** Water that has already reached a probe is an established
fact and the sampler stopping afterwards does not un-establish it, so a latched `WARN` or
`FLOOD` never decays to `UNKNOWN`. Only the *reassurance* needs liveness. The gate sits
between `WARN` and `NORMAL`, and nowhere else.

The FLOOD presentation is kept deliberately distinct from a **link dropout**: a leak is
the sub telling you something, a dropout is the sub telling you nothing, and confusing the
two sends the operator to the wrong action.

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

| Chip / subsystem | Streak · silence | What stops | On the wire | On screen |
|---|---|---|---|---|
| **BNO085** (0x4A) | 5 · 1.0 s | heading, mag-cal, gyro rate, linear accel, pitch/roll | `heading`, `heading_card`, `mag_cal` → `null`; `"bno085"` in `sensor_faults` | HDG shows **`?`** amber wavy; badge **`NO BEARING`**; radar stays on the LAST angle the compass gave; alert chip `NO BEARING · SENSOR STOPPED` |
| **MS5837** (0x76) | 2 · 2.5 s | depth and pressure — they are one chip | `depth`, `pressure` → `null`; `"ms5837"` in `sensor_faults` | DEPTH and PRESSURE both **`?`** amber wavy, **and their depth-band colour drops**; alert chip `NO DEPTH & PRESSURE · SENSOR STOPPED` |
| **INA219** (0x40) | 2 · 5.0 s | pack voltage and pack current — same chip, so they die together | `battery_v`, `current_a` → `null`; `"ina219"` in `sensor_faults` | the voltage loses its band colour and its tooltip says it is not tracking the battery (see the caveat below) |
| **leak probes** (GPIO17/4) | 3 · 1.0 s | the *reassurance* only; latched wet states survive | `leak_state` → `"UNKNOWN"`, `leak` → `true`; `"leak-probes"` in `sensor_faults` | see the caveat below |
| **sensor thread** | — · 1.0 s | everything, because it is what fills every cache | the chips above age out and fault behind it; `"sensor-thread"` in `sensor_faults` | every gated gauge goes to `?` together |
| **I²C bus** | latched at open | all three chips at once | `"i2c"` in `sensor_faults` | one fault named, not three — the console says "the whole I2C bus (so every chip on it)" |

The windows are sized to how often each device is actually polled (§2.2), which is why they
differ. The MS5837's streak is deliberately **short**: its failure path backs off to one
retry a second, so a long streak would let the backoff decide how long a dead depth sensor
keeps showing its last depth.

`sensor_faults` uses the same designations as this document and the wiring diagram, on
purpose — the console names the part you are going to go and unplug. An **empty** list is
not a clean bill of health: a backend that cannot track liveness reports empty, so the
`null` on each individual reading is the authoritative claim and the list only supplies the
cause.

> **Two gaps in the console as it stands, worth knowing at the bench.** The vehicle side of
> both is correct and checkable on the wire; the topside rendering has not caught up.
> **(a)** A dead INA219 nulls the voltage, but the readout falls back to `--V` rather than
> the `?` the depth and heading gauges use, and raises no alert chip. **(b)** A
> `leak_state` of `UNKNOWN` is currently collapsed to `NORMAL` topside, so the drop glyph
> reads "both probes dry" while nothing is sampling them. Until both are fixed, confirm a
> probe or pack fault against `sensor_faults` in the telemetry frame rather than against
> the glyph. This is exactly the class of defect §24 is about: the chain is only as honest
> as its weakest link.

**What is NOT covered by any of this.** Liveness gating applies to the I²C chips, the leak
sampler and the sensor thread. The remaining GPIO inputs have no equivalent, because
nothing in a bare digital input can distinguish a working quiet pin from a dead one:

- **Leak probes** — a dead probe reads *dry* forever. Covered instead by the
  impossible-combination check (§5.1) and the five-second dip test, which is not optional.
- **Paddlewheel** — no pulses is reported as *unknown*, never `0.0 m/s`; no pulses under
  sustained thrust is the **snag** signal. A dead hall sensor and a stopped wheel look
  identical, which is why there is a spare in the BOM.
- **Spool encoder** — `read_payout_m()` returning `0.0` means *no bound known*, not *the
  sub is at the origin*.

### 6.3 Telling a dead sensor from a dropped frame

These are the two failures a bench session will confuse, and they want opposite reactions:
one comes back on its own and one needs you to go and touch a cable. So they are given
different **shapes**, not different colours.

| On screen | What happened | What to do |
|---|---|---|
| `42.7`, coloured by its band | the sensor is reporting | nothing |
| **`--`**, dim, and **the whole bar dashes together** | **STALE** — the tether went quiet for a moment; the socket is still open | nothing; it returns by itself |
| **`?`**, amber, with a **wavy underline** | **CANNOT-TELL** — the chip behind that reading has stopped answering | waiting will not help; check that chip's wiring |

Two tells that separate them without reading a single character:

- **STALE moves as a group.** A dropped frame dashes *every* reading at once, because one
  link carries all of them. A dead chip blanks only the gauges behind that chip — depth and
  pressure together and nothing else is an MS5837; the bearing alone is a BNO085.
- **The last number is never shown for a dead sensor.** A frozen reading and a steady one
  look identical, and that is precisely how an MS5837 that stopped at 4.33 m had the console
  painting a confident, colour-banded 4.3 m while the sub descended to 8 m.

The alert chip names the part (`NO DEPTH · SENSOR STOPPED`, "the vehicle names the MS5837
depth/pressure sensor"), which is the difference between a blank gauge that reads as a
dashboard glitch — something you wait out — and one that reads as an errand.

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
that tell you *whether the vehicle is doing what you told it to*, and every one of them was
being produced by the hull and displayed nowhere. Four of them reached no readout at all;
pack current was spent inside the pack tooltip, which nobody hovers on a canal bank in
sunlight with wet hands.

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
`MockHardware.read_current_a()` in `api/hardware.py` models it — read out on the ROG Ally,
2026-08-07:

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

> **The stock shunt clips before this vehicle does.** §4.4's 0.1 Ω shunt with the driver's
> 320 mV gain setting reads to **±3.2 A**, and full throttle plus both lamps is above that
> on the numbers above. A clipped reading is not a maximum — it is a ceiling wearing a
> measurement's clothes, and the one thing it hides is the overload you fitted the sensor
> to see. If your peak reaches it, fit a 0.01 Ω shunt and change `INA219_SHUNT_OHMS` in
> `api/hardware.py` and §4.4 **together**; a shunt swapped in hardware alone scales every
> amp by exactly the wrong factor and nothing anywhere looks broken.

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

Mirrors `api/config.py`. **One colour, one meaning** — the colour comes only from these
bands, and the voltage number is always shown next to it.

| Band | Volts | Setting | Env | Dashboard |
|---|---|---|---|---|
| Full | 8.4 | `battery_full_v` | `NEPTUNE_BATT_FULL` | the top of the scale |
| Dive on | ≥ 7.0 | `battery_warn_v` | `NEPTUNE_BATT_WARN` | green |
| Head back | < 7.0 | `battery_warn_v` | `NEPTUNE_BATT_WARN` | amber — finish the pass |
| Surface | < 6.6 | `battery_crit_v` | `NEPTUNE_BATT_CRIT` | red + SURFACE prompt |
| Hard floor | 6.0 | `battery_floor_v` | `NEPTUNE_BATT_FLOOR` | 3.0 V/cell — below this the cells are **damaged**, not merely flat |

Nothing in software enforces the floor. It is the number the operator must never reach,
which is exactly why it is written down instead of left to folklore.

**The old 24 V scale is obsolete.** Anything still comparing against 20–25 V is describing a
different vehicle and will read "full" forever on this one.

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

### The flag goes on early now, and here is why that changed

`RealHardware._gpio_available()`'s `wired` flag is the single switch that takes the vehicle
off the bench simulator and onto the loom. **Flip it at step 3, with the first module on the
pins.** Not at the end.

That is the opposite of what this section said until now, and the reversal is worth reading
rather than obeying, because the old advice was right when it was written. Before `bd743ad`,
`RealHardware` answered with constants for everything it could not read. Flip the flag then,
with three empty I²C sockets, and the vehicle came up reporting `mock: false`, `0.0 V`,
`heading 0` and "at the surface": the SIM badge went out and the console drew every one of
those constants as an instrument reading. In that world an early flip was an act of
dishonesty, and the only safe moment was after every readback had been proven real — so
"last" was the correct instruction, for the correct reason.

`bd743ad` removed the constants. Every chip now carries a liveness verdict, every readback
behind it is gated on that verdict, and a chip that has **never** answered is faulted exactly
like one that answered and stopped (§6.2). A module that is not yet fitted therefore reads as
not fitted, per chip, all the way to the console: its gauges show `?` in amber with the wavy
underline, `sensor_faults` names it, and `RealHardware.__init__` says so on the way up —

```
RealHardware active (GPIO + I2C); not answering yet: <the chips that have not answered>
```

Which turns the flag from a certificate into an **instrument**. Flipped at step 3, every
later step gets a free acceptance test that costs nothing to run: seat the connector, and
watch that module's gauge go from `?` to a number and its name leave `sensor_faults` in the
same frame. A module that comes alive on screen the moment it is plugged in has proven its
wiring, its address, its power and its whole path to the operator in one motion. A module
that does not has said so on the evening you wired it — instead of on the evening you flip a
flag and meet six faults at once with nothing to bisect.

**Three things the early flip does not do**, all worth knowing before you trust the screen
mid-build:

- **Actuators have no liveness verdict at all**, and cannot have one: nothing in a GPIO
  output can tell a wired H-bridge from an empty pin. A not-yet-wired thruster, stepper or
  lamp accepts every command and reports nothing wrong. Steps 4, 5 and 6 are still proven by
  watching the hardware move, exactly as before.
- **A not-yet-wired leak probe reads *dry*.** The pin's own pull-up holds it high and the
  sampler is running, so `read_leak()` answers `NORMAL` — a positive claim that the hull is
  dry, made about probes that are not there. `NORMAL` means nothing until step 7's dip test
  has passed. This is the one reading the early flip makes *less* honest, which is precisely
  why that dip test is not optional.
- **Not every gauge has learned to say `?` yet**, so three readouts will lie to a
  half-built loom rather than blank. The box in §6.2 has two: a dead INA219 nulls the
  voltage but the readout falls back to `--V` and raises no alert chip, and a `leak_state`
  of `UNKNOWN` is collapsed to `NORMAL` topside. The third is SPEED, which still renders a
  null as the **stale** `--` — the dropped-frame mark — rather than `?` (`.specs/tasks.md`
  → Open). For those three, read `sensor_faults` in the telemetry frame rather than the
  glyph until they are fixed.

And if the SIM badge does **not** go out when you flip the flag, stop and read the log:
`RealHardware.__init__` raised, `NEPTUNE_HW=auto` has landed on the bench simulator, and
everything on the screen is simulated. That badge is the only notice you get. (`NEPTUNE_HW=real`
refuses to fall back at all, which is the louder version of the same check.)

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
   the module the flag rides in on: now flip `wired = True` in
   `RealHardware._gpio_available()` and run `NEPTUNE_HW=real`. *Proven when:* the SIM badge
   is gone, the boot line names the chips that are not yet fitted, pack voltage tracks a pack
   you can also read with a multimeter, and **every other sensor gauge shows `?`** — bar the
   three readouts named above, which have not learned the shape yet. That screen — one real
   number among a dozen honest blanks — is the picture the rest of the bring-up fills in, one
   connector at a time.
4. **Thrusters:** direction, deadband, and that `safe()` really stops them. Out of the water.
   Proven at the shafts, not on the screen — see the actuator caveat above.
5. **Ballast:** current limit (§4.2), `ballast_home()`, span (§8.3), and both limit switches
   proven by triggering them **by hand** and watching motion stop. The level stays `?` until
   the first successful home, which is the syringe being honest about an open-loop axis
   rather than a fault.
6. **Lights:** both channels, dim to zero and back.
7. **Leak probes:** the dip test (§5.1), both stages, both orders. Only from here on does a
   `NORMAL` on the console mean anything at all.
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
   This step is not optional and it is not a formality: this exact failure shipped three
   times, because every layer passed its own tests and nobody put a dead sensor in one end
   and looked at the other. A staged bring-up has been running the *positive* half of this
   test all along — a chip arriving — but that only proves a `?` can become a number. This
   step is the half that proves a number can become a `?` again, which is the direction that
   kills dives.
10. **Nothing is standing in any more.** With the whole loom on, confirm the vehicle is
    reading rather than modelling: `sensor_faults` empty with every gauge carrying a number,
    and the payout figure moving when cable is pulled off the drum **by hand** — not only
    when the props spin. While no spool answers, `api/nav/sensors.py` fabricates payout from
    throttle × time × 1.2 (an over-estimate: admissible as a *bound*, never as a measurement)
    and the dive journal writes it under the encoder's own name, so through every stage above
    that is what the number has been. See the pre-first-dive batch in `.specs/tasks.md`.
11. Calibrate (§8), in the water, in the order the sections are written.
