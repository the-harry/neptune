// NEPTUNE brainstem — ESP32-WROOM-32 firmware. Arduino C++, one sketch.
//
// THE JOB (docs/hardware.md §8): own every sensor and slow actuator, run the
// reflexes that must survive a hung Pi, and stream the truth up one USB serial
// cable as line-delimited JSON. The Pi half of this cable is api/brainstem.py,
// whose module docstring is the protocol contract — the two are kept in step BY
// HAND. Protocol version: PROTO below.
//
// THE HONESTY RULE, same as the Pi's (docs/hardware.md §13): a chip that is not
// answering ships null for every one of its readings IN THE SAME FRAME that
// names it in "faults" — never a frozen number, never a plausible zero. Each
// chip carries a liveness verdict (consecutive raises OR silence; never-answered
// is faulted), which is the same DeviceHealth rule the Pi runs, ported.
//
// STRUCTURE (top to bottom): config · LEDC compat · liveness · pulse ISRs ·
// I2C chips (BNO085 via the one library; MS5837 + INA219 raw-register, ported
// from the Pi's known-good implementation) · leak zones · pump/ballast · lamp,
// beacon, burn · reflexes · bench mode · ring buffer · JSON out · commands ·
// setup/loop.
//
// LIBRARY DEPENDENCIES: exactly one — "Adafruit BNO08x" (Library Manager; pulls
// in Adafruit BusIO). The BNO085 speaks SH-2, a framed report protocol that
// would be a driver project of its own; everything else here is raw Wire, so
// the loop never sleeps: the MS5837's 17.2 ms conversions run as a state
// machine exactly as they did on the Pi, because a blocking read would stall
// pump metering and telemetry together.
//
// NOTHING IN loop() BLOCKS. Pulses are counted in IRAM ISRs; timing is
// millis()/micros() subtraction (overflow-safe); Serial is polled. The burn
// FIRE pulse, the beacon pattern and the pump run are all state machines.

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_BNO08x.h>
#include <esp_system.h>
#include <math.h>

// ---------------------------------------------------------------------------
// Identity and protocol
// ---------------------------------------------------------------------------
#define FW_NAME "neptune-brainstem"
#define FW_VERSION "0.1.0"
#define PROTO 1
#define BAUD 115200

// ---------------------------------------------------------------------------
// Pin map — mirrors docs/hardware.md §8. Change it THERE and here together.
// ---------------------------------------------------------------------------
#define PIN_I2C_SDA 21
#define PIN_I2C_SCL 22
#define PIN_BNO_INT 19 // reserved; the Adafruit driver polls, INT is wired for later
#define PIN_LEAK_FWD 34 // input-only — EXTERNAL 100k pull-up to 3V3 REQUIRED
#define PIN_LEAK_MID 35 // input-only — EXTERNAL 100k pull-up to 3V3 REQUIRED
#define PIN_LEAK_AFT 39 // input-only — EXTERNAL 100k pull-up to 3V3 REQUIRED
#define PIN_NTC 36     // ADC1 — 3V3 — NTC — (pin) — 10k — GND
#define PIN_FLOW_BALLAST 27 // YF-TM02 inline on the pump tube (internal pull-up)
#define PIN_FLOW_SPEED 16   // YF-TM02 speed log (internal pull-up)
#define PIN_PAS_A 25        // PAS ring quadrature A (internal pull-up)
#define PIN_PAS_B 26        // PAS ring quadrature B (internal pull-up)
#define PIN_PUMP_IN1 18 // pump H-bridge IN1 (LEDC). See PUMP DIRECTION note below
#define PIN_PUMP_IN2 17 // pump H-bridge IN2 (LEDC), or -1 if single-MOSFET build
#define PIN_LAMP 23     // white lamp gate — LEDC 8 kHz, above camera banding
#define PIN_BEACON 13   // red locator gate
#define PIN_BURN_ARM 14 // burn interlock half 1 — held high while armed
#define PIN_BURN_FIRE 33 // burn interlock half 2 — pulsed to fire
#define PIN_BURN_SENSE 32 // OPTIONAL continuity divider; set HAS_BURN_SENSE 1 when fitted

#define HAS_BURN_SENSE 0

// PUMP DIRECTION. A peristaltic pump reverses by reversing its motor, which
// needs an H-bridge (a spare DRV8871 is the natural part: IN1/IN2 above map
// straight onto it). The handoff's power tree drew a single IRLZ44N, which can
// only ever FILL — if that is what gets built, set PIN_PUMP_IN2 to -1: the
// firmware then refuses empty-direction commands and trim_home out loud rather
// than pretending. This is flagged in docs/hardware.md §19's watch list.

// ---------------------------------------------------------------------------
// I2C addresses and chip constants
// ---------------------------------------------------------------------------
#define ADDR_BNO085 0x4A
#define ADDR_MS5837 0x76
#define ADDR_INA219_PACK 0x40 // pack monitor — high side, first in the chain
#define ADDR_INA219_RAIL 0x41 // thruster 8 V rail (A0 jumper bridged)
#define INA219_SHUNT_OHMS 0.1f // the breakout's stock shunt; change with the part

// ---------------------------------------------------------------------------
// Rates, windows, thresholds
// ---------------------------------------------------------------------------
#define TLM_HZ 10           // telemetry rate up the wire
#define LEAK_HZ 10          // leak zone sampling — the debounce budget derives from this
#define LEAK_DEBOUNCE_N 5   // consecutive wet samples to latch (~0.5 s)
#define IMU_INTERVAL_US 20000 // 50 Hz reports from the BNO085
#define NTC_BETA 3950.0f
#define NTC_R_FIXED 10000.0f
#define NTC_R0 10000.0f     // 10k at 25 °C
#define PACK_UNDERVOLT_V 9.0f  // 3.0 V/cell — the documented hard floor
#define PACK_RECOVER_V 9.3f
#define UNDERVOLT_HOLD_MS 5000
#define BURN_ARM_TIMEOUT_MS 60000UL // an ARM nobody fires expires by itself
#define BURN_FIRE_MS 1500          // the pulse the bench bucket-test calibrates
#define PUMP_ML_PER_PULSE 0.2f // PLACEHOLDER until the bench measures it (handoff §15)
#define PUMP_HOME_SILENT_MS 3000  // pumping out with no flow this long = bag empty
#define PUMP_FAULT_SILENT_MS 4000 // pumping IN with no flow this long = tube/sensor fault
#define SPEED_STALE_US 2000000UL  // no speed pulse for 2 s = not fresh (stall/stopped)
#define BALLAST_CAPACITY_ML 250.0f // working swing of the part-filled 500 ml flask

// Liveness windows per chip — sized to how often each is actually read, same
// numbers and same reasoning as the Pi carried (docs/hardware.md §13).
#define BNO_FAIL_STREAK 5
#define BNO_SILENCE_MS 1000
#define MS_FAIL_STREAK 2
#define MS_SILENCE_MS 2500
#define INA_FAIL_STREAK 2
#define INA_SILENCE_MS 5000
#define LEAK_FAIL_STREAK 3
#define LEAK_SILENCE_MS 1000

// ---------------------------------------------------------------------------
// LEDC compatibility — the API changed in arduino-esp32 core 3.0
// ---------------------------------------------------------------------------
#define LEDC_RES_BITS 10
#define LEDC_MAX ((1 << LEDC_RES_BITS) - 1)

