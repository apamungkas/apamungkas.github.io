# Stops the live telemetry simulator started by start_simulator.ps1.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $here "simulator.pid"
if (-not (Test-Path $pidFile)) { Write-Host "Simulator is not running"; exit 0 }
$id = [int](Get-Content $pidFile)
if (Get-Process -Id $id -ErrorAction SilentlyContinue) { Stop-Process -Id $id; Write-Host "Simulator stopped (PID $id)" } else { Write-Host "Simulator process $id not found" }
Remove-Item $pidFile -ErrorAction SilentlyContinue
