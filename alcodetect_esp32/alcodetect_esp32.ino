  /*
 * AlcoDetect — ESP32-WROOM-32 Firmware
 * =====================================
 * Board:    ESP32-WROOM-32 (use "ESP32 Dev Module" in Arduino IDE)
 * Sensors:  MQ3 x3 (analog ADC), DHT11 (digital)
 * Trigger:  HTTP POST /trigger from backend (Option B — middleman)
 * Posts to: POST http://{BACKEND_IP}:8000/training/collect/sensor-data
 *
 * WIRING — Plan A: nose + jaw (mouth+neck) + upperChest clavicle
 * ┌─────────────────────────────────────────────────────┐
 * │  MQ3 Sensor 1 — NOSE (GPIO34, ~0-3cm from source)  │
 * │    VCC  → 3.3V (or 5V if your module has regulator)│
 * │    GND  → GND                                       │
 * │    AOUT → GPIO 34  (ADC1_CH6, input only)          │
 * │                                                     │
 * │  MQ3 Sensor 2 — JAW / mouth+neck (GPIO35, mandible)│
 * │    VCC  → 3.3V                                      │
 * │    GND  → GND                                       │
 * │    AOUT → GPIO 35  (ADC1_CH7, input only)          │
 * │                                                     │
 * │  MQ3 Sensor 3 — UpperChest clavicle (GPIO32,       │
 * │               ~12-15cm below jaw, covers neck+chest)│
 * │    VCC  → 3.3V                                      │
 * │    GND  → GND                                       │
 * │    AOUT → GPIO 32  (ADC1_CH4)                      │
 * │                                                     │
 * │  DHT11                                              │
 * │    VCC  → 3.3V                                      │
 * │    GND  → GND                                       │
 * │    DATA → GPIO 4                                    │
 * │    (10kΩ pull-up resistor between DATA and VCC)    │
 * │                                                     │
 * │  Status LED (optional)                              │
 * │    + → GPIO 2 (built-in LED on most DevKit boards) │
 * │    - → GND (via 220Ω resistor if external LED)     │
 * │                                                     │
 * │  Solenoid gate (optional — future)                  │
 * │    Relay IN → GPIO 26                               │
 * │    Relay VCC → 5V                                   │
 * │    Relay GND → GND                                  │
 * └─────────────────────────────────────────────────────┘
 *
 * WHY THESE PINS:
 * GPIO 34, 35 are input-only pins — perfect for analog sensors
 * GPIO 32 is ADC1 — safe to use with WiFi (ADC2 conflicts with WiFi)
 * GPIO 4 is a safe general-purpose digital pin for DHT11
 * AVOID GPIO 36, 39 (input only, no pull-up/down support)
 * AVOID ADC2 pins (GPIO 0,2,4,12,13,14,15,25,26,27) when WiFi is active
 *
 * ARDUINO IDE SETUP:
 * 1. Board: "ESP32 Dev Module"
 * 2. Install libraries:
 *    - DHT sensor library by Adafruit
 *    - Adafruit Unified Sensor
 *    - ArduinoJson by Benoit Blanchon
 * 3. Upload speed: 115200
 * 4. Flash frequency: 80MHz
 */

#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <Preferences.h>
#include "relay_sequence.h"
#include "indicator.h"
#include "dfplayer.h"

// ── Shared device token (1C) ─────────────────────────────────────────────────
// The real file is gitignored. Without it the firmware builds WITHOUT a token and
// /indicator answers 503 and the indicators stay OFF — fail closed, never green.
#if __has_include("secrets.h")
  #include "secrets.h"
  #define DEVICE_TOKEN_PRESENT 1
#else
  #define DEVICE_TOKEN_PRESENT 0
  static const char* DEVICE_TOKEN = "";
#endif

static bool tokenConfigured() {
  return DEVICE_TOKEN_PRESENT && DEVICE_TOKEN != 0 && strlen(DEVICE_TOKEN) > 0;
}

// Constant-time compare: never short-circuit on the first differing byte, so the
// comparison time does not leak how much of the token was correct.
static bool tokenMatches(const String& presented) {
  if (!tokenConfigured()) return false;
  const char* want = DEVICE_TOKEN;
  size_t n = strlen(want);
  if (presented.length() != n) {
    // still walk the real length so the timing does not depend on the mismatch
    volatile char sink = 0;
    for (size_t i = 0; i < n; i++) sink ^= want[i];
    (void)sink;
    return false;
  }
  unsigned char diff = 0;
  for (size_t i = 0; i < n; i++) diff |= (unsigned char)(want[i] ^ presented[i]);
  return diff == 0;
}

// ── WiFi credentials ──────────────────────────────────────────
const char* WIFI_SSID     = "Bayot kaba?";
const char* WIFI_PASSWORD = "bayotko123";

// ── Backend URL ───────────────────────────────────────────────
// Change to your laptop/Pi IP. Must be on same WiFi network. Keep in sync with Backend/.env ESP32_URL
// Training:   http://192.168.x.x:8000/training/collect/sensor-data
// Deployment: http://192.168.x.x:8000/sensor
const char* BACKEND_IP      = "192.168.69.1";   // backend host, not the ESP32
const int   BACKEND_PORT    = 8000;
const char* TRAINING_ENDPOINT  = "/training/collect/sensor-data";
const char* DEPLOYMENT_ENDPOINT = "/sensor";

// ── Pin definitions — Plan A ────────────────────────────────
#define MQ3_PIN_1    34    // ADC1_CH6 — nose (~0-3cm)
#define MQ3_PIN_2    35    // ADC1_CH7 — jaw mandible (mouth+neck, 5-7cm below nose)
#define MQ3_PIN_3    32    // ADC1_CH4 — upperChest clavicle (12-15cm below jaw)
#define DHT_PIN       4    // DHT11 data pin
#define DHT_TYPE    DHT11 
#define LED_PIN       2    // Built-in LED — status indicator

