# Rollback Operacional (Seguro)

## Cenarios de rollback
- Falha recorrente do LSTM (porta/memoria/processo).
- Aumento anormal de latencia/slippage.
- Bloqueios de risco inesperados apos release.
- Divergencia forte entre backtest/paper e execucao real.

## Procedimento rapido
1. Congelar novas entradas:
- Ativar kill switch de risco (via env/flag operacional).

2. Parar componentes da release atual:
- Encerrar processo do bot e LSTM afetado.

3. Voltar para configuracao anterior:
- Restaurar `.env-*` anterior.
- Restaurar parametros/versao anterior (`BOT_VERSION`, `STRATEGY_VERSION`, `PARAMS_VERSION`).

4. Reiniciar stack minimo:
- Subir apenas bot + LSTM estavel do ativo.
- Validar com `python ops/healthcheck.py --config ops/services.json`.

5. Confirmacao pos-rollback:
- Verificar `logs/critical_events.jsonl`.
- Verificar `logs/strategy_audit.jsonl` (evento com versao restaurada).
- Acompanhar 30 minutos sem anomalias.

## Evidencias minimas
- Horario de inicio/fim do incidente.
- Servicos impactados.
- Versao anterior restaurada.
- Causa provavel e acao corretiva planejada.

