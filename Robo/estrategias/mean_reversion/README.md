# Mean Reversion (FSM + Stop-Limit)

Estratégia de Mean Reversion baseada em máquina de estados (FSM), onde a confirmação da reversão vem do **preço**:

- Detecta um fundo potencial (sem lookahead)
- Arma uma ordem **stop-limit** como “prova de mercado” (rompimento de micro-resistência)
- Entra apenas se houver fill válido (evita chase/gap acima do limit)
- Cancela por invalidação (novo fundo) ou expiração

Módulos:
- `mean_reversion_fsm.py`: FSM + regras de detecção/fill (conservador OHLC)
- `mean_reversion_engine.py`: orquestração para produção (executor + persistência)
- `mr_executor.py`: interface para plugar ccxt/pybit
- `mr_state_store.py`: persistência em SQLite
- `mr_backtest.py`: runner de backtest da MR