// ── Deployment indicators (PHASE 1) — DISPLAY ONLY ────────────────────────────
// Two RGB LEDs share the SAME three PWM channels (R, G, B); each LED gets its own
// series resistor, so one write drives both. Nothing here touches a lock or a relay.
//
// GPIO 16 and 17 are deliberately NOT used: on a WROVER / PSRAM module those two
// pins are consumed by the PSRAM and driving an LED there misbehaves. Gg's board does
// not expose them. GPIO 22/23 are free on both WROOM-32 and WROVER and are not
// strapping, flash or input-only pins.
#define IND_LED_R     22    // free: not a strapping pin, not flash, not input-only
#define IND_LED_G     23    // free: not a strapping pin, not flash, not input-only
#define IND_LED_B     18    // free: not a strapping pin, not flash, not input-only
#define IND_LED_PWM_HZ   5000
#define IND_LED_BITS      8

// LED_COMMON_ANODE — *** UNCONFIRMED: Gg must set this to match his actual LEDs. ***
// false = common cathode (anode to +3.3 V, cathode driven by the pin)
// true  = common anode   (cathode to GND, anode driven by the pin — duty is inverted)
#define LED_COMMON_ANODE false

// ── DFPlayer Mini on hardware UART Serial2 ───────────────────────────────────
// ESP32 TX -> DFPlayer RX (1k series resistor), DFPlayer TX -> ESP32 RX (1k series).
#define DFPLAYER_UART_NUM   2
#define DFPLAYER_TX_PIN    19    // ESP32 TX  -> DFPlayer RX
#define DFPLAYER_RX_PIN    21    // ESP32 RX  <- DFPlayer TX
#define DFPLAYER_BAUD   9600     // UNCONFIRMED against the datasheet — verify
#define DFPLAYER_BUSY_PIN  (-1)  // optional BUSY pin; -1 = not wired, see /status
#define DFPLAYER_VOLUME    18     // 0..30
#define DFPLAYER_AUDIO_RETRY_MS 30000UL   // slow retry when init fails
#define DFPLAYER_AUDIO_INIT_MS   1500UL   // max wait for a BUSY low during init

// ── Indicator timing (display only) ──────────────────────────────────────────
// Timing lives in indicator.h so the firmware, the bench and the host tests all read
// the same constants. Aliased here only for the names already used in the .ino.
#define ANALYZE_TIMEOUT_MS  IND_ANALYZE_TIMEOUT_MS
#define RESULT_HOLD_MS      IND_RESULT_HOLD_MS
#define CUE_COOLDOWN_MS     IND_CUE_COOLDOWN_MS
#define SLOW_PULSE_MS       IND_SLOW_PULSE_MS
#define FAST_PULSE_MS       IND_FAST_PULSE_MS
#define PERSON_HOLD_MS      IND_PERSON_HOLD_MS


// ── Sampling config ───────────────────────────────────────────
#define FAN_RELAY_PIN     27   // suction fan relay IN (ADC2, but used as OUTPUT only)
#define BLOWER_RELAY_PIN  25   // one relay driving all three 5015 helper blowers
// Relay modules are active-LOW: the coil energises when the input is pulled LOW.
// Flip this single constant if the modules are wired the other way round.
const bool RELAY_ACTIVE_LOW = true;
const bool FAN_ENABLED     = true;   // per-channel enable flags
const bool BLOWER_ENABLED  = true;

// ── Relay sequence timing ────────────────────────────────────────────────────
//   PREROLL  0..3000   relays ON, no sampling yet (airflow must settle first)
//   SAMPLING 3000..8000 relays ON, the existing 5 s window (unchanged)
//   POSTROLL after the window, 2000 ms, relays still ON
//   IDLE     relays OFF
#define PREROLL_MS          3000
#define POSTROLL_MS         2000
#define MAX_RELAY_ON_MS     (PREROLL_MS + BUFFER_DURATION_MS + POSTROLL_MS + 2000)

#define SAMPLE_INTERVAL_MS   100   // 100ms between samples = 10 samples/sec
#define BUFFER_DURATION_MS  5000   // 5 second window (50 samples at 100ms)
#define MIN_SAMPLES            5   // minimum samples required by backend

// Bridge RelaySequence's injected callback to the real ESP32 pins.
void relayWritePin(uint8_t channel, uint8_t level) {
  uint8_t pin = (channel == 0) ? FAN_RELAY_PIN : BLOWER_RELAY_PIN;
  digitalWrite(pin, level);
}

RelaySequence::Config makeRelayConfig() {
  RelaySequence::Config c;
  c.prerollMs  = PREROLL_MS;
  c.bufferMs   = BUFFER_DURATION_MS;
  c.postrollMs = POSTROLL_MS;
  c.maxOnMs    = MAX_RELAY_ON_MS;
  c.fanEnabled = FAN_ENABLED;
  c.blowerEnabled = BLOWER_ENABLED;
  c.activeLow  = RELAY_ACTIVE_LOW;
  return c;
}

// Declared early so its constructor (which drives the relays OFF) runs before setup().
RelaySequence relays(relayWritePin, makeRelayConfig());

// ── Mode ──────────────────────────────────────────────────────
// true  = training mode  → posts to /training/collect/sensor-data (training_data)
// false = deployment mode → posts to /sensor                    (sensor_readings)
// The default is DEPLOYMENT: a factory-fresh or NVS-cleared device can never post a
// live gate reading into the training table. Training mode must be opted into
// deliberately (GET /mode?training=1, or the Settings UI), and the choice is kept in
// NVS so it survives both a power cut and a reflash.
bool TRAINING_MODE = false;