#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
static void pwmAttach(int pin, uint32_t freq, int /*legacy_channel*/) {
  ledcAttach(pin, freq, LEDC_RES_BITS);
}
static void pwmWrite(int pin, int /*legacy_channel*/, uint32_t duty) { ledcWrite(pin, duty); }
#else
static void pwmAttach(int pin, uint32_t freq, int channel) {
  ledcSetup(channel, freq, LEDC_RES_BITS);
  ledcAttachPin(pin, channel);
}
static void pwmWrite(int /*pin*/, int channel, uint32_t duty) { ledcWrite(channel, duty); }
#endif
#define CH_PUMP1 0
#define CH_PUMP2 1
#define CH_LAMP 2

// ---------------------------------------------------------------------------
// Liveness — DeviceHealth, ported. Streak OR silence faults; never-answered is
// faulted. millis-based; all comparisons by subtraction so rollover is safe.
// ---------------------------------------------------------------------------
struct DeviceHealth {
  const char *name;
  uint8_t failStreak;
  uint32_t silenceMs;
  uint8_t fails = 0;
  bool ever = false;
  uint32_t lastOkMs = 0;
  DeviceHealth(const char *n, uint8_t streak, uint32_t silence)
      : name(n), failStreak(streak), silenceMs(silence) {}
  void ok(uint32_t now) {
    fails = 0;
    ever = true;
    lastOkMs = now;
  }
  void failed() {
    if (fails < 250) fails++;
  }
  bool faulted(uint32_t now) const {
    if (!ever) return true;
    if (fails >= failStreak) return true;
    return (uint32_t)(now - lastOkMs) > silenceMs;
  }
};

DeviceHealth hBno("bno085", BNO_FAIL_STREAK, BNO_SILENCE_MS);
DeviceHealth hMs("ms5837", MS_FAIL_STREAK, MS_SILENCE_MS);
DeviceHealth hInaPack("ina219", INA_FAIL_STREAK, INA_SILENCE_MS);
DeviceHealth hInaRail("ina219-rail", INA_FAIL_STREAK, INA_SILENCE_MS);
DeviceHealth hLeak("leak-probes", LEAK_FAIL_STREAK, LEAK_SILENCE_MS);
DeviceHealth *ALL_HEALTH[] = {&hBno, &hMs, &hInaPack, &hInaRail, &hLeak};
const int N_HEALTH = 5;

// ---------------------------------------------------------------------------
// Bench mode (announced simulation) and per-chip kill/revive fault injection.
// The same test hooks MockHardware gives the Pi bench, reachable over the wire
// — so the whole console pipeline can be exercised against a bare devkit on a
// breadboard before a single sensor arrives. Bench readings are SIMULATED and
// every frame says so ("mode":"bench"); the Pi surfaces that as mock=true.
// ---------------------------------------------------------------------------
bool benchMode = false;
bool killBno = false, killMs = false, killInaPack = false, killInaRail = false, killLeak = false;
float simHeading = 284.0f;
float simPackV = 12.4f; // a healthy 3S an hour off the charger
float simDepthPsi = 14.7f;

// ---------------------------------------------------------------------------
// Pulse inputs — ISRs do the least work possible; the loop does the maths.
// Period math, not counting: one pulse pair gives a full-precision reading,
// and resolution improves as the vehicle slows (docs/hardware.md §11).
// ---------------------------------------------------------------------------
volatile uint32_t flowBallastPulses = 0;
volatile uint32_t flowSpeedPulses = 0;
volatile uint32_t speedLastUs = 0;
volatile uint32_t speedIntervalUs = 0;
volatile int32_t pasTicks = 0;
volatile uint8_t pasPrev = 0;
volatile uint32_t pasLastUs = 0;

void IRAM_ATTR isrFlowBallast() { flowBallastPulses++; }

void IRAM_ATTR isrFlowSpeed() {
  uint32_t now = micros();
  flowSpeedPulses++;
  uint32_t last = speedLastUs;
  if (last != 0) speedIntervalUs = now - last;
  speedLastUs = now;
}

// Quadrature: the same Gray-code table the Pi's decoder used. Both edges of
// both channels; an impossible transition (two edges missed) moves nothing.
const int8_t QUAD_STEP[16] = {0, -1, +1, 0, +1, 0, 0, -1, -1, 0, 0, +1, 0, +1, -1, 0};
void IRAM_ATTR isrPas() {
  uint8_t cur = (digitalRead(PIN_PAS_A) << 1) | digitalRead(PIN_PAS_B);
  int8_t step = QUAD_STEP[(pasPrev << 2) | cur];
  pasPrev = cur;
  if (step != 0) {
    pasTicks += step;
    pasLastUs = micros();
  }
}

// ---------------------------------------------------------------------------
// BNO085 — the one library-driven chip. Lazy begin with periodic retry, so a
// bare board boots clean and the IMU comes alive the moment its connector
// seats (the arrival test the staged bring-up is built on).
// ---------------------------------------------------------------------------
Adafruit_BNO08x bno;
bool bnoUp = false;
uint32_t bnoNextRetryMs = 0;
float cHeading = NAN, cGyroZ = NAN, cAccelFwd = NAN, cPitch = NAN, cRoll = NAN;
int cMagCal = -1; // -1 = cannot tell (ships as null)

static void bnoEnableReports() {
  bno.enableReport(SH2_ROTATION_VECTOR, IMU_INTERVAL_US);
  bno.enableReport(SH2_GYROSCOPE_CALIBRATED, IMU_INTERVAL_US);
  bno.enableReport(SH2_LINEAR_ACCELERATION, IMU_INTERVAL_US);
}

static void bnoTryBegin(uint32_t now) {
  if (bnoUp || (int32_t)(now - bnoNextRetryMs) < 0) return;
  bnoNextRetryMs = now + 2000;
  if (bno.begin_I2C(ADDR_BNO085, &Wire)) {
    bnoEnableReports();
    bnoUp = true;
  }
}

static void bnoTick(uint32_t now) {
  if (benchMode || !bnoUp) return;
  if (bno.wasReset()) bnoEnableReports(); // the chip brownout-resets quietly; re-arm it
  sh2_SensorValue_t v;
  bool got = false;
  // Drain what the chip has this pass (a few reports at most at 50 Hz).
  for (int i = 0; i < 8 && bno.getSensorEvent(&v); i++) {
    got = true;
    switch (v.sensorId) {
      case SH2_ROTATION_VECTOR: {
        float qi = v.un.rotationVector.i, qj = v.un.rotationVector.j;
        float qk = v.un.rotationVector.k, qr = v.un.rotationVector.real;
        // ENU yaw counts counter-clockwise from EAST; a compass counts
        // clockwise from NORTH — offset AND flip, exactly the Pi's conversion.
        float yawEnu = atan2f(2.0f * (qr * qk + qi * qj), 1.0f - 2.0f * (qj * qj + qk * qk)) * 180.0f / PI;
        cHeading = fmodf(90.0f - yawEnu + 720.0f, 360.0f);
        float sinP = 2.0f * (qr * qj - qk * qi);
        sinP = constrain(sinP, -1.0f, 1.0f);
        cPitch = -asinf(sinP) * 180.0f / PI; // + = nose up
        cRoll = atan2f(2.0f * (qr * qi + qj * qk), 1.0f - 2.0f * (qi * qi + qj * qj)) * 180.0f / PI;
        cMagCal = v.status; // 0..3, the report's own accuracy — the mag-cal ladder
        break;
      }
      case SH2_GYROSCOPE_CALIBRATED:
        // +Z up, counter-clockwise positive; compass convention is clockwise.
        cGyroZ = -v.un.gyroscope.z * 180.0f / PI;
        break;
      case SH2_LINEAR_ACCELERATION:
        cAccelFwd = v.un.linearAcceleration.x; // board +X points ahead
        break;
      default:
        break;
    }
  }
  if (got) hBno.ok(now);
}

