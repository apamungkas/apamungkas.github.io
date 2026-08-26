// =============================================================================
// m5stack-fire.ino  —  Demo IoT: M5Stack Fire <-> Azure IoT Hub <-> Fabric
// -----------------------------------------------------------------------------
// Fitur:
//   * Baca sensor: ENV IV (suhu, kelembapan, tekanan), IMU (getaran), mic (bising)
//   * Kirim telemetry JSON ke Azure IoT Hub via MQTT/TLS (device-to-cloud)
//   * Terima perintah dari cloud via Direct Method (cloud-to-device):
//       - setAlert     : {"ledColor":"red|yellow|green|off","buzzer":true,"message":"..."}
//       - setInterval  : {"seconds": 5}
//       - reboot       : {}
//   * Umpan balik fisik: LCD, RGB LED bar, buzzer
//
// Library yang dibutuhkan (Arduino Library Manager):
//   - M5Unified
//   - M5UnitENV        (untuk sensor ENV III/IV)
//   - PubSubClient     (Nick O'Leary)
//   - ArduinoJson      (Benoit Blanchon, v6+)
// Board: "M5Stack-Fire" (ESP32) via M5Stack board package.
// =============================================================================

#include <M5Unified.h>
#include <M5UnitENV.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>

#include "config.h"
#include "sas_token.h"

// ------------------------- Azure IoT Hub MQTT topics -------------------------
// Telemetry (publish):
//   devices/<deviceId>/messages/events/
// Direct method (subscribe):
//   $iothub/methods/POST/#
// Direct method response (publish):
//   $iothub/methods/res/<status>/?$rid=<rid>
static const char *MQTT_TELEMETRY_TOPIC =
    "devices/" IOT_DEVICE_ID "/messages/events/";
static const char *MQTT_METHOD_SUB_TOPIC = "$iothub/methods/POST/#";

// ------------------------- Root CA (DigiCert Global Root G2) -------------------------
// Azure IoT Hub saat ini memakai rantai sertifikat DigiCert Global Root G2.
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
SHT3X   sht3;   // sensor suhu & kelembapan (ENV II/III, chip SHT30)
BMP280  bmp;    // sensor tekanan (ENV II/IV)

WiFiClientSecure netClient;
PubSubClient     mqtt(netClient);

uint32_t telemetryIntervalMs = DEFAULT_TELEMETRY_INTERVAL_MS;
uint32_t lastTelemetryMs     = 0;

// Baseline getaran (dihitung saat startup, perangkat diam).
float vibrationBaseline = 1.0f;

// Status alert terkini (dipengaruhi sensor lokal & Direct Method).
String  currentStatus  = "normal";
String  alertMessage   = "System OK";

// Alert yang tertunda dari Direct Method; dieksekusi di loop() (bukan di callback
// MQTT) agar operasi I2S speaker tidak bentrok dengan mic dan tidak memutus MQTT.
volatile bool pendingAlert = false;
String  pendingColor   = "off";
bool    pendingBuzzer  = false;
String  pendingMessage = "";

// -----------------------------------------------------------------------------
// Util: LED bar & buzzer
// -----------------------------------------------------------------------------
void setLed(const String &color) {
  // M5Stack Fire memiliki 10 LED RGB (SK6812) di sisi kiri-kanan.
  uint32_t c = 0x000000;
  if      (color == "red")    c = 0xFF0000;
  else if (color == "yellow") c = 0xFFAA00;
  else if (color == "green")  c = 0x00FF00;
  else if (color == "blue")   c = 0x0000FF;
  else                        c = 0x000000;  // off

  M5.Display.fillRect(0, 220, 320, 20,
    color == "red"    ? RED :
    color == "yellow" ? ORANGE :
    color == "green"  ? GREEN :
    color == "blue"   ? BLUE : BLACK);
  // Catatan: kontrol strip LED fisik butuh library FastLED/Adafruit_NeoPixel
  // pada pin 15. Untuk demo ini status warna ditampilkan pada bar layar.
  (void)c;
}

void beep(bool on) {
  if (on) {
    M5.Speaker.tone(2000, 300);  // 2 kHz, 300 ms
  }
}