Preferences nvs;
static const char* NVS_NS   = "alco";
static const char* NVS_KEY  = "mode";
static const char* MODE_TRAINING    = "training";
static const char* MODE_DEPLOYMENT  = "deployment";

// Mode PINNED when /trigger was accepted. The destination is decided HERE, not when
// the window is posted: a mode change mid-sequence can never redirect an in-flight
// window into the wrong table.
bool activeTrainingMode = false;

const char* modeName() { return TRAINING_MODE ? MODE_TRAINING : MODE_DEPLOYMENT; }

void loadModeFromNvs() {
  nvs.begin(NVS_NS, true);
  bool stored = nvs.getBool(NVS_KEY, false);
  nvs.end();
  TRAINING_MODE = stored;
}

void saveModeToNvs() {
  nvs.begin(NVS_NS, false);
  nvs.putBool(NVS_KEY, TRAINING_MODE);
  nvs.end();
}

// ── Objects ───────────────────────────────────────────────────
WebServer server(80);
DHT dht(DHT_PIN, DHT_TYPE);

// ── State ─────────────────────────────────────────────────────
bool    isBuffering   = false;
int     targetRowId   = -1;    // row_id sent by backend trigger, -1 if not provided
unsigned long bufferStart = 0;
// start of the SAMPLING window — the pre-roll is already over by then
unsigned long samplingStart = 0;
bool         samplingBegun = false;

// Sample buffers
std::vector<float> buf1, buf2, buf3;

// ── LED helpers ───────────────────────────────────────────────
void ledOn()  { digitalWrite(LED_PIN, HIGH); }
void ledOff() { digitalWrite(LED_PIN, LOW);  }

void ledBlink(int times, int ms = 100) {
  for (int i = 0; i < times; i++) {
    ledOn();  delay(ms);
    ledOff(); delay(ms);
  }
}

// ── Indicator plumbing ──────────────────────────────────────────────────────
// The Indicator object is pure logic; these are the three sinks that let it touch
// hardware. Both RGB LEDs hang off the same R/G/B channels, so one write drives both.

static void indicatorLed(uint8_t r, uint8_t g, uint8_t b) {
  ledcWrite(IND_LED_R, r);
  ledcWrite(IND_LED_G, g);
  ledcWrite(IND_LED_B, b);
}

static HardwareSerial dfSerial(DFPLAYER_UART_NUM);
static IndClip   lastClipQueued   = CLIP_NONE;
static uint32_t  audioRetryAt     = 0;
static bool     audioReadyFlag   = false;

// DFPlayer TX → ESP32 RX (through a 1k series resistor, never straight in).
static bool dfSend(const uint8_t* frame) {
  return dfSerial.write((const char*)frame, DFPLAYER_FRAME_LEN) == DFPLAYER_FRAME_LEN;
}

// Sends folder+file so playback never depends on SD copy order.
static void indicatorClip(uint8_t clip) {
  uint8_t folder = 1, file = clip;
  if (!ind_clip_slot((IndClip)clip, &folder, &file)) return;
  uint8_t frame[DFPLAYER_FRAME_LEN];
  if (dfplayer_frame_play(frame, folder, file) == DFPLAYER_FRAME_LEN) {
    dfSend(frame);
    lastClipQueued = (IndClip)clip;
    Serial.println("  🔊 clip " + String(file) + " (" + ind_clip_name((IndClip)clip) + ")");
  }
}

static void indicatorLog(const char* msg) { Serial.println("  " + String(msg)); }

static IndConfig makeIndicatorConfig() {
  IndConfig c = ind_default_config();
  c.analyzeTimeoutMs = ANALYZE_TIMEOUT_MS;
  c.resultHoldMs     = RESULT_HOLD_MS;
  c.slowPulseMs      = SLOW_PULSE_MS;
  c.fastPulseMs      = FAST_PULSE_MS;
  c.cueCooldownMs    = CUE_COOLDOWN_MS;
  c.personTimeoutMs  = PERSON_HOLD_MS;
  c.commonAnode      = LED_COMMON_ANODE;
  return c;
}

Indicator indicator;   // configured in setup() via indicator.begin()

// Slow, non-blocking retry. A missing/unplugged DFPlayer must never block boot,
// sampling, the relays or the web server — so this only ever sends bytes.
static void serviceAudio(uint32_t now) {
  if (audioReadyFlag) return;
  if ((long)(now - audioRetryAt) < 0) return;
  audioRetryAt = now + DFPLAYER_AUDIO_RETRY_MS;
  uint8_t f[DFPLAYER_FRAME_LEN];
  bool ok = dfplayer_frame_reset(f) == DFPLAYER_FRAME_LEN;
  if (ok) ok = dfplayer_frame_select_sd(f) == DFPLAYER_FRAME_LEN && dfSend(f);
  if (ok) ok = dfplayer_frame_volume(f, DFPLAYER_VOLUME) == DFPLAYER_FRAME_LEN && dfSend(f);
  if (!ok) { indicator.setAudioReady(false); return; }

  // With a BUSY pin wired, wait for it to go LOW before claiming audio is ready.
  // Without it we can only confirm the bytes were written — reported honestly.
  if (DFPLAYER_BUSY_PIN >= 0) {
    uint32_t until = now + DFPLAYER_AUDIO_INIT_MS;
    while (millis() < until && digitalRead(DFPLAYER_BUSY_PIN) != LOW) delay(10);
    if (digitalRead(DFPLAYER_BUSY_PIN) != LOW) {
      indicator.setAudioReady(false);
      Serial.println("  ⚠ DFPlayer BUSY never went low — audio not ready, retrying later");
      return;
    }
  }
  audioReadyFlag = true;
  indicator.setAudioReady(true);
  Serial.println("  ✓ DFPlayer initialised (volume " + String(DFPLAYER_VOLUME) + ")");
}