// ---------------------------------------------------------------------------
// MS5837-30BA — raw-register state machine, ported from the Pi's known-good
// implementation (PROM CRC + dead-bus shapes rejected, OSR-8192 conversions
// collected a tick later, second-order compensation). Non-blocking on purpose.
// ---------------------------------------------------------------------------
uint16_t msProm[7];
bool msPromOk = false;
uint8_t msStage = 0;
uint32_t msNextMs = 0, msD1 = 0, msD2 = 0;
uint32_t msNextRetryMs = 0;
float cPressPsi = NAN, cWaterC = NAN;

static bool i2cWriteByte(uint8_t addr, uint8_t b) {
  Wire.beginTransmission(addr);
  Wire.write(b);
  return Wire.endTransmission() == 0;
}

static bool i2cReadBlock(uint8_t addr, uint8_t reg, uint8_t *out, uint8_t n) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)addr, (int)n) != n) return false;
  for (uint8_t i = 0; i < n; i++) out[i] = Wire.read();
  return true;
}

static uint8_t ms5837Crc4(uint16_t prom[7]) {
  uint16_t words[8];
  for (int i = 0; i < 7; i++) words[i] = prom[i];
  words[7] = 0;
  words[0] &= 0x0FFF;
  uint16_t rem = 0;
  for (int i = 0; i < 16; i++) {
    rem ^= (i % 2) ? (words[i >> 1] & 0x00FF) : (words[i >> 1] >> 8);
    for (int b = 0; b < 8; b++) rem = (rem & 0x8000) ? ((rem << 1) ^ 0x3000) : (rem << 1);
  }
  return (rem >> 12) & 0x0F;
}

static bool ms5837PromValid(uint16_t prom[7]) {
  // A held-low or floating bus reads one value everywhere, and all-zeros CRCs
  // to the 0x0 nibble it also fabricated — reject the dead-bus shapes before
  // the CRC gets a vote, exactly as the Pi did.
  bool allSame = true;
  for (int i = 1; i < 7; i++)
    if (prom[i] != prom[0]) allSame = false;
  if (allSame) return false;
  for (int i = 1; i < 7; i++)
    if (prom[i] == 0x0000 || prom[i] == 0xFFFF) return false;
  return ms5837Crc4(prom) == (prom[0] >> 12);
}

static void msTryBegin(uint32_t now) {
  if (msPromOk || (int32_t)(now - msNextRetryMs) < 0) return;
  msNextRetryMs = now + 2000;
  if (!i2cWriteByte(ADDR_MS5837, 0x1E)) return; // reset
  delay(10);                                    // PROM reload — begin path only, never the loop
  uint8_t b[2];
  for (int i = 0; i < 7; i++) {
    if (!i2cReadBlock(ADDR_MS5837, 0xA0 + 2 * i, b, 2)) return;
    msProm[i] = ((uint16_t)b[0] << 8) | b[1];
  }
  if (!ms5837PromValid(msProm)) return;
  msPromOk = true;
  msStage = 0;
  msNextMs = now;
}

static void msCompensate() {
  // Datasheet arithmetic in int64, second-order terms included — without them
  // the reading drifts by centimetres of phantom depth as the water cools.
  int64_t C1 = msProm[1], C2 = msProm[2], C3 = msProm[3], C4 = msProm[4], C5 = msProm[5], C6 = msProm[6];
  int64_t dT = (int64_t)msD2 - C5 * 256;
  int64_t TEMP = 2000 + (dT * C6) / 8388608;
  int64_t OFF = C2 * 65536 + (C4 * dT) / 128;
  int64_t SENS = C1 * 32768 + (C3 * dT) / 256;
  int64_t Ti, OFFi, SENSi;
  if (TEMP < 2000) {
    Ti = (3 * dT * dT) / 8589934592LL;
    OFFi = (3 * (TEMP - 2000) * (TEMP - 2000)) / 2;
    SENSi = (5 * (TEMP - 2000) * (TEMP - 2000)) / 8;
    if (TEMP < -1500) {
      OFFi += 7 * (TEMP + 1500) * (TEMP + 1500);
      SENSi += 4 * (TEMP + 1500) * (TEMP + 1500);
    }
  } else {
    Ti = (2 * dT * dT) / 137438953472LL;
    OFFi = ((TEMP - 2000) * (TEMP - 2000)) / 16;
    SENSi = 0;
  }
  OFF -= OFFi;
  SENS -= SENSi;
  int64_t P = (((int64_t)msD1 * SENS) / 2097152 - OFF) / 8192; // 0.1 mbar units
  cPressPsi = (P / 10.0f) * 0.0145037738f;
  cWaterC = (TEMP - Ti) / 100.0f;
}

static void msTick(uint32_t now) {
  if (benchMode || !msPromOk || (int32_t)(now - msNextMs) < 0) return;
  uint8_t b[3];
  bool ok = true;
  if (msStage == 0) {
    ok = i2cWriteByte(ADDR_MS5837, 0x4A); // convert D1, OSR 8192 (17.2 ms)
    msStage = 1;
    msNextMs = now + 20;
  } else if (msStage == 1) {
    ok = i2cReadBlock(ADDR_MS5837, 0x00, b, 3);
    if (ok) {
      msD1 = ((uint32_t)b[0] << 16) | ((uint32_t)b[1] << 8) | b[2];
      ok = i2cWriteByte(ADDR_MS5837, 0x5A); // convert D2
    }
    msStage = 2;
    msNextMs = now + 20;
  } else {
    ok = i2cReadBlock(ADDR_MS5837, 0x00, b, 3);
    if (ok) {
      msD2 = ((uint32_t)b[0] << 16) | ((uint32_t)b[1] << 8) | b[2];
      msCompensate();
      hMs.ok(now); // only the COLLECT stage counts as the device answering
    }
    msStage = 0;
    msNextMs = now + 60; // full cycle ≈ 10 Hz
  }
  if (!ok) {
    hMs.failed();
    msStage = 0;
    msNextMs = now + 1000; // back off; the streak is short so the backoff
                           // cannot decide how long a dead sensor shows depth
  }
}

// ---------------------------------------------------------------------------
// INA219 ×2 — raw-register, current from the SHUNT VOLTAGE over the known
// resistance (never the chip's current register: it forgets its calibration
// word on a brown-out and then reports 0 A forever).
// ---------------------------------------------------------------------------
bool inaPackUp = false, inaRailUp = false;
uint32_t inaNextRetryMs = 0, inaNextMs = 0;
float cPackV = NAN, cPackA = NAN, cRailV = NAN, cRailA = NAN;

