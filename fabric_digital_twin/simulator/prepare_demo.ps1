# Prepares the digital twin demo before a session: back-fills telemetry since the simulator last ran
# (so charts have no gap) and starts the live simulator.
# Usage: .\prepare_demo.ps1 [-Minutes 240]
param([double]$Minutes = 240)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$env:PYTHONIOENCODING = "utf-8"
$pidFile = Join-Path $here "simulator.pid"
$running = $false
if (Test-Path $pidFile) { $running = [bool](Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue) }
if (-not $running) {
    python "$root\scripts\build_eventhouse.py" --fill-gap
    if ($LASTEXITCODE -ne 0) { Write-Host "Gap fill failed (check az login)"; exit 1 }
}
& "$here\start_simulator.ps1" -Minutes $Minutes