// ── WiFi setup ────────────────────────────────────────────────
void connectWiFi() {
  Serial.println("\nConnecting to WiFi: " + String(WIFI_SSID));
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n✓ WiFi connected");
    Serial.println("  IP address: " + WiFi.localIP().toString());
    ledBlink(3, 200);  // 3 quick blinks = connected
  } else {
    Serial.println("\n✗ WiFi failed — restarting in 5s");
    delay(5000);
    ESP.restart();
  }
}

// ── Non-blocking WiFi watchdog ─────────────────────────────────────────────
// WHY THIS EXISTS: the watchdog used to call connectWiFi() straight from loop().
// connectWiFi() blocks for up to 30 x delay(500) = 15 s, preceded by
// ledBlink(2, 500) for another 2 s, and then calls ESP.restart() if the link is
// still down. The entire sampling run is only 3 s pre-roll + 5 s window = 8 s, so
// ONE WiFi blip blocked the loop for ~17 s and rebooted the board mid-run. The
// window was never posted and its training row was left with NULL mq3 and NULL
// temperature. A network problem must never destroy a measurement.
#define WIFI_RETRY_MS        5000UL   // how often to attempt a non-blocking reconnect
#define WIFI_RESTART_MS   120000UL   // only after this long offline, and only when idle

static uint32_t _wifiRetryAt    = 0;
static uint32_t _wifiDownSince  = 0;
static bool    _wifiWarned     = false;

// Non-blocking. Never restarts during a run: a lost link is recoverable, a lost
// measurement is not. A restart is allowed only after a long outage while idle,
// so a device parked next to a dead AP eventually recovers on its own.
void serviceWiFi(uint32_t now) {
  if (WiFi.status() == WL_CONNECTED) {
    if (_wifiWarned) Serial.println("✓ WiFi back");
    _wifiDownSince = 0;
    _wifiRetryAt   = 0;
    _wifiWarned    = false;
    return;
  }
  if (_wifiDownSince == 0) {
    _wifiDownSince = now;
    Serial.println("⚠ WiFi dropped — retrying non-blocking, sampling continues");
    _wifiWarned = true;
  }
  if ((int32_t)(now - _wifiRetryAt) >= 0) {
    _wifiRetryAt = now + WIFI_RETRY_MS;
    WiFi.reconnect();
  }
  bool runInFlight = isBuffering || relays.busy();
  if (!runInFlight && (uint32_t)(now - _wifiDownSince) > WIFI_RESTART_MS) {
    Serial.println("✗ WiFi down for too long while idle — restarting");
    delay(200);
    ESP.restart();
  }
}

// ── Read single ADC pin (averaged over 5 reads to reduce noise) ──
float readMQ3(int pin) {
  long sum = 0;
  for (int i = 0; i < 5; i++) {
    sum += analogRead(pin);
    delayMicroseconds(500);
  }
  // ESP32 ADC: 12-bit (0-4095), 3.3V reference
  // Return raw ADC value — backend expects raw sensor readings
  return (float)(sum / 5);
}

// ── HTTP endpoints ────────────────────────────────────────────

// POST /trigger
// Called by backend when proximity detected (Option B middleman)
// Optional body: { "row_id": 42 }
void handleTrigger() {
  // Intent is read from WHICH key the caller used: training sends row_id, the gate
  // sends reading_id. A body with neither is treated as training (legacy safety).
  int incomingId = -1;
  bool isDeploymentIntent = false;
  if (server.hasArg("plain")) {
    StaticJsonDocument<128> doc;
    DeserializationError err = deserializeJson(doc, server.arg("plain"));
    if (!err && (doc.containsKey("row_id") || doc.containsKey("reading_id"))) {
      isDeploymentIntent = !doc.containsKey("row_id") && doc.containsKey("reading_id");
      incomingId = doc.containsKey("row_id") ? doc["row_id"].as<int>() : doc["reading_id"].as<int>();
    }
  }

  // Busy means the relay sequence is mid-flight (pre-roll, sampling, post-roll or a
  // bench test) — not merely that the buffer is filling.
  if (isBuffering || relays.busy()) {
    // Already busy — reject without clobbering targetRowId
    String busyBody = "{\"status\":\"busy\",\"message\":\"Relay sequence busy — wait for the current run to finish\",\"busy\":true,\"busy_row_id\":" + String(targetRowId) + ",\"incoming_row_id\":" + String(incomingId) + ",\"sequence_state\":\"" + String(RelaySequence::stateName(relays.state())) + "\"}";
    server.send(409, "application/json", busyBody);
    Serial.println("⚠ Trigger rejected — already buffering (busy_row_id=" + String(targetRowId) + " incoming=" + String(incomingId) + ")");
    return;
  }
  // A gate trigger while the device is collecting training data would push a
  // deployment reading into a training session — refuse it. Training triggers
  // (row_id) are never blocked here.
  if (isDeploymentIntent && TRAINING_MODE) {
    server.send(409, "application/json",
      "{\"status\":\"blocked\",\"reason\":\"device_in_training_mode\",\"mode\":\"training\","
      "\"message\":\"Device is in training mode — gate trigger refused\"}");
    Serial.println("⚠ Deployment trigger refused — device is in TRAINING mode");
    return;
  }

  targetRowId = incomingId;
  // PIN the destination now. It is used when the window is posted, so flipping the
  // mode during the pre-roll or sampling window cannot redirect this reading.
  activeTrainingMode = TRAINING_MODE;

  // A new run always starts a new indicator run: bumps the counter, cancels any
  // verdict hold, and shows "sampling" from the START of the pre-roll. Display only —
  // it runs after the relay decision and cannot influence it.
  indicator.onTrigger(millis(), incomingId, incomingId > 0);

  // Relays ON now; sampling only begins once the pre-roll has elapsed.
  // Identical in training and deployment mode — airflow must not vary by mode.
  if (!relays.start(millis())) {
    server.send(409, "application/json", "{\"status\":\"busy\",\"busy\":true}");
    return;
  }

  // Start buffering
  buf1.clear();
  buf2.clear();
  buf3.clear();
  isBuffering  = true;
  bufferStart  = millis();

  Serial.println("▶ Trigger received — relays ON, pre-roll " + String(PREROLL_MS) + "ms, then sampling MQ3 for " + String(BUFFER_DURATION_MS) + "ms");
  if (targetRowId > 0) Serial.println("  row_id: " + String(targetRowId));

  ledOn();  // LED stays on during buffering

  server.send(200, "application/json",
    "{\"status\":\"ok\",\"message\":\"Relay sequence started\",\"duration_ms\":" +
    String(BUFFER_DURATION_MS) + ",\"preroll_ms\":" + String(PREROLL_MS) + ",\"postroll_ms\":" + String(POSTROLL_MS) + "}");
}