static bool inaConfig(uint8_t addr) {
  Wire.beginTransmission(addr);
  Wire.write((uint8_t)0x00);
  Wire.write((uint8_t)0x39);
  Wire.write((uint8_t)0x9F); // 32 V range, 320 mV gain, 12-bit continuous
  return Wire.endTransmission() == 0;
}

static bool inaRead(uint8_t addr, float &volts, float &amps) {
  uint8_t b[2];
  if (!i2cReadBlock(addr, 0x02, b, 2)) return false;
  uint16_t rawBus = ((uint16_t)b[0] << 8) | b[1];
  if (!i2cReadBlock(addr, 0x01, b, 2)) return false;
  int16_t rawShunt = (int16_t)(((uint16_t)b[0] << 8) | b[1]);
  volts = (rawBus >> 3) * 0.004f;
  amps = (rawShunt * 1e-5f) / INA219_SHUNT_OHMS;
  return true;
}

static void inaTick(uint32_t now) {
  if (benchMode) return;
  if ((int32_t)(now - inaNextRetryMs) >= 0) {
    inaNextRetryMs = now + 2000;
    if (!inaPackUp) inaPackUp = inaConfig(ADDR_INA219_PACK);
    if (!inaRailUp) inaRailUp = inaConfig(ADDR_INA219_RAIL);
  }
  if ((int32_t)(now - inaNextMs) < 0) return;
  inaNextMs = now + 500; // 2 Hz — a battery band does not need to be fast
  float v, a;
  if (inaPackUp && inaRead(ADDR_INA219_PACK, v, a)) {
    cPackV = v;
    cPackA = a;
    hInaPack.ok(now);
  } else if (inaPackUp) {
    hInaPack.failed();
  }
  if (inaRailUp && inaRead(ADDR_INA219_RAIL, v, a)) {
    cRailV = v;
    cRailA = a;
    hInaRail.ok(now);
  } else if (inaRailUp) {
    hInaRail.failed();
  }
}

// ---------------------------------------------------------------------------
// NTC pack temperature — 3V3 — NTC — (pin) — 10k — GND, Beta equation.
// ---------------------------------------------------------------------------
float cNtcC = NAN;
static void ntcTick() {
  if (benchMode) return;
  uint32_t mv = analogReadMilliVolts(PIN_NTC);
  if (mv < 30 || mv > 3270) { // pinned at a rail: open or shorted divider, not a reading
    cNtcC = NAN;
    return;
  }
  float rNtc = NTC_R_FIXED * (3300.0f / mv - 1.0f);
  float invT = 1.0f / 298.15f + logf(rNtc / NTC_R0) / NTC_BETA;
  cNtcC = 1.0f / invT - 273.15f;
}

// ---------------------------------------------------------------------------
// Leak zones — three probes, three debouncers, latching one-way, wet-at-boot
// captured. Ported rule-for-rule; the ladder itself (WARN/FLOOD/UNKNOWN) is
// mapped Pi-side from these raw facts, so the wire carries facts not verdicts.
// The 2-of-3 REFLEX lives here, because it has to work with the Pi hung.
// ---------------------------------------------------------------------------
struct LeakZone {
  uint8_t pin;
  const char *name;
  uint8_t wetRun = 0;
  bool latched = false;
  bool raw = false;
  bool bootWet = false;
};
LeakZone leakZones[3] = {{PIN_LEAK_FWD, "fwd"}, {PIN_LEAK_MID, "mid"}, {PIN_LEAK_AFT, "aft"}};
uint32_t leakNextMs = 0;
bool reflexSurface = false;

static bool leakSamplingOk(uint32_t now) { return !hLeak.faulted(now); }

static int leakLatchedCount() {
  int n = 0;
  for (auto &z : leakZones)
    if (z.latched) n++;
  return n;
}

void emitEvt(const char *name, const char *fmtExtra = nullptr, ...); // fwd decl

static void leakTick(uint32_t now) {
  if ((int32_t)(now - leakNextMs) < 0) return;
  leakNextMs = now + (1000 / LEAK_HZ);
  if (benchMode) {
    // Bench touches NOTHING real — not even the health records. A bench that
    // stamped hLeak.ok() would leave `ever=true` behind it, and a probe never
    // fitted would then read "was here and stopped" after one bench session:
    // the simulation leaking into reality, which is the one thing an announced
    // mock must never do. Bench-mode leak state is computed from the kill set
    // at frame-build time instead.
    return;
  }
  for (auto &z : leakZones) {
    z.raw = (digitalRead(z.pin) == LOW); // external pull-up; water pulls LOW
    bool was = z.latched;
    if (z.raw) {
      if (z.wetRun < 250) z.wetRun++;
      if (z.wetRun >= LEAK_DEBOUNCE_N) z.latched = true;
    } else {
      z.wetRun = 0;
    }
    if (z.latched && !was) emitEvt("leak_latch", "\"zone\":\"%s\"", z.name);
  }
  hLeak.ok(now); // reached = both sampled; the health is attached to the work
  // THE REFLEX: two zones agreeing is water, not a flaky probe. Beacon on and
  // bag emptied — both actions point at the surface, and neither needs the Pi.
  if (!reflexSurface && leakLatchedCount() >= 2) {
    reflexSurface = true;
    emitEvt("reflex_surface", "\"zones\":%d", leakLatchedCount());
  }
}

// ---------------------------------------------------------------------------
// Pump / ballast — the loop that closes HERE so the Pi never sees a pulse.
// ballast_ml is null until a purge-home has zeroed it (the same unknown-until-
// homed honesty the syringe had; the mechanism changed, the rule did not).
// ---------------------------------------------------------------------------
int pumpDir = 0;             // -1 empty · 0 stop · +1 fill (continuous command)
bool pumpMetered = false;    // a pump_ml run is in progress
float pumpTargetMl = 0.0f;   // |target| for the metered run
uint32_t pumpStartPulses = 0;
bool homing = false;
bool ballastHomed = false;
bool ballastFault = false; // pump ran, flow stayed silent: tube, clog, or dry
float ballastMl = 0.0f;    // meaningful only when ballastHomed
uint32_t pumpLastFlowMs = 0, pumpOnSinceMs = 0;
uint32_t lastFlowPulseSeen = 0;

static bool pumpCanEmpty() { return PIN_PUMP_IN2 >= 0; }

static void pumpDrive(int dir) {
  uint32_t d1 = 0, d2 = 0;
  if (dir > 0) d1 = LEDC_MAX;
  if (dir < 0) d2 = LEDC_MAX;
  pwmWrite(PIN_PUMP_IN1, CH_PUMP1, d1);
  if (PIN_PUMP_IN2 >= 0) pwmWrite(PIN_PUMP_IN2, CH_PUMP2, d2);
  if (dir != 0 && pumpOnSinceMs == 0) {
    uint32_t now = millis();
    pumpOnSinceMs = now;
    pumpLastFlowMs = now;
  }
  if (dir == 0) pumpOnSinceMs = 0;
}

static void pumpStop(const char *why) {
  bool was = (pumpDir != 0) || homing || pumpMetered;
  pumpDir = 0;
  pumpMetered = false;
  homing = false;
  pumpDrive(0);
  if (was && why) emitEvt("pump_stop", "\"why\":\"%s\"", why);
}

