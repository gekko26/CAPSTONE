/*
 * AlcoDetect — ESP32-WROOM-32 Firmware
 * =====================================
 * Board:    ESP32-WROOM-32 (use "ESP32 Dev Module" in Arduino IDE)
 * Sensors:  MQ3 x3 (analog ADC), DHT22 (digital)
 * Trigger:  HTTP POST /trigger from backend (Option B — middleman)
 * Posts to: POST http://{BACKEND_IP}:8000/training/collect/sensor-data
 *
 * WIRING:
 * ┌─────────────────────────────────────────────────────┐
 * │  MQ3 Sensor 1                                       │
 * │    VCC  → 3.3V (or 5V if your module has regulator)│
 * │    GND  → GND                                       │
 * │    AOUT → GPIO 34  (ADC1_CH6, input only)          │
 * │                                                     │
 * │  MQ3 Sensor 2                                       │
 * │    VCC  → 3.3V                                      │
 * │    GND  → GND                                       │
 * │    AOUT → GPIO 35  (ADC1_CH7, input only)          │
 * │                                                     │
 * │  MQ3 Sensor 3                                       │
 * │    VCC  → 3.3V                                      │
 * │    GND  → GND                                       │
 * │    AOUT → GPIO 32  (ADC1_CH4)                      │
 * │                                                     │
 * │  DHT22                                              │
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
 * GPIO 4 is a safe general-purpose digital pin for DHT22
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

// ── WiFi credentials ──────────────────────────────────────────
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// ── Backend URL ───────────────────────────────────────────────
// Change to your laptop/Pi IP. Must be on same WiFi network.
// Training:   http://192.168.x.x:8000/training/collect/sensor-data
// Deployment: http://192.168.x.x:8000/sensor
const char* BACKEND_IP      = "192.168.1.x";   // ← change this
const int   BACKEND_PORT    = 8000;
const char* TRAINING_ENDPOINT  = "/training/collect/sensor-data";
const char* DEPLOYMENT_ENDPOINT = "/sensor";

// ── Pin definitions ───────────────────────────────────────────
#define MQ3_PIN_1    34    // ADC1_CH6 — sensor 1 (top-left in enclosure)
#define MQ3_PIN_2    35    // ADC1_CH7 — sensor 2 (top-right in enclosure)
#define MQ3_PIN_3    32    // ADC1_CH4 — sensor 3 (bottom-center in enclosure)
#define DHT_PIN       4    // DHT22 data pin
#define DHT_TYPE    DHT22
#define LED_PIN       2    // Built-in LED — status indicator


// ── Sampling config ───────────────────────────────────────────
#define SAMPLE_INTERVAL_MS   100   // 100ms between samples = 10 samples/sec
#define BUFFER_DURATION_MS  3000   // 3 second window (30 samples at 100ms)
#define MIN_SAMPLES            5   // minimum samples required by backend

// ── Mode ──────────────────────────────────────────────────────
// true  = training mode  → posts to /training/collect/sensor-data
// false = deployment mode → posts to /sensor
bool TRAINING_MODE = true;   // ← change to false for deployment

// ── Objects ───────────────────────────────────────────────────
WebServer server(80);
DHT dht(DHT_PIN, DHT_TYPE);

// ── State ─────────────────────────────────────────────────────
bool    isBuffering   = false;
int     targetRowId   = -1;    // row_id sent by backend trigger, -1 if not provided
unsigned long bufferStart = 0;

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
  // Parse optional row_id from body
  targetRowId = -1;
  if (server.hasArg("plain")) {
    StaticJsonDocument<128> doc;
    DeserializationError err = deserializeJson(doc, server.arg("plain"));
    if (!err && doc.containsKey("row_id")) {
      targetRowId = doc["row_id"].as<int>();
    }
  }

  if (isBuffering) {
    // Already buffering — reject to avoid overlap
    server.send(409, "application/json",
      "{\"status\":\"busy\",\"message\":\"Already buffering — wait for current window to finish\"}");
    Serial.println("⚠ Trigger rejected — already buffering");
    return;
  }

  // Start buffering
  buf1.clear();
  buf2.clear();
  buf3.clear();
  isBuffering  = true;
  bufferStart  = millis();

  Serial.println("▶ Trigger received — buffering MQ3 for " + String(BUFFER_DURATION_MS) + "ms");
  if (targetRowId > 0) Serial.println("  row_id: " + String(targetRowId));

  ledOn();  // LED stays on during buffering

  server.send(200, "application/json",
    "{\"status\":\"ok\",\"message\":\"Buffering started\",\"duration_ms\":" +
    String(BUFFER_DURATION_MS) + "}");
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

  // Read DHT22
  float temperature = dht.readTemperature();
  float humidity    = dht.readHumidity();

  if (isnan(temperature) || isnan(humidity)) {
    Serial.println("⚠ DHT22 read failed — using fallback values");
    temperature = 25.0;
    humidity    = 60.0;
  }

  // Build JSON body
  StaticJsonDocument<2048> doc;

  doc["temperature"] = temperature;
  doc["humidity"]    = humidity;

  // Add row_id if provided by backend trigger
  if (targetRowId > 0) {
    doc["row_id"] = targetRowId;
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

  // Choose endpoint based on mode
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

  // DHT22
  dht.begin();
  delay(2000);  // DHT22 needs 2s to stabilize

  // Test DHT22
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (isnan(t) || isnan(h)) {
    Serial.println("⚠ DHT22 not responding — check wiring on GPIO " + String(DHT_PIN));
  } else {
    Serial.println("✓ DHT22: " + String(t, 1) + "°C  " + String(h, 1) + "%");
  }

  // WiFi
  connectWiFi();

  // HTTP server routes
  server.on("/trigger", HTTP_POST, handleTrigger);
  server.on("/status",  HTTP_GET,  handleStatus);
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

  // WiFi watchdog — reconnect if dropped
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("⚠ WiFi disconnected — reconnecting...");
    ledBlink(2, 500);
    connectWiFi();
  }

  // ── Buffering loop ──────────────────────────────────────────
  if (isBuffering) {
    unsigned long elapsed = millis() - bufferStart;

    if (elapsed < BUFFER_DURATION_MS) {
      // Still within window — sample sensors
      float v1 = readMQ3(MQ3_PIN_1);
      float v2 = readMQ3(MQ3_PIN_2);
      float v3 = readMQ3(MQ3_PIN_3);

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

  // Small delay when idle to prevent watchdog reset
  if (!isBuffering) {
    delay(10);
  }
}