// GET /status
// Backend polls this to check if ESP32 is alive
void handleStatus() {
  float temp = dht.readTemperature();
  float hum  = dht.readHumidity();

  String body = "{";
  body += "\"status\":\"online\",";
  body += "\"buffering\":" + String(isBuffering ? "true" : "false") + ",";
  body += "\"mode\":\"" + String(TRAINING_MODE ? "training" : "deployment") + "\",";
  body += "\"temperature\":" + (isnan(temp) ? "null" : String(temp, 1)) + ",";
  body += "\"humidity\":"    + (isnan(hum)  ? "null" : String(hum, 1))  + ",";
  body += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  body += "\"sequence_state\":\"" + String(RelaySequence::stateName(relays.state())) + "\",";
  body += "\"preroll_ms\":" + String(PREROLL_MS) + ",";
  body += "\"buffer_ms\":" + String(BUFFER_DURATION_MS) + ",";
  body += "\"postroll_ms\":" + String(POSTROLL_MS) + ",";
  body += "\"fan_on\":" + String(relays.fanOn() ? "true" : "false") + ",";
  body += "\"blower_on\":" + String(relays.blowerOn() ? "true" : "false") + ",";
  // ── Indicator additions (existing keys above are untouched) ──
  body += "\"indicator_state\":\"" + String(indicator.stateName()) + "\",";
  body += "\"indicator_enabled\":" + String(tokenConfigured() ? "true" : "false") + ",";
  body += "\"audio_ready\":" + String(indicator.audioReady() ? "true" : "false") + ",";
  body += "\"run_id\":" + String(indicator.runId()) + ",";
  body += "\"trigger_run_id\":" + String(indicator.triggerRunId()) + ",";
  body += "\"indicator_timeout_ms\":" + String(ANALYZE_TIMEOUT_MS) + ",";
  body += "\"indicator_hold_ms\":" + String(RESULT_HOLD_MS) + ",";
  body += "\"last_clip\":" + String((int)lastClipQueued) + ",";
  body += "\"uptime_s\":" + String(millis() / 1000);
  body += "}";

  server.send(200, "application/json", body);
}

// GET /mode?training=1  or  GET /mode?training=0
// Switch between training and deployment mode without reflashing
void handleMode() {
  if (server.hasArg("training")) {
    // Refuse while a sequence is mid-flight. Even though the destination is pinned,
    // switching now would leave the UI claiming a mode the next reading will not use.
    if (relays.busy()) {
      server.send(409, "application/json",
        "{\"error\":\"Relay sequence running — wait until idle before changing mode\",\"sequence_state\":\"" +
        String(RelaySequence::stateName(relays.state())) + "\"}");
      Serial.println("⚠ Mode change refused — sequence is " + String(RelaySequence::stateName(relays.state())));
      return;
    }
    TRAINING_MODE = server.arg("training") == "1";
    saveModeToNvs();
    Serial.println("◉ Mode switched to: " + String(modeName()) + " (saved to NVS)");
    server.send(200, "application/json",
      "{\"mode\":\"" + String(modeName()) + "\",\"message\":\"Mode updated\"}");
  } else {
    server.send(400, "application/json", "{\"error\":\"Pass ?training=1 or ?training=0\"}");
  }
}

// GET /relay_test?fan=0|1&blower=0|1&ms=N
// Bench/wiring check only. Idle-only, ms clamped by the state machine, auto-offs.
void handleRelayTest() {
  if (!relays.busy()) {
    bool fan    = server.hasArg("fan")    && server.arg("fan") == "1";
    bool blower = server.hasArg("blower") && server.arg("blower") == "1";
    unsigned long ms = 2000;
    if (server.hasArg("ms")) ms = (unsigned long)atol(server.arg("ms").c_str());
    if (relays.test(fan, blower, ms, millis())) {
      server.send(200, "application/json",
        "{\"status\":\"ok\",\"message\":\"Relay test started\",\"test_ms\":" +
        String(relays.testMs()) + "}");
      Serial.println("◉ Relay test — fan=" + String(fan) + " blower=" + String(blower) +
                     " for " + String(relays.testMs()) + "ms");
    } else {
      server.send(409, "application/json", "{\"status\":\"busy\",\"busy\":true}");
    }
  } else {
    server.send(409, "application/json",
      "{\"status\":\"busy\",\"message\":\"Sequence busy\",\"busy\":true}");
  }
}

