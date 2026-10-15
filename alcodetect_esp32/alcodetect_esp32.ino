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
#include "relay_sequence.h"

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
bool TRAINING_MODE = true;   // ← change to false for deployment

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
  // Parse optional row_id from body — parse to incomingId first to avoid overwriting targetRowId on 409 busy
  int incomingId = -1;
  if (server.hasArg("plain")) {
    StaticJsonDocument<128> doc;
    DeserializationError err = deserializeJson(doc, server.arg("plain"));
    if (!err && (doc.containsKey("row_id") || doc.containsKey("reading_id"))) {
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
  targetRowId = incomingId;
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
  body += "\"uptime_s\":" + String(millis() / 1000);
  body += "}";

  server.send(200, "application/json", body);
}

// GET /mode?training=1  or  GET /mode?training=0
// Switch between training and deployment mode without reflashing
void handleMode() {
  if (server.hasArg("training")) {
    TRAINING_MODE = server.arg("training") == "1";
    String mode = TRAINING_MODE ? "training" : "deployment";
    Serial.println("◉ Mode switched to: " + mode);
    server.send(200, "application/json",
      "{\"mode\":\"" + mode + "\",\"message\":\"Mode updated\"}");
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
  String endpoint = TRAINING_MODE ? TRAINING_ENDPOINT : DEPLOYMENT_ENDPOINT;
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


  // Pin modes
  pinMode(LED_PIN, OUTPUT);

  ledOff();

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
  server.onNotFound(handleNotFound);
  server.begin();

  Serial.println("\n✓ HTTP server started on port 80");
  Serial.println("  POST http://" + WiFi.localIP().toString() + "/trigger");
  Serial.println("  GET  http://" + WiFi.localIP().toString() + "/status");
  Serial.println("  GET  http://" + WiFi.localIP().toString() + "/mode?training=1");
  Serial.println("\n  Mode: " + String(TRAINING_MODE ? "TRAINING" : "DEPLOYMENT"));
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

  // WiFi watchdog — reconnect if dropped
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("⚠ WiFi disconnected — reconnecting...");
    ledBlink(2, 500);
    connectWiFi();
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

      // window elapsed — hand over to POSTROLL (relays stay on)
      relays.finishSampling(millis());
      samplingBegun = false;
    } else {
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
