// =============================================================================
// config.example.h  —  Template konfigurasi. SALIN menjadi "config.h" lalu isi.
//   Copy-Item config.example.h config.h
// File config.h asli DIABAIKAN git (berisi kredensial). Jangan commit kredensial.
// =============================================================================
#pragma once

// ------------------------- WiFi (2.4 GHz) -------------------------
#define WIFI_SSID       "YOUR_WIFI_SSID"
#define WIFI_PASSWORD   "YOUR_WIFI_PASSWORD"

// ------------------------- Azure IoT Hub -------------------------
#define IOT_HUB_HOST    "YOUR_IOT_HUB.azure-devices.net"
#define IOT_DEVICE_ID   "m5fire-01"
#define IOT_DEVICE_KEY  "YOUR_DEVICE_PRIMARY_KEY_BASE64"

// Masa berlaku SAS token dalam detik (default 1 jam).
#define SAS_TOKEN_TTL_SECONDS   3600

// ------------------------- Telemetry -------------------------
#define DEFAULT_TELEMETRY_INTERVAL_MS   2000

// ------------------------- Ambang batas (status lokal) -------------------------
#define TEMP_WARN_C     35.0f
#define TEMP_CRIT_C     40.0f
