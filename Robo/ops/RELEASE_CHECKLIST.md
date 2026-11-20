# Release Checklist (Sprint 8)

## 1) Pre-flight tecnico
- Validar sintaxe: `python -m py_compile compra1.py trading_bot_geneses.py ops/healthcheck.py ops/autorestart.py auditoria/strategy_audit.py`
- Rodar healthcheck: `python ops/healthcheck.py --config ops/services.json`
- Confirmar `global_status=OK` ou justificar alertas.

## 2) Configuracao de operacao
- Revisar `.env-*` do ativo alvo:
  - `LSTM_SERVICE_PORT`
  - `LSTM_API_URL`
  - limites de risco (`MAX_*`, `RISK_*`)
  - versoes (`BOT_VERSION`, `STRATEGY_VERSION`, `PARAMS_VERSION`)
- Ativar somente servicos necessarios em `ops/services.json` (`enabled=true`).

## 3) Gate de robustez
- Verificar ultimo relatorio diario em `reports/daily/`.
- Bloquear release se:
  - PF abaixo da meta definida.
  - DD acima do tolerado.
  - setup sem amostra minima de trades.

## 4) Deploy controlado
- Subir stack:
  - API/UI conforme `start_ui_api.bat` / `start_ui_stack.bat`
  - Bot e LSTM do ativo
- Rodar supervisor em dry-run:
  - `python ops/autorestart.py --config ops/services.json --dry-run`
- Se OK, habilitar supervisor real por agendamento.

## 5) Pos-release (primeiros 30-60 min)
- Acompanhar:
  - `logs/critical_events.jsonl`
  - `logs/ops_supervisor.jsonl`
  - `logs/strategy_audit.jsonl`
- Confirmar:
  - sem bloqueios inesperados
  - sem restart em loop
  - latencia/slippage dentro de limite

