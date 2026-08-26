# =============================================================================
# provision-iot-hub.ps1  —  Provisioning Azure IoT Hub untuk demo Fabric
# -----------------------------------------------------------------------------
# Script ini:
#   1. Membuat resource group
#   2. Membuat IoT Hub (tier S1; ganti ke F1 untuk free/1 per subscription)
#   3. Membuat consumer group khusus untuk Fabric Eventstream
#   4. Mendaftarkan device 'm5fire-01'
#   5. Menampilkan connection string & primary key untuk diisi ke config.h
#
# Prasyarat:
#   - Azure CLI terinstall  (az --version)
#   - Ekstensi IoT           (az extension add --name azure-iot)
#   - Sudah login            (az login)
# Jalankan:
#   ./provision-iot-hub.ps1
# =============================================================================

# ------------------------- Parameter (sesuaikan) -------------------------
$Location        = "southeastasia"
$ResourceGroup   = "rg-demo-iot-fabric"
# Nama IoT Hub harus unik global. Tambahkan sufiks acak agar aman.
$Suffix          = -join ((48..57) + (97..122) | Get-Random -Count 5 | ForEach-Object { [char]$_ })
$IotHubName      = "iothub-demo-$Suffix"
$IotHubSku       = "S1"                 # gunakan "F1" untuk free tier (maks 1/subscription)
$DeviceId        = "m5fire-01"
$ConsumerGroup   = "fabric"             # consumer group untuk Eventstream

Write-Host "==> Membuat resource group '$ResourceGroup' di $Location..." -ForegroundColor Cyan
az group create --name $ResourceGroup --location $Location --output none

Write-Host "==> Membuat IoT Hub '$IotHubName' (SKU $IotHubSku)..." -ForegroundColor Cyan
az iot hub create `
    --name $IotHubName `
    --resource-group $ResourceGroup `
    --sku $IotHubSku `
    --location $Location `
    --output none

Write-Host "==> Membuat consumer group '$ConsumerGroup' untuk Fabric Eventstream..." -ForegroundColor Cyan
# Consumer group terpisah mencegah konflik offset dengan konsumer lain.
az iot hub consumer-group create `
    --hub-name $IotHubName `
    --resource-group $ResourceGroup `
    --name $ConsumerGroup `
    --output none

Write-Host "==> Mendaftarkan device '$DeviceId'..." -ForegroundColor Cyan
az iot hub device-identity create `
    --hub-name $IotHubName `
    --device-id $DeviceId `
    --output none

# ------------------------- Ambil kredensial untuk firmware -------------------------
$PrimaryKey = az iot hub device-identity connection-string show `
    --hub-name $IotHubName `
    --device-id $DeviceId `
    --key-type primary `
    --query connectionString -o tsv

$HubHost = "$IotHubName.azure-devices.net"

Write-Host ""
Write-Host "=========================================================" -ForegroundColor Green
Write-Host " Provisioning selesai. Isi nilai berikut ke config.h:" -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Green
Write-Host "  IOT_HUB_HOST   = `"$HubHost`""
Write-Host "  IOT_DEVICE_ID  = `"$DeviceId`""
Write-Host ""
Write-Host " Device connection string (mengandung SharedAccessKey):"
Write-Host "  $PrimaryKey"
Write-Host ""
Write-Host " Ambil hanya bagian 'SharedAccessKey=' -> itulah IOT_DEVICE_KEY."
Write-Host ""
Write-Host " Untuk Fabric Eventstream, gunakan:"
Write-Host "  IoT Hub name    : $IotHubName"
Write-Host "  Consumer group  : $ConsumerGroup"
Write-Host "  Shared access policy: service (atau iothubowner)"
Write-Host "=========================================================" -ForegroundColor Green