// -----------------------------------------------------------------------------
// Util: gambar status di LCD
// -----------------------------------------------------------------------------
void drawStatus(float temp, float hum, float pres, float vib, int noise) {
  M5.Display.fillScreen(BLACK);
  M5.Display.setTextColor(WHITE, BLACK);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(10, 10);
  M5.Display.printf("Device: %s", IOT_DEVICE_ID);

  uint16_t stColor = currentStatus == "critical" ? RED :
                     currentStatus == "warning"  ? ORANGE : GREEN;
  M5.Display.setTextColor(stColor, BLACK);
  M5.Display.setCursor(10, 40);
  M5.Display.printf("Status: %s", currentStatus.c_str());

  M5.Display.setTextColor(WHITE, BLACK);
  M5.Display.setCursor(10, 75);
  M5.Display.printf("Temp : %.1f C", temp);
  M5.Display.setCursor(10, 100);
  M5.Display.printf("Hum  : %.1f %%", hum);
  M5.Display.setCursor(10, 125);
  M5.Display.printf("Pres : %.0f hPa", pres);
  M5.Display.setCursor(10, 150);
  M5.Display.printf("Vibr : %.2f", vib);
  M5.Display.setCursor(10, 175);
  M5.Display.printf("Noise: %d", noise);

  M5.Display.setTextSize(1);
  M5.Display.setCursor(10, 205);
  M5.Display.printf("%s", alertMessage.c_str());
}

// -----------------------------------------------------------------------------
// Sensor: baca getaran dari IMU (deviasi magnitudo akselerasi dari baseline)
// -----------------------------------------------------------------------------
float readVibration() {
  float ax, ay, az;
  M5.Imu.getAccel(&ax, &ay, &az);
  float mag = sqrtf(ax * ax + ay * ay + az * az);
  return fabsf(mag - vibrationBaseline);
}

// -----------------------------------------------------------------------------
// Sensor: level kebisingan.
// Mic DINONAKTIFKAN: mic & speaker berbagi I2S; menukar keduanya berulang
// (rekam mic tiap siklus + buzzer) membuat ESP32 crash & MQTT putus. Untuk demo
// closed-loop, speaker/buzzer diprioritaskan. Kembalikan 0 sebagai placeholder.
// -----------------------------------------------------------------------------
int readNoise() {
  return 0;
}

// -----------------------------------------------------------------------------
// WiFi
// -----------------------------------------------------------------------------
// Terjemahkan kode status WiFi ke teks agar mudah didiagnosa di Serial Monitor.
const char *wifiStatusText(wl_status_t s) {
  switch (s) {
    case WL_IDLE_STATUS:     return "IDLE";
    case WL_NO_SSID_AVAIL:   return "NO_SSID_AVAIL (SSID tak ditemukan / salah / bukan 2.4GHz)";
    case WL_SCAN_COMPLETED:  return "SCAN_COMPLETED";
    case WL_CONNECTED:       return "CONNECTED";
    case WL_CONNECT_FAILED:  return "CONNECT_FAILED (password salah?)";
    case WL_CONNECTION_LOST: return "CONNECTION_LOST";
    case WL_DISCONNECTED:    return "DISCONNECTED";
    default:                 return "UNKNOWN";
  }
}

void connectWiFi() {
  M5.Display.fillScreen(BLACK);
  M5.Display.setCursor(10, 10);
  M5.Display.print("WiFi connecting...");

  // Reset stack WiFi & matikan power save (fix umum ESP32 gagal konek).
  WiFi.persistent(false);
  WiFi.disconnect(true, true);
  delay(200);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);

  Serial.printf("Menyambung ke SSID: '%s'\n", WIFI_SSID);

  while (true) {
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    // Beri waktu ~10 detik per percobaan sambil cetak status.
    uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 10000) {
      delay(500);
      Serial.printf("  status=%d (%s)\n",
                    WiFi.status(), wifiStatusText((wl_status_t)WiFi.status()));
      M5.Display.print(".");
    }

    if (WiFi.status() == WL_CONNECTED) break;

    // Gagal: tampilkan diagnosa & scan SSID yang terlihat, lalu ulangi.
    Serial.println("WiFi gagal konek. Scan jaringan 2.4GHz yang terlihat:");
    int n = WiFi.scanNetworks();
    for (int i = 0; i < n; i++) {
      Serial.printf("  [%d] %s  (RSSI %d)\n", i, WiFi.SSID(i).c_str(), WiFi.RSSI(i));
    }
    Serial.println("Cek: SSID benar? Jaringan 2.4GHz? Password benar? Coba lagi...");
    M5.Display.print("!");
    delay(1500);
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
  while (now < 1700000000) {  // tunggu hingga epoch valid
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
  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, body);

  if (methodName == "setAlert") {
    if (err) { sendMethodResponse(rid, 400, "{\"error\":\"bad json\"}"); return; }
    // Simpan permintaan; eksekusi hardware ditunda ke loop() (hindari bentrok I2S).
    pendingColor   = doc["ledColor"] | "off";
    pendingBuzzer  = doc["buzzer"]   | false;
    pendingMessage = doc["message"]  | "";
    pendingAlert   = true;
    sendMethodResponse(rid, 200, "{\"result\":\"alert applied\"}");

  } else if (methodName == "setInterval") {
    if (err) { sendMethodResponse(rid, 400, "{\"error\":\"bad json\"}"); return; }
    int sec = doc["seconds"] | 2;
    telemetryIntervalMs = (uint32_t)constrain(sec, 1, 60) * 1000UL;
    sendMethodResponse(rid, 200, "{\"result\":\"interval updated\"}");

  } else if (methodName == "reboot") {
    sendMethodResponse(rid, 200, "{\"result\":\"rebooting\"}");
    delay(500);
    ESP.restart();

  } else {
    sendMethodResponse(rid, 404, "{\"error\":\"unknown method\"}");
  }
}

