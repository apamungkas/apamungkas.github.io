# Starts the live telemetry simulator in the background (hidden window).
# Usage: .\start_simulator.ps1 [-Minutes 480]
param([double]$Minutes = 480)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $env:USERPROFILE ".venvs\fabric-dt\Scripts\python.exe"
$pidFile = Join-Path $here "simulator.pid"
if (Test-Path $pidFile) {
    $old = Get-Content $pidFile
    if (Get-Process -Id $old -ErrorAction SilentlyContinue) { Write-Host "Simulator already running (PID $old)"; exit 0 }
}
$p = Start-Process -FilePath $py -ArgumentList @("`"$here\simulate_telemetry.py`"", "--minutes", $Minutes) -WorkingDirectory $here -WindowStyle Hidden -PassThru
Write-Host "Simulator started (PID $($p.Id)) for $Minutes minutes. Log: $here\simulator.log"