static void pumpTick(uint32_t now) {
  // Millilitres accumulate from the ballast flow counter whichever way the
  // water moves; direction is the commanded sign because the YF-TM02 is
  // one-directional by construction.
  static uint32_t lastPulses = 0;
  uint32_t pulses = flowBallastPulses;
  uint32_t fresh = pulses - lastPulses;
  lastPulses = pulses;
  if (fresh > 0) {
    pumpLastFlowMs = now;
    float ml = fresh * PUMP_ML_PER_PULSE;
    if (benchMode) ml = fresh * PUMP_ML_PER_PULSE; // same maths; pulses are simulated
    int dir = homing ? -1 : pumpDir;
    ballastMl += dir * ml;
    if (ballastMl < 0) ballastMl = 0;
    if (ballastMl > BALLAST_CAPACITY_ML) ballastMl = BALLAST_CAPACITY_ML;
  }
  if (pumpDir == 0 && !homing) return;
  uint32_t silent = now - pumpLastFlowMs;
  if (homing) {
    // Purge-home: pumping OUT against the empty bag. Flow going silent IS the
    // datum — the bag has nothing left to give — so silence here is success.
    if (silent > PUMP_HOME_SILENT_MS) {
      ballastMl = 0.0f;
      ballastHomed = true;
      ballastFault = false;
      pumpStop(nullptr);
      emitEvt("homed", "\"ballast_ml\":0");
    }
    return;
  }
  // A metered run ends at its target...
  if (pumpMetered) {
    float moved = (pulses - pumpStartPulses) * PUMP_ML_PER_PULSE;
    if (moved >= pumpTargetMl) {
      emitEvt("pump_done", "\"ml\":%.1f", moved);
      pumpStop(nullptr);
      return;
    }
  }
  // ...and ANY commanded run with silent flow is the mechanism's skipped-step:
  // a worn tube, a clogged sensor, a dry inlet. Stop and say so — a quietly
  // wrong ballast figure is how a sub gets left on the bottom.
  if (silent > PUMP_FAULT_SILENT_MS) {
    ballastFault = true;
    emitEvt("pump_fault", "\"silent_ms\":%u", (unsigned)silent);
    pumpStop("no-flow");
  }
}

// ---------------------------------------------------------------------------
// Lamp, beacon, burn interlock
// ---------------------------------------------------------------------------
float lampLevel = 0.0f;
bool beaconOn = false;
bool burnArmed = false, burnFired = false, burnFiring = false;
uint32_t burnArmedAtMs = 0, burnFireAtMs = 0;

static void lampSet(float level) {
  lampLevel = constrain(level, 0.0f, 1.0f);
  pwmWrite(PIN_LAMP, CH_LAMP, (uint32_t)(lampLevel * LEDC_MAX));
}

static void beaconTick(uint32_t now) {
  // 0.2 s on / 1.8 s off — short flash, long gap, unmistakable and cheap. The
  // reflex forces it on; the commanded state resumes when the reflex clears.
  bool want = beaconOn || reflexSurface;
  digitalWrite(PIN_BEACON, (want && (now % 2000) < 200) ? HIGH : LOW);
}

static void burnTick(uint32_t now) {
  if (burnArmed && !burnFiring && (uint32_t)(now - burnArmedAtMs) > BURN_ARM_TIMEOUT_MS) {
    burnArmed = false;
    digitalWrite(PIN_BURN_ARM, LOW);
    emitEvt("burn_disarmed", "\"why\":\"timeout\"");
  }
  if (burnFiring && (uint32_t)(now - burnFireAtMs) > BURN_FIRE_MS) {
    digitalWrite(PIN_BURN_FIRE, LOW);
    digitalWrite(PIN_BURN_ARM, LOW);
    burnFiring = false;
    burnArmed = false;
    burnFired = true;
    emitEvt("burn_fired", "\"pulse_ms\":%d", BURN_FIRE_MS);
  }
}

// ---------------------------------------------------------------------------
// Ring buffer — the third blackbox witness. Events only (telemetry is
// reconstructible; the discrete things are not), RAM-resident: it survives a
// hung Pi, which is its job, and honestly does not survive a power cut.
// ---------------------------------------------------------------------------
#define RING_N 48
#define RING_LINE 160
char ringBuf[RING_N][RING_LINE];
uint16_t ringHead = 0, ringCount = 0;

static void ringPush(const char *line) {
  strncpy(ringBuf[ringHead], line, RING_LINE - 1);
  ringBuf[ringHead][RING_LINE - 1] = 0;
  ringHead = (ringHead + 1) % RING_N;
  if (ringCount < RING_N) ringCount++;
}

// ---------------------------------------------------------------------------
// JSON out — hand-assembled with snprintf into one buffer. No library: the
// shapes are flat and known, and a fixed buffer cannot fragment the heap.
// ---------------------------------------------------------------------------
char out[1152];
int outLen = 0;

static void outReset() {
  outLen = 0;
  out[0] = 0;
}
static void outAdd(const char *fmt, ...) {
  if (outLen >= (int)sizeof(out) - 8) return;
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(out + outLen, sizeof(out) - outLen, fmt, ap);
  va_end(ap);
  if (n > 0) outLen = min(outLen + n, (int)sizeof(out) - 1);
}
// A float field that can be cannot-tell: NAN ships as null, never as a number.
static void outF(const char *key, float v, int dp, bool comma = true) {
  if (isnan(v)) outAdd("\"%s\":null%s", key, comma ? "," : "");
  else outAdd("\"%s\":%.*f%s", key, dp, v, comma ? "," : "");
}

void emitEvt(const char *name, const char *fmtExtra, ...) {
  char line[RING_LINE];
  int n = snprintf(line, sizeof(line), "{\"t\":\"evt\",\"ms\":%u,\"name\":\"%s\"", (unsigned)millis(), name);
  if (fmtExtra != nullptr) {
    n += snprintf(line + n, sizeof(line) - n, ",");
    va_list ap;
    va_start(ap, fmtExtra);
    n += vsnprintf(line + n, sizeof(line) - n, fmtExtra, ap);
    va_end(ap);
  }
  snprintf(line + min(n, (int)sizeof(line) - 2), 3, "}");
  Serial.println(line);
  ringPush(line);
}

// ---------------------------------------------------------------------------
// Bench simulation — advances only in bench mode. Shapes are coherent (depth
// follows the bag, the pack sags, flow pulses while the pump runs) so the whole
// console pipeline exercises the same relationships the water will produce.
// ---------------------------------------------------------------------------
uint32_t simNextMs = 0;
static void benchTick(uint32_t now) {
  if (!benchMode || (int32_t)(now - simNextMs) < 0) return;
  simNextMs = now + 100;
  simHeading = fmodf(simHeading + 0.4f, 360.0f);
  simPackV = max(9.0f, simPackV - 0.00005f);
  float depthM = ballastHomed ? (ballastMl / BALLAST_CAPACITY_ML) * 3.0f : 0.4f;
  simDepthPsi = 14.7f + depthM * 1.42f;
  if (pumpDir != 0 || homing) {
    // ~2 ml per 100 ms at 0.2 ml/pulse = one simulated pulse per tick — but a
    // homing run against an already-empty bag goes silent, which is the datum.
    bool dry = homing && ballastMl <= 0.01f;
    if (!dry) flowBallastPulses += 1;
  }
  // DELIBERATELY no health stamps here: the real DeviceHealth records belong
  // to real chips only (see leakTick's bench note). Bench liveness is the kill
  // set, consulted at frame-build time.
}

