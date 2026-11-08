$ErrorActionPreference = 'Stop'
$sql = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('__SQL_BASE64__'))
$connection = New-Object System.Data.SqlClient.SqlConnection 'Server=localhost;Database=master;Integrated Security=true;Encrypt=true;TrustServerCertificate=true'
try {
    $connection.Open()
    foreach ($batch in [regex]::Split($sql, '(?im)^\s*GO\s*(?:--[^\r\n]*)?\r?$')) {
        if ([string]::IsNullOrWhiteSpace($batch)) { continue }
        $command = $connection.CreateCommand()
        $command.CommandText = $batch
        $command.CommandTimeout = 600
        $adapter = New-Object System.Data.SqlClient.SqlDataAdapter $command
        $data = New-Object System.Data.DataSet
        [void]$adapter.Fill($data)
        foreach ($table in $data.Tables) {
            $rows = @()
            foreach ($row in $table.Rows) {
                $record = [ordered]@{}
                foreach ($column in $table.Columns) {
                    $value = $row[$column]
                    if ($value -is [DBNull]) { $value = $null }
                    elseif ($value -is [byte[]]) { $value = '0x' + [BitConverter]::ToString($value).Replace('-', '') }
                    elseif ($value -is [datetime]) { $value = $value.ToString('o') }
                    $record[$column.ColumnName] = $value
                }
                $rows += [pscustomobject]$record
            }
            ConvertTo-Json -InputObject $rows -Depth 8 -Compress | Write-Output
        }
    }
}
finally {
    $connection.Dispose()
}
