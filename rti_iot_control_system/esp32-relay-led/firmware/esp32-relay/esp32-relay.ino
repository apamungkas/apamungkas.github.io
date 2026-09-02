// =============================================================================
// esp32-relay.ino  —  ESP32 relay -> LED, dikontrol dari Azure IoT Hub
// -----------------------------------------------------------------------------
// Requirement tambahan: dari Azure bisa menyalakan/mematikan LED via relay.
//
// Alur:
//   Azure Function (controlRelay) -> IoT Hub Direct Method -> ESP32 -> Relay -> LED
//
// Direct Method (cloud-to-device):
//   * setRelay   : {"state":"on|off"}   atau  {"on": true|false}
//   * toggleRelay: {}
//   * getState   : {}                    (balikan status relay saat ini)
//   * reboot     : {}
//
// Telemetry (device-to-cloud): status relay dikirim berkala + setiap kali berubah,
// sehingga bisa dipantau di Fabric Real-Time Dashboard.
//
// Library (Arduino Library Manager):
//   - PubSubClient (Nick O'Leary)
//   - ArduinoJson  (Benoit Blanchon, v6+)
// Board: generic "ESP32 Dev Module" (mis. ESP32-WROOM DevKit).
//
// Wiring relay module (aktif-LOW umum):
//   ESP32 GPIO26 -> IN relay
//   ESP32 5V     -> VCC relay      (modul relay 5V; sebagian modul 3.3V)
//   ESP32 GND    -> GND relay
//   LED: sumber + -> COM relay ; NO relay -> LED(+) ; LED(-) -> GND sumber
// =============================================================================

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>

#include "config.h"
#include "sas_token.h"

// ------------------------- Azure IoT Hub MQTT topics -------------------------
static const char *MQTT_TELEMETRY_TOPIC =
    "devices/" IOT_DEVICE_ID "/messages/events/";
static const char *MQTT_METHOD_SUB_TOPIC = "$iothub/methods/POST/#";

// ------------------------- Root CA (DigiCert Global Root G2) -------------------------
// Azure IoT Hub memakai rantai sertifikat DigiCert Global Root G2.
static const char *AZURE_ROOT_CA =
    "-----BEGIN CERTIFICATE-----\n"
    "MIIDjjCCAnagAwIBAgIQAzrx5qcRqaC7KGSxHQn65TANBgkqhkiG9w0BAQsFADBh\n"
    "MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3\n"
    "d3cuZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBH\n"
    "MjAeFw0xMzA4MDExMjAwMDBaFw0zODAxMTUxMjAwMDBaMGExCzAJBgNVBAYTAlVT\n"
    "MRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3dy5kaWdpY2VydC5j\n"
    "b20xIDAeBgNVBAMTF0RpZ2lDZXJ0IEdsb2JhbCBSb290IEcyMIIBIjANBgkqhkiG\n"
    "9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuzfNNNx7a8myaJCtSnX/RrohCgiN9RlUyfuI\n"
    "2/Ou8jqJkTx65qsGGmvPrC3oXgkkRLpimn7Wo6h+4FR1IAWsULecYxpsMNzaHxmx\n"
    "1x7e/dfgy5SDN67sH0NO3Xss0r0upS/kqbitOtSZpLYl6ZtrAGCSYP9PIUkY92eQ\n"
    "q2EGnI/yuum06ZIya7XzV+hdG82MHauVBJVJ8zUtluNJbd134/tJS7SsVQepj5Wz\n"
    "tCO7TG1F8PapspUwtP1MVYwnSlcUfIKdzXOS0xZKBgyMUNGPHgm+F6HmIcr9g+UQ\n"
    "vIOlCsRnKPZzFBQ9RnbDhxSJITRNrw9FDKZJobq7nMWxM4MphQIDAQABo0IwQDAP\n"
    "BgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBhjAdBgNVHQ4EFgQUTiJUIBiV\n"
    "5uNu5g/6+rkS7QYXjzkwDQYJKoZIhvcNAQELBQADggEBAGBnKJRvDkhj6zHd6mcY\n"
    "1Yl9PMWLSn/pvtsrF9+wX3N3KjITOYFnQoQj8kVnNeyIv/iPsGEMNKSuIEyExtv4\n"
    "NeF22d+mQrvHRAiGfzZ0JFrabA0UWTW98kndth/Jsw1HKj2ZL7tcu7XUIOGZX1NG\n"
    "Fdtom/DzMNU+MeKNhJ7jitralj41E6Vf8PlwUHBHQRFXGU7Aj64GxJUTFy8bJZ91\n"
    "8rGOmaFvE7FBcf6IKshPECBV1/MUReXgRPTqh5Uykw7+U0b6LJ3/iyK5S9kJRaTe\n"
    "pLiaWN0bfVKfjllDiIGknibVb63dDcY3fe0Dkhvld1927jyNxF1WW6LZZm6zNTfl\n"
    "MrY=\n"
    "-----END CERTIFICATE-----\n";

