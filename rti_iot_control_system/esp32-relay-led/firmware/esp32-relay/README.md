# ESP32 Relay → LED — Firmware

Firmware ESP32 (bukan M5Stack) yang menerima perintah dari Azure IoT Hub via
**Direct Method** untuk menyalakan/mematikan LED melalui **modul relay**.

## Hardware & wiring

| ESP32 | Modul relay 1-channel |
|-------|-----------------------|
| GPIO26 (`RELAY_PIN`) | IN |
| 5V (VIN) | VCC |
| GND | GND |

LED (via kontak relay):

```
Sumber(+) ──▶ COM
NO ──▶ LED(+) ──▶ (resistor jika perlu) ──▶ LED(-) ──▶ Sumber(-)
```

> Sebagian besar modul relay bersifat **aktif-LOW** (`RELAY_ACTIVE_HIGH 0`).
> Jika relay berperilaku terbalik, ubah `RELAY_ACTIVE_HIGH` di `config.h`.
> Untuk uji cepat tanpa relay, sambungkan LED + resistor 220Ω langsung ke
> GPIO26→GND dan set `RELAY_ACTIVE_HIGH 1`.

## Library Arduino

- `PubSubClient` (Nick O'Leary)
- `ArduinoJson` (Benoit Blanchon, v6+)

Board: **ESP32 Dev Module** (paket board Espressif ESP32).

## Konfigurasi

```powershell
Copy-Item config.example.h config.h
```

Isi `config.h`: SSID/password WiFi (2.4 GHz), host IoT Hub, device ID, primary key.
Daftarkan device di IoT Hub (mis. `esp32-relay-01`) — lihat README utama folder.

## Direct Method yang didukung

| Method | Payload | Aksi |
|--------|---------|------|
| `setRelay` | `{"state":"on"}` / `{"state":"off"}` / `{"on":true}` | Set LED nyala/mati |
| `toggleRelay` | `{}` | Balik status LED |
| `getState` | `{}` | Balikan status relay saat ini |
| `reboot` | `{}` | Restart device |

## Telemetry

Device mengirim status relay ke IoT Hub (heartbeat + saat berubah):

```json
{ "deviceId": "esp32-relay-01", "relay": "on", "ledOn": true, "uptimeSec": 123 }
```

Field ini bisa langsung dipakai di Fabric Real-Time Dashboard untuk memantau
status LED.
