# Ops Toolkit

## Arquivos
- `ops/services.json`: catalogo de servicos, portas, processo e comando de restart.
- `ops/healthcheck.py`: verifica memoria/processo/porta/endpoint.
- `ops/autorestart.py`: executa healthcheck e reinicia servicos criticos com cooldown.
- `ops/RELEASE_CHECKLIST.md`: checklist de release.
- `ops/ROLLBACK.md`: runbook de rollback.

## Uso rapido
- Healthcheck:
  - `python ops/healthcheck.py --config ops/services.json`
- Healthcheck com JSON:
  - `python ops/healthcheck.py --config ops/services.json --json-out logs/healthcheck_latest.json`
- Supervisor (simulacao):
  - `python ops/autorestart.py --config ops/services.json --dry-run`
- Supervisor (real):
  - `python ops/autorestart.py --config ops/services.json --json-out logs/supervisor_last.json`

## Runner continuo (Windows)
- Rodar em loop manual:
  - `powershell -ExecutionPolicy Bypass -File ops/supervisor_runner.ps1 -Root (Resolve-Path ..) -PythonExe "python" -ConfigPath "ops/services.json" -IntervalSec 60`

## Instalar no Task Scheduler (Windows)
- Instalar tarefa:
  - `powershell -ExecutionPolicy Bypass -File ops/install_supervisor_task.ps1 -TaskName "GenesisQuantSupervisor" -Root (Resolve-Path ..)`
- Iniciar tarefa:
  - `Start-ScheduledTask -TaskName "GenesisQuantSupervisor"`
- Ver status:
  - `Get-ScheduledTask -TaskName "GenesisQuantSupervisor" | Get-ScheduledTaskInfo`
- Remover tarefa:
  - `Unregister-ScheduledTask -TaskName "GenesisQuantSupervisor" -Confirm:$false`

## Saidas geradas
- `logs/ops_supervisor.jsonl`
- `logs/ops_supervisor_state.json`
- `logs/critical_events.jsonl` (evento estruturado adicional quando houver restart)