// ---------------------------------------------------------------------------
// Telemetry frame
// ---------------------------------------------------------------------------
uint32_t tlmSeq = 0, tlmNextMs = 0;
bool undervolt = false;
uint32_t undervoltSinceMs = 0;

static void undervoltTick(uint32_t now, float packV) {
  if (isnan(packV)) return; // cannot-tell is not evidence in either direction
  if (packV < PACK_UNDERVOLT_V) {
    if (undervoltSinceMs == 0) undervoltSinceMs = now;
    if (!undervolt && (uint32_t)(now - undervoltSinceMs) > UNDERVOLT_HOLD_MS) {
      undervolt = true;
      emitEvt("undervolt", "\"pack_v\":%.2f", packV);
    }
  } else if (packV > PACK_RECOVER_V) {
    undervoltSinceMs = 0;
    undervolt = false;
  }
}

static bool benchKilled(DeviceHealth *h); // defined below addFaultList

static void addFaultList(const char *key, bool absentOnly, uint32_t now) {
  outAdd("\"%s\":[", key);
  bool first = true;
  for (int i = 0; i < N_HEALTH; i++) {
    DeviceHealth *h = ALL_HEALTH[i];
    bool bad = benchMode ? benchKilled(h) : h->faulted(now);
    bool absent = benchMode ? false : !h->ever;
    if (bad && (!absentOnly || absent)) {
      outAdd("%s\"%s\"", first ? "" : ",", h->name);
      first = false;
    }
  }
  outAdd("]");
}

static bool benchKilled(DeviceHealth *h) {
  if (h == &hBno) return killBno;
  if (h == &hMs) return killMs;
  if (h == &hInaPack) return killInaPack;
  if (h == &hInaRail) return killInaRail;
  if (h == &hLeak) return killLeak;
  return false;
}

static void sendTelemetry(uint32_t now) {
  if ((int32_t)(now - tlmNextMs) < 0) return;
  tlmNextMs = now + (1000 / TLM_HZ);
  tlmSeq++;

  // Gather, with per-chip gating: a faulted chip's readings are null in the
  // same frame that names it — one decision, read twice, same as the Pi.
  bool bnoOk = benchMode ? !killBno : !hBno.faulted(now);
  bool msOk = benchMode ? !killMs : !hMs.faulted(now);
  bool packOk = benchMode ? !killInaPack : !hInaPack.faulted(now);
  bool railOk = benchMode ? !killInaRail : !hInaRail.faulted(now);
  float heading = bnoOk ? (benchMode ? simHeading : cHeading) : NAN;
  float gyroZ = bnoOk ? (benchMode ? 0.0f : cGyroZ) : NAN;
  float accel = bnoOk ? (benchMode ? 0.0f : cAccelFwd) : NAN;
  float pitch = bnoOk ? (benchMode ? 0.0f : cPitch) : NAN;
  float roll = bnoOk ? (benchMode ? 0.0f : cRoll) : NAN;
  int magCal = bnoOk ? (benchMode ? 3 : cMagCal) : -1;
  float press = msOk ? (benchMode ? simDepthPsi : cPressPsi) : NAN;
  float waterC = msOk ? (benchMode ? 12.5f : cWaterC) : NAN;
  float packV = packOk ? (benchMode ? simPackV : cPackV) : NAN;
  float packA = packOk ? (benchMode ? 0.42f : cPackA) : NAN;
  float railV = railOk ? (benchMode ? 8.0f : cRailV) : NAN;
  float railA = railOk ? (benchMode ? 0.0f : cRailA) : NAN;
  float ntcC = benchMode ? 21.5f : cNtcC;
  undervoltTick(now, packV);

  // Speed: period math off the last interval; stale after 2 s of no pulses.
  uint32_t interval = speedIntervalUs;
  uint32_t last = speedLastUs;
  bool speedFresh = last != 0 && (uint32_t)(micros() - last) < SPEED_STALE_US && interval > 0;
  float speedHz = speedFresh ? (1000000.0f / interval) : 0.0f;
  int speedDir = 0;
  if ((uint32_t)(micros() - pasLastUs) < SPEED_STALE_US && pasLastUs != 0) {
    // Direction from the PAS ring: the sign of recent tick movement.
    static int32_t lastPas = 0;
    int32_t ticksNow = pasTicks;
    if (ticksNow > lastPas) speedDir = 1;
    else if (ticksNow < lastPas) speedDir = -1;
    lastPas = ticksNow;
  }

  outReset();
  outAdd("{\"t\":\"tlm\",\"seq\":%u,\"ms\":%u,\"mode\":\"%s\",", (unsigned)tlmSeq, (unsigned)now,
         benchMode ? "bench" : "real");
  outF("heading", heading, 1);
  if (magCal < 0) outAdd("\"mag_cal\":null,");
  else outAdd("\"mag_cal\":%d,", magCal);
  outF("gyro_z", gyroZ, 2);
  outF("accel_fwd", accel, 2);
  outF("pitch", pitch, 1);
  outF("roll", roll, 1);
  outF("press_psi", press, 2);
  outF("water_c", waterC, 1);
  outF("pack_v", packV, 2);
  outF("pack_a", packA, 2);
  outF("rail_v", railV, 2);
  outF("rail_a", railA, 2);
  outF("ntc_c", ntcC, 1);
  bool leakOk = benchMode ? !killLeak : leakSamplingOk(now);
  outAdd("\"leak_ok\":%s,", leakOk ? "true" : "false");
  if (benchMode) {
    // Bench simulates a DRY, healthy hull — the real pins are not consulted.
    // On a bare devkit with no pull-ups fitted yet, the real zones float wet;
    // showing that in bench mode would put a FLOOD on the console during the
    // exact bare-board walk bench mode exists for. Real mode shows the truth
    // about the pins, wet and all; bench shows the simulation, announced.
    outAdd("\"leak_raw\":[false,false,false],");
    outAdd("\"leak_latch\":[false,false,false],");
    outAdd("\"leak_boot\":[false,false,false],");
  } else {
    outAdd("\"leak_raw\":[%s,%s,%s],", leakZones[0].raw ? "true" : "false", leakZones[1].raw ? "true" : "false",
           leakZones[2].raw ? "true" : "false");
    outAdd("\"leak_latch\":[%s,%s,%s],", leakZones[0].latched ? "true" : "false",
           leakZones[1].latched ? "true" : "false", leakZones[2].latched ? "true" : "false");
    outAdd("\"leak_boot\":[%s,%s,%s],", leakZones[0].bootWet ? "true" : "false",
           leakZones[1].bootWet ? "true" : "false", leakZones[2].bootWet ? "true" : "false");
  }
  if (ballastHomed) outAdd("\"ballast_ml\":%.1f,", ballastMl);
  else outAdd("\"ballast_ml\":null,");
  outAdd("\"ballast_homed\":%s,", ballastHomed ? "true" : "false");
  outAdd("\"ballast_fault\":%s,", ballastFault ? "true" : "false");
  outAdd("\"pump\":%d,", homing ? -1 : pumpDir);
  outAdd("\"flow_ml\":%.1f,", flowBallastPulses * PUMP_ML_PER_PULSE);
  outF("speed_hz", speedHz, 2);
  outAdd("\"speed_fresh\":%s,", speedFresh ? "true" : "false");
  outAdd("\"speed_dir\":%d,", speedDir);
  outAdd("\"lamp\":%.2f,", lampLevel);
  outAdd("\"beacon\":%s,", (beaconOn || reflexSurface) ? "true" : "false");
  outAdd("\"burn_armed\":%s,", burnArmed ? "true" : "false");
  outAdd("\"burn_fired\":%s,", burnFired ? "true" : "false");
#if HAS_BURN_SENSE
  outF("burn_cont_mv", (float)analogReadMilliVolts(PIN_BURN_SENSE), 0);
#endif
  outAdd("\"reflex_surface\":%s,", reflexSurface ? "true" : "false");
  outAdd("\"undervolt\":%s,", undervolt ? "true" : "false");
  addFaultList("faults", false, now);
  outAdd(",");
  addFaultList("absent", true, now);
  outAdd("}");
  Serial.println(out);
}

