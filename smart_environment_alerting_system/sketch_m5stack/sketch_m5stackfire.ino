#include <M5Unified.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include "M5UnitENV.h"   // from M5Unit-ENV library

// ---------- USER SETTINGS ----------
const char* WIFI_SSID     = "Your_WiFi_SSID";
const char* WIFI_PASSWORD = "Your_WiFi_Password";

// Example:
// https://<functionapp>.azurewebsites.net/api/ingest?code=<FUNCTION_KEY>
const char* INGEST_URL = "Your_Function_App_URL";

const char* DEVICE_ID = "m5stack-fire-01";
const uint32_t SEND_INTERVAL_MS = 5000;

// M5Stack Core/FIRE Grove I2C pins (Port A)
static const int I2C_SDA = 21;
static const int I2C_SCL = 22;

// ---------- ENV (SHT30) ----------
SHT3X sht3x;

uint32_t lastSendMs = 0;

bool ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) return true;

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - start) < 10000) {
    delay(200);
    M5.update();
  }
  return WiFi.status() == WL_CONNECTED;
}

String postTelemetry(float tempC, float humidity) {
  if (!ensureWiFi()) return "wifi_offline";

  HTTPClient http;
  http.begin(INGEST_URL);
  http.addHeader("Content-Type", "application/json");

  String body = "{";
  body += "\"deviceId\":\"" + String(DEVICE_ID) + "\",";
  body += "\"tempC\":" + String(tempC, 2) + ",";
  body += "\"humidity\":" + String(humidity, 2);
  body += "}";

  int code = http.POST(body);
  http.end();

  if (code <= 0) return "http_err";
  return String(code);
}

void drawUI(float t, float h, const String& wifi, const String& post) {
  M5.Display.clear();
  M5.Display.setCursor(0, 0);

  M5.Display.setTextSize(2);
  M5.Display.printf("ENV Telemetry\n\n");

  M5.Display.setTextSize(3);
  if (!isnan(t) && !isnan(h)) {
    M5.Display.printf("T: %.2f C\n", t);
    M5.Display.printf("H: %.2f %%\n", h);
  } else {
    M5.Display.println("No Sensor");
  }

  M5.Display.setTextSize(1);
  M5.Display.printf("\nWiFi: %s\n", wifi.c_str());
  M5.Display.printf("POST: %s\n", post.c_str());
  M5.Display.printf("BtnA=Send  BtnB=Reconnect\n");
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);

  // Typical for M5Stack Core/FIRE
  M5.Display.setRotation(1);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);

  // Init ENV(SHT30) on Port A (SDA=21, SCL=22)
  // You can also do Wire.begin(I2C_SDA, I2C_SCL) if you prefer,
  // but the library supports passing pins directly.
  if (!sht3x.begin(&Wire, SHT3X_I2C_ADDR, I2C_SDA, I2C_SCL, 400000U)) {
    M5.Display.clear();
    M5.Display.setCursor(0, 0);
    M5.Display.setTextSize(2);
    M5.Display.println("SHT3X FAIL");
    M5.Display.setTextSize(1);
    M5.Display.println("Check ENV wiring to Port A");
    while (true) delay(1000);
  }

  ensureWiFi();
  drawUI(NAN, NAN, (WiFi.status() == WL_CONNECTED ? "connected" : "offline"), "n/a");
}

void loop() {
  M5.update();

  float tempC = NAN, hum = NAN;

  if (sht3x.update()) {
    tempC = sht3x.cTemp;
    hum   = sht3x.humidity;
  }

  // Send every interval
  if (millis() - lastSendMs >= SEND_INTERVAL_MS) {
    lastSendMs = millis();
    String wifi = (WiFi.status() == WL_CONNECTED ? "connected" : "offline");
    String post = (isnan(tempC) ? "no_sensor" : postTelemetry(tempC, hum));
    drawUI(tempC, hum, wifi, post);
  }

  // BtnA: force send
  if (M5.BtnA.wasPressed() && !isnan(tempC)) {
    String wifi = (WiFi.status() == WL_CONNECTED ? "connected" : "offline");
    String post = postTelemetry(tempC, hum);
    drawUI(tempC, hum, wifi, post);
  }

  // BtnB: force WiFi reconnect
  if (M5.BtnB.wasPressed()) {
    WiFi.disconnect(true, true);
    delay(200);
    ensureWiFi();
    String wifi = (WiFi.status() == WL_CONNECTED ? "connected" : "offline");
    drawUI(tempC, hum, wifi, "reconnect");
  }

  delay(50);
}
