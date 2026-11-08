$ErrorActionPreference = 'Stop'
$instancePath = 'HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\MSSQL16.MSSQLSERVER\MSSQLServer'
$networkPath = "$instancePath\SuperSocketNetLib"
Set-ItemProperty $instancePath -Name LoginMode -Value 2
Set-ItemProperty "$networkPath\Tcp" -Name Enabled -Value 1
Set-ItemProperty "$networkPath\Tcp\IPAll" -Name TcpPort -Value '1433'
Set-ItemProperty "$networkPath\Tcp\IPAll" -Name TcpDynamicPorts -Value ''
Set-ItemProperty $networkPath -Name ForceEncryption -Value 1
if (-not (Get-NetFirewallRule -DisplayName 'SQL Fabric CDC demo' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName 'SQL Fabric CDC demo' -Direction Inbound -Protocol TCP `
        -LocalPort 1433 -RemoteAddress '10.74.30.32/27' -Action Allow | Out-Null
}
Set-Service SQLSERVERAGENT -StartupType Automatic
Restart-Service MSSQLSERVER -Force
Start-Service SQLSERVERAGENT
if ([string]::IsNullOrWhiteSpace($PipelinePassword)) { throw 'A protected pipeline password parameter is required.' }
$connection = New-Object System.Data.SqlClient.SqlConnection 'Server=localhost;Database=master;Integrated Security=true;Encrypt=true;TrustServerCertificate=true'
try {
    $connection.Open()
    $command = $connection.CreateCommand()
    $escaped = $PipelinePassword.Replace("'", "''")
    $command.CommandText = @"
IF SUSER_ID(N'fabric_cdc') IS NULL
    CREATE LOGIN [fabric_cdc] WITH PASSWORD=N'$escaped', CHECK_POLICY=ON;
EXEC sp_configure N'show advanced options', 1;
RECONFIGURE;
EXEC sp_configure N'max server memory (MB)', 4096;
RECONFIGURE;
SELECT SERVERPROPERTY('ServerName') AS ServerName, SERVERPROPERTY('Edition') AS Edition,
       SERVERPROPERTY('ProductVersion') AS ProductVersion;
"@
    $reader = $command.ExecuteReader()
    while ($reader.Read()) {
        Write-Output ("Server: " + $reader['ServerName'] + "; edition: " + $reader['Edition'] + "; version: " + $reader['ProductVersion'])
    }
    $reader.Close()
}
finally {
    $connection.Dispose()
    $escaped = $null
    $PipelinePassword = $null
}