// ---------------------------------------------------------------------------
// Commands — {"t":"cmd","id":N,"name":"...","value":...}. The parser is a
// strict, bounded key-scanner rather than a JSON library: the shape is one flat
// object the Pi builds, and a scanner cannot be blown up by a deep document.
// Every command is acked; unknown ones ok=false — version skew degrades.
// ---------------------------------------------------------------------------
char lineBuf[512];
int lineLen = 0;

static bool jsonFindInt(const char *s, const char *key, long *out) {
  char pat[24];
  snprintf(pat, sizeof(pat), "\"%s\":", key);
  const char *p = strstr(s, pat);
  if (!p) return false;
  *out = strtol(p + strlen(pat), nullptr, 10);
  return true;
}
static bool jsonFindFloat(const char *s, const char *key, float *out) {
  char pat[24];
  snprintf(pat, sizeof(pat), "\"%s\":", key);
  const char *p = strstr(s, pat);
  if (!p) return false;
  *out = strtof(p + strlen(pat), nullptr);
  return true;
}
static bool jsonFindStr(const char *s, const char *key, char *out, int outN) {
  char pat[24];
  snprintf(pat, sizeof(pat), "\"%s\":\"", key);
  const char *p = strstr(s, pat);
  if (!p) return false;
  p += strlen(pat);
  int i = 0;
  while (*p && *p != '"' && i < outN - 1) out[i++] = *p++;
  out[i] = 0;
  return *p == '"';
}

static void ack(long id, bool ok, const char *errOrNull, const char *resultFmt = nullptr, ...) {
  outReset();
  outAdd("{\"t\":\"ack\",\"id\":%ld,\"ok\":%s", id, ok ? "true" : "false");
  if (errOrNull) outAdd(",\"err\":\"%s\"", errOrNull);
  if (resultFmt) {
    outAdd(",\"result\":");
    char tmp[128];
    va_list ap;
    va_start(ap, resultFmt);
    vsnprintf(tmp, sizeof(tmp), resultFmt, ap);
    va_end(ap);
    outAdd("%s", tmp);
  }
  outAdd("}");
  Serial.println(out);
}

static bool killByName(const char *chip, bool dead) {
  if (!strcmp(chip, "bno085")) killBno = dead;
  else if (!strcmp(chip, "ms5837")) killMs = dead;
  else if (!strcmp(chip, "ina219")) killInaPack = dead;
  else if (!strcmp(chip, "ina219-rail")) killInaRail = dead;
  else if (!strcmp(chip, "leak-probes")) killLeak = dead;
  else return false;
  return true;
}

static void handleCommand(const char *line) {
  long id = -1;
  jsonFindInt(line, "id", &id);
  char name[24] = {0};
  if (!jsonFindStr(line, "name", name, sizeof(name))) {
    ack(id, false, "no name");
    return;
  }
  float fval = 0.0f;
  bool hasF = jsonFindFloat(line, "value", &fval);
  char sval[24] = {0};
  bool hasS = jsonFindStr(line, "value", sval, sizeof(sval));

  if (!strcmp(name, "ping")) {
    ack(id, true, nullptr, "{\"ms\":%u,\"seq\":%u}", (unsigned)millis(), (unsigned)tlmSeq);
  } else if (!strcmp(name, "info")) {
    ack(id, true, nullptr, "{\"fw\":\"%s %s\",\"proto\":%d,\"mode\":\"%s\",\"pump2\":%s}", FW_NAME, FW_VERSION, PROTO,
        benchMode ? "bench" : "real", pumpCanEmpty() ? "true" : "false");
  } else if (!strcmp(name, "lamp")) {
    lampSet(hasF ? fval : 0.0f);
    ack(id, true, nullptr, "%.2f", lampLevel);
  } else if (!strcmp(name, "beacon")) {
    beaconOn = hasF && fval >= 0.5f;
    ack(id, true, nullptr, "%s", beaconOn ? "true" : "false");
  } else if (!strcmp(name, "pump")) {
    int dir = hasF ? (fval > 0.5f ? 1 : (fval < -0.5f ? -1 : 0)) : 0;
    if (dir < 0 && !pumpCanEmpty()) {
      ack(id, false, "single-MOSFET build cannot pump out");
      return;
    }
    homing = false;
    pumpMetered = false;
    pumpDir = dir;
    if (dir == 0) pumpStop(nullptr);
    else pumpDrive(dir);
    ack(id, true, nullptr, "%d", dir);
  } else if (!strcmp(name, "pump_ml")) {
    if (!hasF || fval == 0.0f) {
      ack(id, false, "value must be signed ml");
      return;
    }
    int dir = fval > 0 ? 1 : -1;
    if (dir < 0 && !pumpCanEmpty()) {
      ack(id, false, "single-MOSFET build cannot pump out");
      return;
    }
    homing = false;
    pumpMetered = true;
    pumpTargetMl = fabsf(fval);
    pumpStartPulses = flowBallastPulses;
    pumpDir = dir;
    pumpDrive(dir);
    ack(id, true, nullptr, "\"started\"");
  } else if (!strcmp(name, "trim_home")) {
    if (!pumpCanEmpty()) {
      ack(id, false, "single-MOSFET build cannot purge-home");
      return;
    }
    pumpMetered = false;
    pumpDir = 0;
    homing = true;
    pumpDrive(-1);
    ack(id, true, nullptr, "\"homing\"");
  } else if (!strcmp(name, "arm_burn")) {
    bool want = hasF && fval >= 0.5f;
    burnArmed = want && !burnFiring;
    burnArmedAtMs = millis();
    digitalWrite(PIN_BURN_ARM, burnArmed ? HIGH : LOW);
    emitEvt(burnArmed ? "burn_armed" : "burn_disarmed", nullptr);
    ack(id, true, nullptr, "%s", burnArmed ? "true" : "false");
  } else if (!strcmp(name, "fire_burn")) {
    // THE INTERLOCK: fire is refused unless the ARM half already agrees. Two
    // commands, two pins, and this check — no single glitch fires a release.
    if (!burnArmed) {
      ack(id, false, "not armed");
      return;
    }
    burnFiring = true;
    burnFireAtMs = millis();
    digitalWrite(PIN_BURN_FIRE, HIGH);
    ack(id, true, nullptr, "\"firing\"");
  } else if (!strcmp(name, "leak_reset")) {
    // Clears the MEMORY of water, never water that is there now — same refusal
    // the Pi's backend enforced, moved to where the pins live.
    for (auto &z : leakZones) {
      if (!benchMode && digitalRead(z.pin) == LOW) {
        ack(id, false, z.name); // the err names the wet zone
        return;
      }
    }
    for (auto &z : leakZones) {
      z.latched = false;
      z.wetRun = 0;
      z.bootWet = false;
    }
    reflexSurface = false;
    emitEvt("leak_reset", nullptr);
    ack(id, true, nullptr, "\"cleared\"");
  } else if (!strcmp(name, "mock")) {
    bool want = hasF && fval >= 0.5f;
    if (want != benchMode) {
      benchMode = want;
      if (benchMode) {
        // Entering bench: every simulated chip "answers" until killed. The
        // REAL health records are untouched in both directions — bench never
        // stamps them (see benchTick), so leaving bench simply resumes the
        // real verdicts exactly where reality left them: a chip that was
        // answering picks up within a tick, one that never existed is still
        // absent, and nothing the bench did can certify either.
        killBno = killMs = killInaPack = killInaRail = killLeak = false;
      }
      emitEvt("mode", "\"mode\":\"%s\"", benchMode ? "bench" : "real");
    }
    ack(id, true, nullptr, "\"%s\"", benchMode ? "bench" : "real");
  } else if (!strcmp(name, "kill") || !strcmp(name, "revive")) {
    if (!benchMode) {
      ack(id, false, "bench mode only");
      return;
    }
    if (!hasS || !killByName(sval, name[0] == 'k')) {
      ack(id, false, "unknown chip");
      return;
    }
    ack(id, true, nullptr, "\"%s\"", sval);
  } else if (!strcmp(name, "ring")) {
    for (uint16_t i = 0; i < ringCount; i++) {
      uint16_t idx = (ringHead + RING_N - ringCount + i) % RING_N;
      Serial.println(ringBuf[idx]);
    }
    ack(id, true, nullptr, "%u", (unsigned)ringCount);
  } else {
    ack(id, false, "unknown command");
  }
}

