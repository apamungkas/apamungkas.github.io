// =============================================================================
// sas_token.h  —  Generator Azure IoT Hub SAS token (HMAC-SHA256)
// -----------------------------------------------------------------------------
// SAS token dipakai sebagai password MQTT saat konek ke IoT Hub.
// Format: SharedAccessSignature sr=<res>&sig=<sig>&se=<expiry>
// signature = base64( HMAC-SHA256( key = base64decode(deviceKey),
//                                 msg = <urlEncodedResourceUri> + "\n" + <expiry> ) )
// =============================================================================
#pragma once

#include <Arduino.h>
#include "mbedtls/md.h"
#include "mbedtls/base64.h"

// URL-encode minimal untuk karakter yang muncul pada resource URI/signature.
static String urlEncode(const String &src) {
  String out;
  const char *hex = "0123456789ABCDEF";
  for (size_t i = 0; i < src.length(); i++) {
    char c = src.charAt(i);
    if (isalnum((unsigned char)c) || c == '-' || c == '_' || c == '.' || c == '~') {
      out += c;
    } else {
      out += '%';
      out += hex[(c >> 4) & 0xF];
      out += hex[c & 0xF];
    }
  }
  return out;
}

// Menghasilkan SAS token siap-pakai sebagai password MQTT.
//   host        : "myhub.azure-devices.net"
//   deviceId    : "esp32-relay-01"
//   deviceKeyB64: primary key (base64) dari IoT Hub
//   expiryEpoch : waktu kedaluwarsa (detik epoch UTC)
static String generateSasToken(const String &host,
                               const String &deviceId,
                               const String &deviceKeyB64,
                               uint32_t expiryEpoch) {
  // Resource URI: <host>/devices/<deviceId>
  String resourceUri = host + "/devices/" + deviceId;
  String encodedUri  = urlEncode(resourceUri);

  // String yang ditandatangani: <encodedUri>\n<expiry>
  String toSign = encodedUri + "\n" + String(expiryEpoch);

  // Decode device key (base64) -> raw bytes.
  uint8_t key[64];
  size_t  keyLen = 0;
  if (mbedtls_base64_decode(key, sizeof(key), &keyLen,
                            (const uint8_t *)deviceKeyB64.c_str(),
                            deviceKeyB64.length()) != 0) {
    return String("");  // key tidak valid
  }

  // HMAC-SHA256(key, toSign).
  uint8_t hmac[32];
  const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, info, 1 /* HMAC */);
  mbedtls_md_hmac_starts(&ctx, key, keyLen);
  mbedtls_md_hmac_update(&ctx, (const uint8_t *)toSign.c_str(), toSign.length());
  mbedtls_md_hmac_finish(&ctx, hmac);
  mbedtls_md_free(&ctx);

  // Base64 encode signature.
  uint8_t sigB64[64];
  size_t  sigB64Len = 0;
  mbedtls_base64_encode(sigB64, sizeof(sigB64), &sigB64Len, hmac, sizeof(hmac));
  String signature((char *)sigB64, sigB64Len);

  // Rakit token.
  String token = "SharedAccessSignature sr=" + encodedUri +
                 "&sig=" + urlEncode(signature) +
                 "&se="  + String(expiryEpoch);
  return token;
}