// ------------------------- Globals -------------------------
WiFiClientSecure netClient;
PubSubClient     mqtt(netClient);

// Status relay saat ini (true = LED nyala).
bool     relayOn          = false;
uint32_t telemetryIntervalMs = DEFAULT_TELEMETRY_INTERVAL_MS;
uint32_t lastTelemetryMs   = 0;
bool     stateChanged      = false;  // paksa publish saat status berubah

// -----------------------------------------------------------------------------
// Kontrol relay fisik.
// Banyak modul relay bersifat AKTIF-LOW: IN=LOW menyalakan relay (kontak COM-NO
// tersambung). Nilai level disesuaikan lewat RELAY_ACTIVE_HIGH di config.h.
// -----------------------------------------------------------------------------
void applyRelay(bool on) {
  relayOn = on;
#if RELAY_ACTIVE_HIGH
  digitalWrite(RELAY_PIN, on ? HIGH : LOW);
#else
  digitalWrite(RELAY_PIN, on ? LOW : HIGH);
#endif
  Serial.printf("Relay -> %s\n", on ? "ON (LED nyala)" : "OFF (LED mati)");
  stateChanged = true;
}

// -----------------------------------------------------------------------------
// WiFi
// -----------------------------------------------------------------------------
void connectWiFi() {
  WiFi.persistent(false);
  WiFi.disconnect(true, true);
  delay(200);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);

  Serial.printf("Menyambung ke SSID: '%s'\n", WIFI_SSID);
  while (WiFi.status() != WL_CONNECTED) {
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 10000) {
      delay(500);
      Serial.print(".");
    }
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("\nWiFi gagal, coba lagi (pastikan 2.4GHz, SSID & password benar).");
    }
  }
  Serial.printf("\nWiFi connected: %s\n", WiFi.localIP().toString().c_str());
}

// -----------------------------------------------------------------------------
// Waktu (NTP) — diperlukan untuk expiry SAS token & TLS
// -----------------------------------------------------------------------------
void syncTime() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  Serial.print("Sync time");
  time_t now = time(nullptr);
  while (now < 1700000000) {
    delay(300);
    Serial.print(".");
    now = time(nullptr);
  }
  Serial.printf("\nTime synced: %ld\n", (long)now);
}

// -----------------------------------------------------------------------------
// Direct Method: kirim response ke IoT Hub
// -----------------------------------------------------------------------------
void sendMethodResponse(const String &rid, int status, const String &payload) {
  String topic = "$iothub/methods/res/" + String(status) + "/?$rid=" + rid;
  mqtt.publish(topic.c_str(), payload.c_str());
}

// -----------------------------------------------------------------------------
// Direct Method: parsing & eksekusi perintah dari cloud
// -----------------------------------------------------------------------------
void handleDirectMethod(const String &methodName, const String &rid,
                        const String &body) {
  StaticJsonDocument<192> doc;
  DeserializationError err = deserializeJson(doc, body);

  if (methodName == "setRelay") {
    if (err) { sendMethodResponse(rid, 400, "{\"error\":\"bad json\"}"); return; }
    // Terima dua bentuk payload: {"state":"on|off"} atau {"on":true|false}.
    bool target = relayOn;
    if (doc.containsKey("on")) {
      target = doc["on"].as<bool>();
    } else {
      const char *state = doc["state"] | "";
      if      (strcmp(state, "on")  == 0) target = true;
      else if (strcmp(state, "off") == 0) target = false;
      else { sendMethodResponse(rid, 400, "{\"error\":\"state harus on|off\"}"); return; }
    }
    applyRelay(target);
    sendMethodResponse(rid, 200,
        String("{\"result\":\"ok\",\"relay\":\"") + (relayOn ? "on" : "off") + "\"}");

  } else if (methodName == "toggleRelay") {
    applyRelay(!relayOn);
    sendMethodResponse(rid, 200,
        String("{\"result\":\"ok\",\"relay\":\"") + (relayOn ? "on" : "off") + "\"}");

  } else if (methodName == "getState") {
    sendMethodResponse(rid, 200,
        String("{\"relay\":\"") + (relayOn ? "on" : "off") + "\"}");

  } else if (methodName == "reboot") {
    sendMethodResponse(rid, 200, "{\"result\":\"rebooting\"}");
    delay(500);
    ESP.restart();

  } else {
    sendMethodResponse(rid, 404, "{\"error\":\"unknown method\"}");
  }
}