// POST /indicator   { "state": "...", "run_id": N, "volume": 0..30 }
// Display only. Authenticated by the shared token. This is the ONLY route that can
// turn a verdict LED on, and it cannot do so without a matching run id while the
// device is ANALYZING.
void handleIndicator() {
  // No token compiled in -> refuse, and the indicators stay OFF. Fail closed.
  if (!tokenConfigured()) {
    server.send(503, "application/json",
      "{\"error\":\"indicator token not configured on device\",\"indicator_enabled\":false}");
    return;
  }
  if (!server.hasHeader("X-Device-Token") || !tokenMatches(server.header("X-Device-Token"))) {
    server.send(401, "application/json", "{\"error\":\"unauthorized\"}");
    Serial.println("  ⚠ /indicator rejected — bad or missing token");
    return;
  }

  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, server.arg("plain"));
  if (err || !doc.containsKey("state") || !doc["state"].is<const char*>()) {
    server.send(400, "application/json", "{\"error\":\"missing or malformed 'state'\"}");
    return;
  }
  const char* state = doc["state"];

  // Optional volume for the bench.
  if (doc.containsKey("volume")) {
    int v = doc["volume"].as<int>();
    if (v < 0) v = 0;
    if (v > DFPLAYER_VOLUME) v = DFPLAYER_VOLUME;
    uint8_t f[DFPLAYER_FRAME_LEN];
    if (dfplayer_frame_volume(f, (uint8_t)v) == DFPLAYER_FRAME_LEN) dfSend(f);
  }

  bool hasRunId = doc.containsKey("run_id") && doc["run_id"].is<int64_t>();
  long runId = hasRunId ? (long)doc["run_id"].as<int64_t>() : 0;

  IndicatorState want;
  if (!ind_state_parse(state, &want)) {
    // Unknown state name: keep the current state and the LEDs exactly as they are.
    server.send(400, "application/json",
      "{\"error\":\"unknown state\",\"indicator_state\":\"" + String(indicator.stateName()) + "\"}");
    Serial.println("  ⚠ /indicator rejected — unknown state name");
    return;
  }

  uint32_t now = millis();
  bool ok = false;
  if (ind_is_verdict(want)) {
    // The only path to GREEN/WARN/FAIL: must match the CURRENT run while ANALYZING.
    ok = indicator.submitVerdict(state, hasRunId, runId, now);
    if (!ok) {
      server.send(409, "application/json",
        "{\"error\":\"verdict refused\",\"reason\":\"stale_or_unmatched_run\","
        "\"indicator_state\":\"" + String(indicator.stateName()) + "\","
        "\"run_id\":" + String(indicator.runId()) + "}");
      Serial.println("  ⚠ Verdict refused — run id " + String(runId) + " while state " +
                     String(indicator.stateName()));
      return;
    }
  } else {
    // The bench test path may drive any non-verdict state, but only while idle.
    if (relays.busy() && (want == IND_SAMPLING || want == IND_ANALYZING)) {
      server.send(409, "application/json",
        "{\"error\":\"sequence running\",\"sequence_state\":\"" +
        String(RelaySequence::stateName(relays.state())) + "\"}");
      return;
    }
    ok = indicator.applyProgress(state, now);
    if (!ok) {
      server.send(409, "application/json",
        "{\"error\":\"state refused\",\"indicator_state\":\"" +
        String(indicator.stateName()) + "\",\"mode\":\"" + String(modeName()) + "\"}");
      return;
    }
  }

  server.send(200, "application/json",
    "{\"status\":\"ok\",\"indicator_state\":\"" + String(indicator.stateName()) + "\","
    "\"run_id\":" + String(indicator.runId()) + ",\"audio_ready\":" +
    String(audioReadyFlag ? "true" : "false") + "}");
}

// 404 fallback
void handleNotFound() {
  server.send(404, "application/json", "{\"error\":\"Endpoint not found\"}");
}

// ── Post sensor data to backend ───────────────────────────────
void postSensorData() {
  if (buf1.size() < MIN_SAMPLES) {
    Serial.println("✗ Not enough samples: " + String(buf1.size()) + " (need " + String(MIN_SAMPLES) + ")");
    ledBlink(5, 80);  // rapid blinks = error
    return;
  }

  // Read DHT11
  float temperature = dht.readTemperature();
  float humidity    = dht.readHumidity();

  if (isnan(temperature) || isnan(humidity)) {
    Serial.println("⚠ DHT11 read failed — using fallback values");
    temperature = 25.0;
    humidity    = 60.0;
  }

  // Build JSON body
  StaticJsonDocument<2048> doc;

  doc["temperature"] = temperature;
  doc["humidity"]    = humidity;
  // Tag the payload with the mode it was sampled in, so the backend can refuse a
  // reading that arrives at the wrong endpoint instead of trusting it blindly.
  doc["mode"] = activeTrainingMode ? MODE_TRAINING : MODE_DEPLOYMENT;

  // Add both row_id and reading_id so training (/training/collect/sensor-data expects row_id)
  // and deployment (/sensor expects reading_id) both find the field they look for
  if (targetRowId > 0) {
    doc["row_id"] = targetRowId;
    doc["reading_id"] = targetRowId;
  }

  // MQ3 arrays
  JsonArray a1 = doc.createNestedArray("mq3_1");
  JsonArray a2 = doc.createNestedArray("mq3_2");
  JsonArray a3 = doc.createNestedArray("mq3_3");

  for (float v : buf1) a1.add(v);
  for (float v : buf2) a2.add(v);
  for (float v : buf3) a3.add(v);

  String payload;
  serializeJson(doc, payload);

  // Choose endpoint from the mode PINNED at trigger time (not the live mode).
  String endpoint = activeTrainingMode ? TRAINING_ENDPOINT : DEPLOYMENT_ENDPOINT;
  String url = "http://" + String(BACKEND_IP) + ":" + String(BACKEND_PORT) + endpoint;

  Serial.println("→ Posting to: " + url);
  Serial.println("  Samples: " + String(buf1.size()));
  Serial.println("  Temp: " + String(temperature, 1) + "°C  Hum: " + String(humidity, 1) + "%");
  Serial.println("  MQ3-1 max: " + String(*max_element(buf1.begin(), buf1.end()), 0));
  Serial.println("  MQ3-2 max: " + String(*max_element(buf2.begin(), buf2.end()), 0));
  Serial.println("  MQ3-3 max: " + String(*max_element(buf3.begin(), buf3.end()), 0));

  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(10000);  // 10s timeout

  int code = http.POST(payload);

  if (code == 200 || code == 201) {
    String response = http.getString();
    Serial.println("✓ Backend response (" + String(code) + "): " + response);
    ledBlink(2, 300);  // 2 slow blinks = success
  } else if (code > 0) {
    Serial.println("✗ Backend error " + String(code) + ": " + http.getString());
    ledBlink(4, 100);  // 4 fast blinks = backend error
  } else {
    Serial.println("✗ HTTP failed: " + http.errorToString(code));
    ledBlink(6, 80);   // 6 rapid blinks = connection error
  }

  http.end();
}

