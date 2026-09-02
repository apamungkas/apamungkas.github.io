// =============================================================================
// controlRelay  —  HTTP trigger: kontrol LED (relay) ESP32 via IoT Hub
// -----------------------------------------------------------------------------
// Memanggil Direct Method "setRelay" (atau "toggleRelay") pada device ESP32
// relay via Azure IoT Hub. Bisa dipicu dari halaman web, Power Automate, atau
// Fabric Data Activator.
//
// Body JSON (semua opsional):
//   {
//     "deviceId": "esp32-relay-01",
//     "state":    "on"            // "on" | "off" | "toggle"
//   }
// Kompatibel juga dengan {"on": true|false}.
// =============================================================================
const { app } = require('@azure/functions');
const { Client } = require('azure-iothub');

// Tentukan state target ("on"|"off"|"toggle") dari body request.
function resolveState(body) {
  if (typeof body.on === 'boolean') return body.on ? 'on' : 'off';
  const s = (body.state || '').toString().toLowerCase();
  if (s === 'on' || s === 'off' || s === 'toggle') return s;
  return null;
}

app.http('controlRelay', {
  methods: ['POST'],
  authLevel: 'function', // butuh function key; lihat README untuk URL+key
  route: 'relay',
  handler: async (request, context) => {
    const connStr = process.env.IOTHUB_CONNECTION_STRING;
    if (!connStr) {
      return { status: 500, jsonBody: { ok: false, error: 'IOTHUB_CONNECTION_STRING belum di-set' } };
    }

    const body = await request.json().catch(() => ({}));
    const deviceId = body.deviceId || process.env.DEFAULT_DEVICE_ID || 'esp32-relay-01';
    const state = resolveState(body);
    if (!state) {
      return { status: 400, jsonBody: { ok: false, error: 'state harus on | off | toggle' } };
    }

    // toggle -> Direct Method toggleRelay; on/off -> setRelay {state}.
    const methodParams = state === 'toggle'
      ? { methodName: 'toggleRelay', payload: {} }
      : { methodName: 'setRelay', payload: { state } };
    methodParams.responseTimeoutInSeconds = 30;
    methodParams.connectTimeoutInSeconds = 10;

    context.log(`Invoking ${methodParams.methodName} on ${deviceId}:`, methodParams.payload);

    try {
      const client = Client.fromConnectionString(connStr);
      const response = await client.invokeDeviceMethod(deviceId, methodParams);
      const result = response.result || {};
      context.log(`Device responded status=${result.status}`, result.payload);

      return {
        status: 200,
        jsonBody: {
          ok: true,
          deviceId,
          requestedState: state,
          deviceStatus: result.status,
          deviceResponse: result.payload,
        },
      };
    } catch (err) {
      // Umum: device offline (404 DeviceNotOnline) atau connection string salah.
      context.log('invokeDeviceMethod gagal:', err.message);
      return {
        status: 502,
        jsonBody: { ok: false, deviceId, error: err.message },
      };
    }
  },
});
