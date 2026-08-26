// =============================================================================
// alertDevice  —  HTTP trigger: Fabric Activator -> IoT Hub Direct Method
// -----------------------------------------------------------------------------
// Menerima POST (dari Power Automate yang dipicu Data Activator), lalu memanggil
// Direct Method "setAlert" pada device M5Stack Fire via Azure IoT Hub.
//
// Body JSON (semua opsional, ada default):
//   {
//     "deviceId": "m5fire-01",
//     "ledColor": "red",          // red | yellow | green | off
//     "buzzer":   true,
//     "message":  "OVERHEAT (cloud)"
//   }
// =============================================================================
const { app } = require('@azure/functions');
const { Client } = require('azure-iothub');

app.http('alertDevice', {
  methods: ['POST'],
  authLevel: 'function', // butuh function key; lihat README untuk cara ambil URL+key
  handler: async (request, context) => {
    const connStr = process.env.IOTHUB_CONNECTION_STRING;
    if (!connStr) {
      return { status: 500, jsonBody: { ok: false, error: 'IOTHUB_CONNECTION_STRING belum di-set' } };
    }

    // Body boleh kosong; pakai default bila field tidak dikirim.
    const body = await request.json().catch(() => ({}));
    const deviceId = body.deviceId || process.env.DEFAULT_DEVICE_ID || 'm5fire-01';
    const methodParams = {
      methodName: 'setAlert',
      payload: {
        ledColor: body.ledColor || 'red',
        buzzer: body.buzzer !== undefined ? body.buzzer : true,
        message: body.message || 'OVERHEAT (cloud)',
      },
      responseTimeoutInSeconds: 30,
      connectTimeoutInSeconds: 10,
    };

    context.log(`Invoking setAlert on ${deviceId}:`, methodParams.payload);

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