// ── Setup ─────────────────────────────────────────────────────
void setup() {
  // ── Relays OFF first ──────────────────────────────────────────────
  // Before Serial, Wi-Fi and everything else, so the pins are never floating and the
  // external pull-ups hold the relays off while the ESP32 boots.
  pinMode(FAN_RELAY_PIN, OUTPUT);
  pinMode(BLOWER_RELAY_PIN, OUTPUT);

  uint8_t offLevel = RELAY_ACTIVE_LOW ? HIGH : LOW;
  digitalWrite(FAN_RELAY_PIN, offLevel);
  digitalWrite(BLOWER_RELAY_PIN, offLevel);
  relays.tick(millis());   // re-assert OFF through the state machine too

  Serial.begin(115200);
  delay(1000);

  Serial.println("\n=============================");
  Serial.println(" AlcoDetect ESP32 Firmware");
  Serial.println("=============================");

  // Mode is loaded from NVS before the server accepts anything.
  loadModeFromNvs();
  Serial.println(String("  DEVICE MODE: ") + String(modeName()) + (TRAINING_MODE ? "  <<< TRAINING — readings go to training_data" : "  (readings go to sensor_readings)"));

  // Pin modes
  pinMode(LED_PIN, OUTPUT);

  ledOff();

  // ── Indicators: outputs driven to OFF before anything else can run ───────────
  // Boot state is OFF and stays OFF until an authenticated command or a run moves it.
  // Nothing plays until the DFPlayer initialises successfully.
  ledcAttach(IND_LED_R, IND_LED_PWM_HZ, IND_LED_BITS);
  ledcAttach(IND_LED_G, IND_LED_PWM_HZ, IND_LED_BITS);
  ledcAttach(IND_LED_B, IND_LED_PWM_HZ, IND_LED_BITS);
  indicatorLed(0, 0, 0);
  IndConfig indCfg = makeIndicatorConfig();
  indicator.begin(indicatorLed, indicatorClip, indicatorLog, &indCfg);
  indicator.setTrainingMode(TRAINING_MODE);   // training => LEDs OFF, no clips

  // DFPlayer: UART comes up, but audio only reports ready after init succeeds.
  // A missing module, wrong baud or absent SD card must never block boot.
  dfSerial.begin(DFPLAYER_BAUD, SERIAL_8N1, DFPLAYER_RX_PIN, DFPLAYER_TX_PIN);
  if (DFPLAYER_BUSY_PIN >= 0) pinMode(DFPLAYER_BUSY_PIN, INPUT);
  audioRetryAt = 0;

  // ADC settings for ESP32
  // 12-bit resolution (0-4095), 3.3V reference
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);  // full range 0-3.3V

  // DHT11
  dht.begin();
  delay(2000);  // DHT11 needs 2s to stabilize

  // Test DHT11
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (isnan(t) || isnan(h)) {
    Serial.println("⚠ DHT11 not responding — check wiring on GPIO " + String(DHT_PIN));
  } else {
    Serial.println("✓ DHT11: " + String(t, 1) + "°C  " + String(h, 1) + "%");
  }

  // WiFi
  connectWiFi();

  // HTTP server routes
  server.on("/trigger", HTTP_POST, handleTrigger);
  server.on("/status",  HTTP_GET,  handleStatus);
  server.on("/relay_test", HTTP_GET, handleRelayTest);
  server.on("/mode",    HTTP_GET,  handleMode);
  server.on("/indicator", HTTP_POST, handleIndicator);
  server.onNotFound(handleNotFound);
  server.begin();

  Serial.println("\n✓ HTTP server started on port 80");
  Serial.println("  POST http://" + WiFi.localIP().toString() + "/trigger");
  Serial.println("  GET  http://" + WiFi.localIP().toString() + "/status");
  Serial.println("  GET  http://" + WiFi.localIP().toString() + "/mode?training=1");
  Serial.println("\n  ───────────────────────────────────────────");
  Serial.println("  DEVICE MODE: " + String(modeName()));
  Serial.println("  POST target: " + String(activeTrainingMode ? TRAINING_ENDPOINT : DEPLOYMENT_ENDPOINT) + " (pinned at trigger)");
  if (TRAINING_MODE) {
    Serial.println("  ⚠ TRAINING MODE — sampled windows are written to the TRAINING table.");
    Serial.println("    Switch back with: GET /mode?training=0  (or the Settings page)");
  } else {
    Serial.println("  Deployment mode — sampled windows are written to the DEPLOYMENT table.");
  }
  Serial.println("  ───────────────────────────────────────────");
  Serial.println("  Backend: " + String(BACKEND_IP) + ":" + String(BACKEND_PORT));
  Serial.println("\nReady — waiting for trigger from backend...\n");

  // Warm-up blink
  ledBlink(5, 100);
}

