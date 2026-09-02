# relay-control — Azure Function

HTTP-trigger Function yang memanggil Direct Method `setRelay` / `toggleRelay`
pada device ESP32 relay via Azure IoT Hub. Bisa dipanggil dari halaman web,
Power Automate, atau Fabric Data Activator.

## Setup lokal

```powershell
Copy-Item local.settings.json.example local.settings.json
npm install
npm start
```

Isi `IOTHUB_CONNECTION_STRING` dengan **service** connection string IoT Hub
(policy `service`), dan `DEFAULT_DEVICE_ID` dengan device ID relay Anda.

## Endpoint

`POST /api/relay`  (authLevel: `function` → butuh function key)

Body:

```json
{ "deviceId": "esp32-relay-01", "state": "on" }
```

`state` menerima `on`, `off`, atau `toggle`. Kompatibel juga dengan `{"on": true}`.

Contoh:

```powershell
$body = @{ state = "on" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://localhost:7071/api/relay" `
  -ContentType "application/json" -Body $body
```

Nyalakan / matikan:

```powershell
# ON
Invoke-RestMethod -Method Post -Uri "http://localhost:7071/api/relay" -ContentType "application/json" -Body (@{ state="on"  } | ConvertTo-Json)
# OFF
Invoke-RestMethod -Method Post -Uri "http://localhost:7071/api/relay" -ContentType "application/json" -Body (@{ state="off" } | ConvertTo-Json)
```

## Respons

```json
{
  "ok": true,
  "deviceId": "esp32-relay-01",
  "requestedState": "on",
  "deviceStatus": 200,
  "deviceResponse": { "result": "ok", "relay": "on" }
}
```

Jika device offline: status `502` dengan pesan `DeviceNotOnline`.

## Deploy ke Azure

```powershell
func azure functionapp publish <NAMA_FUNCTION_APP>
```

Set app settings `IOTHUB_CONNECTION_STRING` dan `DEFAULT_DEVICE_ID` di Function App.
Untuk produksi: gunakan Managed Identity + Key Vault, bukan connection string.
