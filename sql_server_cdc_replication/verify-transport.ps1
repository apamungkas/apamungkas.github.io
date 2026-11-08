$ErrorActionPreference = 'Stop'
$path = 'HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\MSSQL16.MSSQLSERVER\MSSQLServer\SuperSocketNetLib'
$forced = (Get-ItemProperty $path -Name ForceEncryption).ForceEncryption
if ($forced -ne 1) { throw 'SQL Server ForceEncryption is not enabled.' }
if ([string]::IsNullOrWhiteSpace($PipelinePassword)) { throw 'A protected pipeline password parameter is required.' }
$builder = New-Object System.Data.SqlClient.SqlConnectionStringBuilder
$builder['Data Source'] = 'tcp:127.0.0.1,1433'
$builder['Initial Catalog'] = '__DATABASE__'
$builder['User ID'] = 'fabric_cdc'
$builder['Password'] = $PipelinePassword
$builder['Encrypt'] = $false
$builder['TrustServerCertificate'] = $true
$client = New-Object System.Data.SqlClient.SqlConnection $builder.get_ConnectionString()
$admin = New-Object System.Data.SqlClient.SqlConnection 'Server=localhost;Database=master;Integrated Security=true;Encrypt=true;TrustServerCertificate=true'
try {
    $client.Open()
    $command = $client.CreateCommand()
    $command.CommandText = 'SELECT @@SPID;'
    $sessionId = [int]$command.ExecuteScalar()
    $admin.Open()
    $command = $admin.CreateCommand()
    $command.CommandText = 'SELECT encrypt_option FROM sys.dm_exec_connections WHERE session_id=@id AND net_transport=N''TCP'';'
    [void]$command.Parameters.Add('@id', [Data.SqlDbType]::Int)
    $command.Parameters['@id'].Value = $sessionId
    $observed = [string]$command.ExecuteScalar()
    if ($observed -ne 'TRUE') { throw 'SQL Server did not enforce TLS for an optional-encryption TCP client.' }
    [pscustomobject]@{
        ServerForceEncryption = $forced
        OptionalEncryptionTcpClientObservedEncryption = $observed
        CertificateMode = 'Lab self-signed; private routing; production requires a trusted server certificate'
    } | ConvertTo-Json -Compress | Write-Output
}
finally {
    $client.Dispose()
    $admin.Dispose()
    $builder.Clear()
    $PipelinePassword = $null
}
