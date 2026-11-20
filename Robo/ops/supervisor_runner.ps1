param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExe = "python",
    [string]$ConfigPath = "ops/services.json",
    [int]$IntervalSec = 60
)

$ErrorActionPreference = "Continue"

Set-Location $Root
New-Item -ItemType Directory -Force -Path ".\logs" | Out-Null

Write-Host "[SUPERVISOR_RUNNER] root=$Root interval=${IntervalSec}s config=$ConfigPath"

while ($true) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts] rodada supervisor..."
    & $PythonExe ".\ops\autorestart.py" --config $ConfigPath --json-out ".\logs\supervisor_last.json"
    Start-Sleep -Seconds $IntervalSec
}