// ── Main loop ─────────────────────────────────────────────────
void loop() {
  // Always handle HTTP requests
  server.handleClient();

  // Advance the relay sequence (non-blocking). Never delays the web server.
  relays.tick(millis());

  // Indicators and audio are driven entirely from millis(); no delay(), no blocking.
  // Ticking BEFORE the buffering block means a verdict arriving in the same loop
  // iteration can already drive the LED, and the timeout can fire on time.
  indicator.setTrainingMode(TRAINING_MODE);
  serviceAudio(millis());
  indicator.tick(millis());

  // WiFi watchdog — non-blocking. It must never stall the loop or restart the board
  // mid-run; see serviceWiFi() for why that used to destroy every measurement.
  serviceWiFi(millis());

  // ── Stuck guard ──────────────────────────────────────────────
  // If the relay sequence has returned to idle while the buffer is still marked
  // active (e.g. the failsafe fired because the window never completed), clear the
  // flag. Otherwise isBuffering stays true forever and EVERY /trigger is refused
  // with 409 — the device would be dead for collection with no visible cause.
  if (isBuffering && relays.state() == RelaySequence::IDLE) {
    Serial.println("⚠ Buffer flag stranded — relay sequence is idle; clearing so triggers work again");
    isBuffering = false;
    samplingBegun = false;
    ledOff();
    buf1.clear();
    buf2.clear();
    buf3.clear();
    targetRowId = -1;
  }

  // ── Buffering loop ──────────────────────────────────────────
  if (isBuffering) {
    // Sampling starts only after the pre-roll; samples taken during the
    // pre-roll must never reach the buffer.
    if (!relays.mayBeginSampling(millis())) {
      // pre-roll still running — relays are ON, but nothing is sampled yet
    } else {
      if (!samplingBegun) { samplingBegun = true; samplingStart = millis(); }
      unsigned long elapsed = millis() - samplingStart;

      if (elapsed < BUFFER_DURATION_MS) {
        // Still within window — sample sensors
        float v1 = readMQ3(MQ3_PIN_1);
        float v2 = readMQ3(MQ3_PIN_2);
        float v3 = readMQ3(MQ3_PIN_3);
        Serial.print("MQ1: ");
        Serial.println(v1);
         Serial.print("MQ2: ");
        Serial.println(v2);
         Serial.print("MQ3: ");
        Serial.println(v3);
        buf1.push_back(v1);
        buf2.push_back(v2);
        buf3.push_back(v3);

        // Debug print every 500ms
        if (buf1.size() % 5 == 0) {
          Serial.printf("  [%lu ms] MQ3: %.0f | %.0f | %.0f  (%d samples)\n",
            elapsed, v1, v2, v3, (int)buf1.size());
        }

        delay(SAMPLE_INTERVAL_MS);
      } else {
        // Window elapsed — hand over to POSTROLL (relays stay on).
        // This MUST live in the else branch, not inside the sampling branch: calling
        // finishSampling() while still sampling pushed the state machine straight to
        // POSTROLL on the very first sample, so mayBeginSampling() went false, the
        // remaining ~4.5 s of the window were never sampled, and the stuck guard
        // then cleared isBuffering two seconds later. Every run produced a single
        // MQ1/MQ2/MQ3 line and no window was ever posted.
        relays.finishSampling(millis());
        samplingBegun = false;
        // Display only: the window is closed, the device now waits on the backend.
        // The analyze deadline starts here, and tick() turns a missing verdict YELLOW.
        indicator.onSamplingEnd(millis());

        // Window complete — stop buffering
        isBuffering = false;
        ledOff();

      Serial.println("■ Buffer complete — " + String(buf1.size()) + " samples collected");

      // Post to backend
      postSensorData();

      // Reset
      targetRowId = -1;
      buf1.clear();
      buf2.clear();
      buf3.clear();

      Serial.println("\nReady — waiting for next trigger...\n");
    }
  }
  }

  // ── Idle monitoring — prints sensor + DHT every 2s when not buffering ──
  // Watch baseline recovery before sending next trigger
  if (!isBuffering) {
//    static unsigned long lastIdlePrint = 0;
//    unsigned long now = millis();
//
//    if (now - lastIdlePrint >= 2000) {
//      lastIdlePrint = now;
//
//      float v1   = readMQ3(MQ3_PIN_1);
//      float v2   = readMQ3(MQ3_PIN_2);
//      float v3   = readMQ3(MQ3_PIN_3);
//      float temp = dht.readTemperature();
//      float hum  = dht.readHumidity();
//
//      Serial.println("─────────────────────────────");
//      Serial.printf("  MQ3-1: %.0f  MQ3-2: %.0f  MQ3-3: %.0f\n", v1, v2, v3);
//      if (isnan(temp) || isnan(hum)) {
//        Serial.println("  Temp: --.-°C   Hum: --.-%");
//      } else {
//        Serial.printf("  Temp: %.1f°C   Hum: %.1f%%\n", temp, hum); 
//      }
//      Serial.println("  (idle — waiting for trigger)");
//    }

    delay(10);
  }
} 