// -----------------------------------------------------------------------------
// Callback MQTT: dipanggil saat menerima pesan (Direct Method)
// -----------------------------------------------------------------------------
void mqttCallback(char *topic, byte *payload, unsigned int length) {
  String t(topic);
  String body;
  body.reserve(length);
  for (unsigned int i = 0; i < length; i++) body += (char)payload[i];

  Serial.printf("MQTT in [%s]: %s\n", t.c_str(), body.c_str());

  // Topic Direct Method: $iothub/methods/POST/<methodName>/?$rid=<rid>
  if (t.startsWith("$iothub/methods/POST/")) {
    int nameStart = strlen("$iothub/methods/POST/");
    int nameEnd   = t.indexOf('/', nameStart);
    String methodName = t.substring(nameStart, nameEnd);

    int ridIdx = t.indexOf("$rid=");
    String rid = ridIdx >= 0 ? t.substring(ridIdx + 5) : "0";

    handleDirectMethod(methodName, rid, body);
  }
}

// -----------------------------------------------------------------------------
// MQTT: konek ke IoT Hub memakai SAS token sebagai password
// -----------------------------------------------------------------------------
void connectMqtt() {
  netClient.setCACert(AZURE_ROOT_CA);
  mqtt.setServer(IOT_HUB_HOST, 8883);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(1024);

  while (!mqtt.connected()) {
    uint32_t expiry = (uint32_t)time(nullptr) + SAS_TOKEN_TTL_SECONDS;
    String password = generateSasToken(IOT_HUB_HOST, IOT_DEVICE_ID,
                                        IOT_DEVICE_KEY, expiry);
    String username = String(IOT_HUB_HOST) + "/" + IOT_DEVICE_ID +
                      "/?api-version=2021-04-12";

    Serial.println("MQTT connecting to IoT Hub...");
    if (mqtt.connect(IOT_DEVICE_ID, username.c_str(), password.c_str())) {
      Serial.println("MQTT connected.");
      mqtt.subscribe(MQTT_METHOD_SUB_TOPIC);
    } else {
      Serial.printf("MQTT failed rc=%d, retry in 3s\n", mqtt.state());
      delay(3000);
    }
  }
}

// -----------------------------------------------------------------------------
// Telemetry: kirim status relay
// -----------------------------------------------------------------------------
void publishTelemetry() {
  StaticJsonDocument<128> doc;
  doc["deviceId"] = IOT_DEVICE_ID;
  doc["relay"]    = relayOn ? "on" : "off";
  doc["ledOn"]    = relayOn;
  doc["uptimeSec"] = (uint32_t)(millis() / 1000);

  char buf[128];
  size_t n = serializeJson(doc, buf);
  if (mqtt.publish(MQTT_TELEMETRY_TOPIC, buf, n)) {
    Serial.printf("TX: %s\n", buf);
  } else {
    Serial.println("Telemetry publish failed.");
  }
}

// -----------------------------------------------------------------------------
// setup
// -----------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(RELAY_PIN, OUTPUT);
  applyRelay(false);  // LED mati saat boot (kondisi aman)

  connectWiFi();
  syncTime();
  connectMqtt();
}

// -----------------------------------------------------------------------------
// loop
// -----------------------------------------------------------------------------
void loop() {
  if (!mqtt.connected()) {
    connectMqtt();
  }
  mqtt.loop();  // proses Direct Method masuk

  uint32_t now = millis();
  // Publish berkala (heartbeat) atau langsung saat status relay berubah.
  if (stateChanged || now - lastTelemetryMs >= telemetryIntervalMs) {
    lastTelemetryMs = now;
    stateChanged = false;
    publishTelemetry();
  }
}
