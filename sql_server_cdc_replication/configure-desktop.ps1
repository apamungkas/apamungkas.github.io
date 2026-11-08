$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$bastionPrefix = '__BASTION_PREFIX__'

function Find-Ssms {
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)})
    $candidates = foreach ($root in $roots) {
        foreach ($version in 22, 21, 20, 19, 18) {
            Join-Path $root "Microsoft SQL Server Management Studio $version\Release\Common7\IDE\Ssms.exe"
            Join-Path $root "Microsoft SQL Server Management Studio $version\Common7\IDE\Ssms.exe"
        }
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

$ssms = Find-Ssms
if (-not $ssms) {
    $installer = Join-Path $env:TEMP 'sqlsync-vs-SSMS.exe'
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri 'https://aka.ms/ssms/22/release/vs_SSMS.exe' -OutFile $installer -UseBasicParsing
        $signature = Get-AuthenticodeSignature -FilePath $installer
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
            throw 'SSMS installer does not have a valid Microsoft signature.'
        }
        $process = Start-Process -FilePath $installer -ArgumentList '--quiet', '--wait', '--norestart', '--nocache' -Wait -PassThru
        if ($process.ExitCode -eq 3010) {
            throw 'SSMS installation requires a VM reboot. Schedule it outside the demo, then rerun this action; no automatic reboot was performed.'
        }
        if ($process.ExitCode -ne 0) { throw "SSMS installation failed with exit code $($process.ExitCode)." }
    }
    finally {
        if (Test-Path -LiteralPath $installer) { Remove-Item -LiteralPath $installer -Force }
    }
    $ssms = Find-Ssms
    if (-not $ssms) { throw 'SSMS executable was not found after installation.' }
}

$connection = New-Object System.Data.SqlClient.SqlConnection 'Server=localhost;Database=master;Integrated Security=true;Encrypt=true;TrustServerCertificate=true'
try {
    $connection.Open()
    $command = $connection.CreateCommand()
    $command.CommandText = 'SELECT ORIGINAL_LOGIN() AS LoginName, SERVERPROPERTY(''ServerName'') AS ServerName;'
    $reader = $command.ExecuteReader()
    if (-not $reader.Read()) { throw 'Local Windows-authenticated SQL inspection returned no identity.' }
    $login = [string]$reader['LoginName']
    $server = [string]$reader['ServerName']
    $reader.Close()
}
finally {
    $connection.Dispose()
}

# Disable the image's broad RDP rules; only the Bastion subnet may reach the guest.
Get-NetFirewallRule -Name 'RemoteDesktop*' -ErrorAction Stop | Disable-NetFirewallRule
$rule = Get-NetFirewallRule -Name 'SQLSync-Bastion-RDP' -ErrorAction SilentlyContinue
if ($rule) { Remove-NetFirewallRule -Name 'SQLSync-Bastion-RDP' }
New-NetFirewallRule -Name 'SQLSync-Bastion-RDP' -DisplayName 'SQL Sync Bastion RDP' `
    -Direction Inbound -Protocol TCP -LocalPort 3389 -RemoteAddress $bastionPrefix `
    -Action Allow -Profile Any | Out-Null
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -Name UserAuthentication -Value 1
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -Value 0
Start-Service TermService

$demoPath = 'C:\SQLSyncDemo'
New-Item -ItemType Directory -Path $demoPath -Force | Out-Null
$query = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__INSPECT_BASE64__'))
Set-Content -LiteralPath "$demoPath\inspect.sql" -Value $query -Encoding UTF8
$desktop = [Environment]::GetFolderPath('CommonDesktopDirectory')
Copy-Item -LiteralPath "$demoPath\inspect.sql" -Destination (Join-Path $desktop 'SQL Sync - Inspect.sql') -Force
$shell = New-Object -ComObject WScript.Shell
try {
    $shortcut = $shell.CreateShortcut((Join-Path $desktop 'SQL Server Management Studio.lnk'))
    $shortcut.TargetPath = $ssms
    $shortcut.WorkingDirectory = Split-Path $ssms
    $shortcut.Save()
}
finally {
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
}

$listener = Get-NetTCPConnection -LocalPort 3389 -State Listen -ErrorAction Stop
if (-not $listener) { throw 'Remote Desktop is not listening on TCP 3389.' }
[pscustomobject]@{
    SsmsPath = $ssms
    SsmsVersion = (Get-Item -LiteralPath $ssms).VersionInfo.ProductVersion
    SqlLogin = $login
    SqlServer = $server
    RdpAllowedSource = $bastionPrefix
    NlaRequired = $true
    InspectionFile = "$demoPath\inspect.sql"
} | ConvertTo-Json -Compress | Write-Output