// -----------------------------------------------------------------------------
// Callback MQTT: dipanggil saat menerima pesan (Direct Method / C2D)
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
    // Username IoT Hub: <host>/<deviceId>/?api-version=2021-04-12
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
// Telemetry: rakit JSON & publish
// -----------------------------------------------------------------------------
void publishTelemetry(float temp, float hum, float pres, float vib, int noise) {
  // Hitung status lokal berdasar suhu.
  if      (temp >= TEMP_CRIT_C) { currentStatus = "critical"; }
  else if (temp >= TEMP_WARN_C) { currentStatus = "warning";  }
  else                          { currentStatus = "normal";   }

  StaticJsonDocument<256> doc;
  doc["deviceId"]    = IOT_DEVICE_ID;
  doc["temperature"] = round(temp * 10) / 10.0;
  doc["humidity"]    = round(hum * 10) / 10.0;
  doc["pressure"]    = round(pres);
  doc["vibration"]   = round(vib * 100) / 100.0;
  doc["noiseLevel"]  = noise;
  doc["status"]      = currentStatus;

  char buf[256];
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
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Display.setRotation(1);
  M5.Display.fillScreen(BLACK);
  M5.Display.setTextSize(2);
  Serial.begin(115200);

  // Inisialisasi sensor ENV II (SHT30 + BMP280) via port A (I2C).
  if (!sht3.begin(&Wire, SHT3X_I2C_ADDR, 21, 22, 400000U)) {
    Serial.println("SHT3x not found (cek koneksi ENV unit).");
  }
  if (!bmp.begin(&Wire, BMP280_I2C_ADDR, 21, 22, 400000U)) {
    Serial.println("BMP280 not found (cek koneksi ENV unit).");
  }

  // Kalibrasi baseline getaran (perangkat diasumsikan diam saat boot).
  M5.Imu.begin();
  float ax, ay, az;
  M5.Imu.getAccel(&ax, &ay, &az);
  vibrationBaseline = sqrtf(ax * ax + ay * ay + az * az);

  connectWiFi();
  syncTime();
  connectMqtt();
}

// -----------------------------------------------------------------------------
// loop
// -----------------------------------------------------------------------------
void loop() {
  M5.update();

  if (!mqtt.connected()) {
    connectMqtt();
  }
  mqtt.loop();  // proses pesan masuk (Direct Method)

  uint32_t now = millis();
  if (now - lastTelemetryMs >= telemetryIntervalMs) {
    lastTelemetryMs = now;

    float temp = 0, hum = 0, pres = 0;
    if (sht3.update()) { temp = sht3.cTemp; hum = sht3.humidity; }
    if (bmp.update())  { pres = bmp.pressure / 100.0f; }  // Pa -> hPa
    float vib   = readVibration();
    int   noise = readNoise();

    publishTelemetry(temp, hum, pres, vib, noise);
    drawStatus(temp, hum, pres, vib, noise);
  }

  // Terapkan alert dari Direct Method di luar callback MQTT (aman untuk I2S).
  if (pendingAlert) {
    pendingAlert = false;
    setLed(pendingColor);
    beep(pendingBuzzer);
    if (pendingMessage.length()) alertMessage = pendingMessage;
    currentStatus = (pendingColor == "red")    ? "critical" :
                    (pendingColor == "yellow") ? "warning"  : "normal";
  }

  // Tombol A: paksa kirim alert test ke cloud dilakukan dari sisi cloud;
  // di sini tombol A hanya membisukan buzzer lokal untuk demo.
  if (M5.BtnA.wasPressed()) {
    alertMessage = "Acknowledged (local)";
    currentStatus = "normal";
    setLed("green");
  }
}
