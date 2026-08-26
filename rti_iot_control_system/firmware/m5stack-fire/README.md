# Firmware M5Stack Fire — Demo IoT Fabric

Firmware untuk **M5Stack Fire (ESP32)** yang membaca sensor lingkungan, mengirim
telemetry ke **Azure IoT Hub** (device-to-cloud), dan menerima perintah dari
cloud melalui **Direct Method** (cloud-to-edge). Ini adalah komponen *edge* dari
demo Real-Time Intelligence Dashboard di Microsoft Fabric.

## Struktur file

| File | Fungsi |
|------|--------|
| [m5stack-fire.ino](m5stack-fire.ino) | Sketch utama: sensor, MQTT, Direct Method |
| [config.h](config.h) | Kredensial WiFi & Azure IoT Hub (isi sendiri) |
| [sas_token.h](sas_token.h) | Generator SAS token (HMAC-SHA256) untuk auth MQTT |

## Hardware

- **M5Stack Fire** (ESP32, LCD, IMU, mic, speaker, LED bar)
- **Unit ENV III atau ENV IV** (suhu, kelembapan, tekanan) via port A (Grove/I2C)
- Kabel Grove

> Jika Anda belum punya unit ENV, kode tetap jalan — nilai suhu/kelembapan/tekanan
> akan 0. Getaran (IMU) dan kebisingan (mic) tetap terbaca karena sensornya internal.

## Prasyarat software

1. **Arduino IDE** (atau PlatformIO)
2. **Board package M5Stack** — di Board Manager URL tambahkan:
   `https://static-cdn.m5stack.com/resource/arduino/package_m5stack_index.json`
   lalu install *M5Stack* dan pilih board **M5Stack-Fire**.
3. **Library** (Library Manager):
   - `M5Unified`
   - `M5UnitENV`
   - `PubSubClient` (Nick O'Leary)
   - `ArduinoJson` (v6+)

## Langkah setup

1. **Daftarkan device** di Azure IoT Hub:
   ```bash
   az iot hub device-identity create \
     --hub-name <NAMA_IOT_HUB> \
     --device-id m5fire-01
   ```
   Ambil primary key:
   ```bash
   az iot hub device-identity connection-string show \
     --hub-name <NAMA_IOT_HUB> --device-id m5fire-01
   ```

2. **Isi `config.h`** dengan SSID WiFi, host IoT Hub, device ID, dan primary key.

3. **Compile & upload** dari Arduino IDE ke board M5Stack-Fire.

4. Buka **Serial Monitor** (115200 baud) untuk melihat log koneksi & telemetry.

## Format telemetry (device → cloud)

```json
{
  "deviceId": "m5fire-01",
  "temperature": 42.5,
  "humidity": 55.2,
  "pressure": 1008,
  "vibration": 0.87,
  "noiseLevel": 72,
  "status": "warning"
}
```

## Direct Method (cloud → device)

Uji dari Azure CLI:

**Nyalakan alert merah + buzzer:** --> TESTED OK
```bash
az iot hub invoke-device-method \
  --hub-name iothub-demo-v640s --device-id m5fire-01 \
  --method-name setAlert \
  --method-payload '{"ledColor":"red","buzzer":true,"message":"OVERHEAT Zone A"}'
```

**Ubah interval telemetry jadi 5 detik:**
```bash
az iot hub invoke-device-method \
  --hub-name iothub-demo-v640s --device-id m5fire-01 \
  --method-name setInterval \
  --method-payload '{"seconds":5}'
```

**Reboot device:**
```bash
az iot hub invoke-device-method \
  --hub-name iothub-demo-v640s --device-id m5fire-01 \
  --method-name reboot --method-payload '{}'
```

| Method | Payload | Efek di device |
|--------|---------|----------------|
| `setAlert` | `{"ledColor","buzzer","message"}` | LED bar warna, buzzer, pesan di LCD |
| `setInterval` | `{"seconds"}` | Ubah interval kirim telemetry (1–60 s) |
| `reboot` | `{}` | Restart ESP32 |

## Monitoring (log koneksi & telemetry)

```bash
# Pantau telemetry masuk secara real-time
az iot hub monitor-events --hub-name iothub-demo-v640s --device-id m5fire-01

# Cek status online/offline device
az iot hub device-identity show --hub-name iothub-demo-v640s --device-id m5fire-01 \
  --query "{id:deviceId, connectionState:connectionState}" -o table
```

> Untuk log connect/disconnect & error autentikasi, aktifkan **Diagnostic Settings**
> (kategori *Connections*) di Portal dan kirim ke Log Analytics, lalu query dengan KQL.

## Catatan teknis

- **Auth**: firmware membuat SAS token per koneksi (HMAC-SHA256 via mbedtls),
  dipakai sebagai password MQTT. Token kedaluwarsa diatur di `SAS_TOKEN_TTL_SECONDS`.
- **TLS**: koneksi ke port 8883 memakai root CA *DigiCert Global Root G2*. Jika di
  masa depan Azure mengganti rantai sertifikat, perbarui `AZURE_ROOT_CA`.
- **Waktu**: NTP disinkron saat boot karena SAS token & TLS butuh waktu akurat.
- **LED bar fisik**: contoh ini menampilkan status warna pada bar di LCD. Untuk
  mengontrol strip SK6812 fisik (pin 15), tambahkan `FastLED`/`Adafruit_NeoPixel`.

## Berikutnya

Setelah firmware jalan, lanjut ke:
- Setup **Azure IoT Hub** + routing ke Fabric Eventstream
- Setup **Fabric** (Eventstream → Eventhouse → Real-Time Dashboard)
- **Azure Function** sebagai jembatan command dari Fabric Activator ke Direct Method
