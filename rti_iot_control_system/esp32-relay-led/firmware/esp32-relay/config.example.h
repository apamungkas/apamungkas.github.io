// =============================================================================
// config.example.h  —  Template konfigurasi ESP32 relay->LED.
//   SALIN menjadi "config.h" lalu isi nilai Anda:  Copy-Item config.example.h config.h
// File config.h asli JANGAN di-commit (berisi kredensial).
// =============================================================================
#pragma once

// ------------------------- WiFi (2.4 GHz) -------------------------
#define WIFI_SSID       "YOUR_WIFI_SSID"
#define WIFI_PASSWORD   "YOUR_WIFI_PASSWORD"

// ------------------------- Azure IoT Hub -------------------------
#define IOT_HUB_HOST    "YOUR_IOT_HUB.azure-devices.net"
#define IOT_DEVICE_ID   "esp32-relay-01"
#define IOT_DEVICE_KEY  "YOUR_DEVICE_PRIMARY_KEY_BASE64"

// Masa berlaku SAS token dalam detik (default 1 jam).
#define SAS_TOKEN_TTL_SECONDS   3600

// ------------------------- Relay / LED -------------------------
// Pin GPIO yang tersambung ke input (IN) modul relay.
#define RELAY_PIN         26

// Set 0 bila modul relay bertipe AKTIF-LOW (paling umum: IN=LOW -> relay ON).
// Set 1 bila modul AKTIF-HIGH (IN=HIGH -> relay ON), atau bila menyalakan LED
// langsung dari GPIO via resistor tanpa modul relay.
#define RELAY_ACTIVE_HIGH 0

// ------------------------- Telemetry -------------------------
// Interval heartbeat status relay (ms). Status juga dikirim seketika saat berubah.
#define DEFAULT_TELEMETRY_INTERVAL_MS   5000