static void serialTick() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen > 0) {
        lineBuf[lineLen] = 0;
        handleCommand(lineBuf);
        lineLen = 0;
      }
    } else if (lineLen < (int)sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    } else {
      lineLen = 0; // a line that long is garbage; drop it whole
    }
  }
}

// ---------------------------------------------------------------------------
// setup / loop
// ---------------------------------------------------------------------------
static const char *resetReasonName() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "poweron";
    case ESP_RST_SW: return "sw";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_WDT: case ESP_RST_INT_WDT: case ESP_RST_TASK_WDT: return "wdt";
    case ESP_RST_BROWNOUT: return "brownout";
    default: return "other";
  }
}

void setup() {
  Serial.begin(BAUD);
  // Outputs first, and SAFE first — a floating gate with the rails up is a
  // pump that runs and a beacon that lies the moment power arrives.
  pinMode(PIN_BEACON, OUTPUT);
  digitalWrite(PIN_BEACON, LOW);
  pinMode(PIN_BURN_ARM, OUTPUT);
  digitalWrite(PIN_BURN_ARM, LOW);
  pinMode(PIN_BURN_FIRE, OUTPUT);
  digitalWrite(PIN_BURN_FIRE, LOW);
  pwmAttach(PIN_PUMP_IN1, 5000, CH_PUMP1);
  if (PIN_PUMP_IN2 >= 0) pwmAttach(PIN_PUMP_IN2, 5000, CH_PUMP2);
  pumpDrive(0);
  pwmAttach(PIN_LAMP, 8000, CH_LAMP); // 8 kHz: above anything the camera's shutter aliases
  lampSet(0.0f);

  // Inputs. 34/35/39 have no internal pulls — the breadboard MUST carry
  // external 100k pull-ups or every zone reads permanently wet.
  pinMode(PIN_LEAK_FWD, INPUT);
  pinMode(PIN_LEAK_MID, INPUT);
  pinMode(PIN_LEAK_AFT, INPUT);
  pinMode(PIN_FLOW_BALLAST, INPUT_PULLUP);
  pinMode(PIN_FLOW_SPEED, INPUT_PULLUP);
  pinMode(PIN_PAS_A, INPUT_PULLUP);
  pinMode(PIN_PAS_B, INPUT_PULLUP);
  analogSetPinAttenuation(PIN_NTC, ADC_11db);
#if HAS_BURN_SENSE
  analogSetPinAttenuation(PIN_BURN_SENSE, ADC_11db);
#endif
  attachInterrupt(digitalPinToInterrupt(PIN_FLOW_BALLAST), isrFlowBallast, FALLING);
  attachInterrupt(digitalPinToInterrupt(PIN_FLOW_SPEED), isrFlowSpeed, FALLING);
  pasPrev = (digitalRead(PIN_PAS_A) << 1) | digitalRead(PIN_PAS_B);
  attachInterrupt(digitalPinToInterrupt(PIN_PAS_A), isrPas, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_PAS_B), isrPas, CHANGE);

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  Wire.setClock(400000);
  Wire.setTimeOut(50); // a wedged bus costs 50 ms, never a hung loop

  // Wet-at-boot: a hull sealed dry then powered up should read three dry
  // zones; a zone already wet is a shorted probe or a flooded hull, and a
  // second from now it is indistinguishable from a leak that just started.
  for (auto &z : leakZones) {
    z.bootWet = (digitalRead(z.pin) == LOW);
    z.raw = z.bootWet;
  }

  outReset();
  outAdd("{\"t\":\"hello\",\"fw\":\"%s %s\",\"proto\":%d,\"mode\":\"real\",\"reset\":\"%s\",\"pump2\":%s,"
         "\"leak_boot\":[%s,%s,%s]}",
         FW_NAME, FW_VERSION, PROTO, resetReasonName(), pumpCanEmpty() ? "true" : "false",
         leakZones[0].bootWet ? "true" : "false", leakZones[1].bootWet ? "true" : "false",
         leakZones[2].bootWet ? "true" : "false");
  Serial.println(out);
  ringPush(out);
}

void loop() {
  uint32_t now = millis();
  bnoTryBegin(now);
  msTryBegin(now);
  bnoTick(now);
  msTick(now);
  inaTick(now);
  ntcTick();
  leakTick(now);
  // The reflex's second act: with two zones latched, empty the bag. Issued
  // once — pumpStop / new commands are not fought, but the first response to
  // corroborated water is buoyancy, Pi or no Pi.
  static bool reflexPumped = false;
  if (reflexSurface && !reflexPumped && pumpCanEmpty()) {
    reflexPumped = true;
    pumpMetered = false;
    homing = false;
    pumpDir = -1;
    pumpDrive(-1);
    emitEvt("reflex_pump_empty", nullptr);
  }
  if (!reflexSurface) reflexPumped = false;
  pumpTick(now);
  beaconTick(now);
  burnTick(now);
  benchTick(now);
  sendTelemetry(now);
  serialTick();
}
