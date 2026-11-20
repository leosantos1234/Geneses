param(
    [string]$TaskName = "GenesisQuantSupervisor",
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"

$runnerBat = Join-Path $Root "ops\run_supervisor_task.bat"
if (-not (Test-Path $runnerBat)) {
    throw "Batch runner nao encontrado: $runnerBat"
}

try {
    $action = New-ScheduledTaskAction -Execute $runnerBat
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
    } catch {}

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Supervisor autorestart do Genesis Quant" | Out-Null
    Write-Host "Tarefa instalada com sucesso (Register-ScheduledTask): $TaskName"
}
catch {
    Write-Warning "Register-ScheduledTask falhou. Tentando fallback via schtasks.exe (ONLOGON, usuario atual)..."
    $args = @(
        "/Create",
        "/TN", $TaskName,
        "/TR", $runnerBat,
        "/SC", "ONLOGON",
        "/RL", "LIMITED",
        "/F"
    )
    $p = Start-Process -FilePath "schtasks.exe" -ArgumentList $args -NoNewWindow -PassThru -Wait
    if ($p.ExitCode -ne 0) {
        throw "Falha ao criar tarefa via schtasks (exit=$($p.ExitCode))."
    }
    Write-Host "Tarefa instalada com sucesso (schtasks): $TaskName"
}

Write-Host "Comando para iniciar agora:"
Write-Host "schtasks /Run /TN `"$TaskName`""
