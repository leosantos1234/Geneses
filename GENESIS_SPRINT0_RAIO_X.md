# GÊNESIS QUANT — SPRINT 0: RAIO-X COMPLETO

**Data da auditoria:** 2026-10-06  
**Escopo:** `C:\Users\NPO\Desenvolvimento\genesis`  
**Natureza:** somente leitura/análise. Nenhum código, configuração, banco, dependência ou deploy foi alterado.

## 1. Resumo executivo

O Gênesis é hoje um sistema Python/FastAPI de trading algorítmico com três superfícies paralelas:

1. **Worker operacional** em `Robo/trading_bot_geneses.py`, que coleta mercado, calcula indicadores, classifica regime, executa o funil de estratégias, consulta serviços/modelos auxiliares e chama a camada de execução.
2. **API de agentes** em `Agente_IA/api_agente_ia.py`, que agrega rotas de análise e também possui rotas capazes de criar, cancelar, fechar ou ajustar operações.
3. **API/painel operacional** em `Robo/api_ui.py` e frontend React/Vite, predominantemente para consulta de trades, posições e resumo.

Há componentes funcionais ou quase funcionais de Bybit, OKX, Breakout, Mean Reversion, Market Making, Early Trend, LSTM, Random Forest, XGBoost, sentimento, OpenAI, risco, idempotência, paper trading, backtest e observabilidade. Porém, eles não constituem uma arquitetura única e coerente. Há versões duplicadas, persistências incompatíveis, caminhos relativos, arquivos de outro ambiente, serviços desabilitados no catálogo de ops, modelos disponíveis mas não demonstradamente conectados ao fluxo produtivo e registros históricos com baixa capacidade de reconstrução causal.

Conclusão de maturidade: **experimental/parcial**. O sistema contém uma cadeia operacional real e guardas importantes, mas não há evidência suficiente para classificá-lo como arquitetura madura ou produção controlada. O maior risco é a coexistência de dois ecossistemas de decisão/execução e de SQLite/PostgreSQL sem uma fonte de verdade inequívoca.

Evidências quantitativas encontradas:

- `Robo/trades.db`: 1.443 trades, 115.632 previsões LSTM, 660.160 eventos de log.
- `Robo/trades.db`: 1.320 trades fechados, 35 abertos, 48 sem status e 40 importados; 78 sem preço de saída/PnL; 1.262 sem `entry_score`; todos sem `sentiment_score`; a maior parte sem SL/TP, regime ou telemetria de execução.
- `Robo/predictions_lstm`: predominância de `hold` (110.084 de 115.632) e `error_percent` nulo em todos os registros observados.
- `Robo/reports/paper/paper_phase_status.json`: 1 trade em 1 dia, ainda inelegível para micro.
- `Robo/reports/daily/daily_walk_forward_2026-03-08.md`: status `SEM_DADOS`, zero runs e zero trades.

## 2. Arquitetura atual

### Topologia observada

```text
Exchange (Bybit; OKX opcional/read-only por padrão)
        │
        ├── Robo/trading_bot_geneses.py  ──> estratégias/regime/modelos
        │                                  └─> commander + risk + execution
        │                                      └─> exchange + trades.db/logs
        │
        ├── Agente_IA/api_agente_ia.py ──> análise/LLM/rotas de ação
        │                                  └─> Bybit + decisoes_ia.db
        │
        └── Robo/api_ui.py ──> ui_backend/repository.py ──> PostgreSQL pretendido
                              └─> frontend React/Vite
```

### Componentes e contratos principais

| Componente | Entrada | Saída | Dependências/chamadores | Estado atual |
|---|---|---|---|---|
| Worker | `.env-*`, símbolo, candles e exchange | decisões, ordens, monitoramento e persistência | `Robo/SERVICOS.md`, `trading_bot_geneses.py`, `compra1.py` | principal candidato a fluxo ativo |
| Exchange factory | `ACTIVE_EXCHANGE`, credenciais | cliente Bybit/OKX | `Robo/exchange/factory.py` | Bybit padrão; OKX com escrita bloqueada por padrão |
| Estratégias | OHLCV, indicadores, regime | candidatos, direção e setup | `Robo/estrategia_entrada/*`, `Robo/estrategias/*` | parcialmente conectadas ao worker |
| Risk Engine | DB, exposição, limites e env | allow/block, kill switch, transições | `Robo/utils/risk_guard.py`, `execution_gate.py` | presente e chamado, mas dependente de caminhos/DB |
| Comandante local | contexto do trade, score e regime | autorização/ajustes | `Robo/comandante/hierarquia.py` | determinístico; não executa ordem |
| Comandante IA | contexto de mercado e votos | decisão e ação exchange | `Agente_IA/cerebro_ia/agente_comandante.py`, `tomar_decisao_comandante.py` | rota paralela, autônoma em ações |
| Persistência | trade, previsão, evento | SQLite ou PostgreSQL | `Robo/banco_trade/db_trades.py`, `ui_backend/repository.py` | inconsistente; requer definição de fonte oficial |
| UI | token e filtros | JSON de resumo/trades/posições | `Robo/api_ui.py`, frontend Lovable/Vite | leitura protegida |
| Ops | catálogo, processo, portas | healthcheck/restart/logs | `Robo/ops/*` | catálogo com serviços `enabled:false` |

### Observações estruturais

- `trading_bot_geneses.py` tem definições duplicadas, código executável no import e funções antigas no mesmo arquivo. Isso dificulta provar qual implementação será usada em cada processo.
- `Robo/ops/services.json`, documentação e comandos de restart ainda apontam para `C:\Users\emgepron\Documents\robo`, diferente da raiz auditada.
- `docker-compose.genesis.yml` descreve API 8010, painel 8080, worker e PostgreSQL 16. `render.yaml` no root descreve serviços de web API, painel, worker e banco; não substitui a topologia local e não foi validado em execução.
- O catálogo de ops tem worker/LSTM/UI API desabilitados; isso é uma configuração declarativa, não evidência de que nenhum processo externo esteja rodando.
- Não há raiz Git detectável em `genesis` nem subdiretório `.git` encontrado. Portanto não foi possível produzir um diff/status Git válido; isso é uma limitação de controle, não uma confirmação de limpeza do histórico.

## 3. Componentes principais

### Worker e ciclo de mercado

`Robo/trading_bot_geneses.py` é o ponto de entrada descrito em `Robo/SERVICOS.md` e `Dockerfile.worker`. `main()` prepara mercado/modelos e `iniciar_robo()` mantém o loop operacional. O fluxo inclui múltiplos timeframes, indicadores, FSM de regime, backtest curto, validação de setup, idempotência, guardas de produção e chamada de `compra1.executar_ordem_completa`.

O arquivo também contém funções de análise OpenAI, carregamento de modelos e código de versões anteriores. O código no nível de módulo inicializa sessão e acessa mercado antes do bloco principal; importá-lo não é uma operação puramente declarativa.

### Backend e frontend

- `Robo/api_ui.py`: `/api/auth/login`, `/api/auth/me`, `/api/ui/health`, `/api/ui/summary`, `/api/ui/open-positions`, `/api/ui/trades`, `/api/ui/trades/{trade_id}` e `/api/ui/filters`. As consultas de dados são protegidas por `require_auth`, exceto health/login.
- `Robo/ui_backend/auth.py`: token HMAC próprio, TTL de 8 horas, segredo mínimo de 32 caracteres e credenciais em env.
- `Robo/ui_backend/repository.py`: consulta `trades`; por default tenta PostgreSQL em `127.0.0.1:5432/genesis_trading` e só usa SQLite numa condição que, com o default atual, fica praticamente inalcançável.
- `Robo/ui/lovable_export/genesis-quant-terminal-main`: aplicação React/Vite com rotas de dashboard, trades, posições, risco/configuração e cliente em `src/lib/api.ts`.

### API de agentes

`Agente_IA/api_agente_ia.py` inclui rotas de análise, gestão, criação de ordem, decisão, monitoramento, market making, controle de ordens, trailing, ajuste de SL/TP e inicialização do robô. Ela contém CORS amplo e não foi encontrada uma dependência uniforme de autenticação nas rotas de ação.

## 4. Agentes existentes

| Agente/módulo | Dados | Estado/memória | ML/LLM | Decide | Executa | Classificação técnica |
|---|---|---|---|---|---|---|
| `cerebro_ia/agente_comandante.py` | contexto de mercado/trade | sem memória própria; configuração global de modo | heurística; sem ML/LLM | sim | indiretamente, via rota | ensemble determinístico nomeado “agente” |
| `agente_defensivo` | risco, direção, contexto | não | não | voto | não | regra especializada |
| `agente_ofensivo` | sinal e contexto | não | não | voto | não | regra especializada |
| `agente_estrategico` | estratégia/regime | não | não | voto | não | regra especializada |
| `agente_tatico` | indicadores/posição | não | não | voto | não | regra especializada |
| `trade_ia.py` | indicadores ou histórico de trades | sem memória persistente própria | RF/modelo via `ml_core`; GPT em sugestão agregada | sim | não no ramo de sugestão | serviço híbrido |
| `analyze_market.py` | RSI, ATR, Bollinger, volume | não | GPT-3.5 com fallback técnico | sim/análise | não | serviço de análise |
| `analyze_trade.py` | trade, mercado, RSI/ATR/tendência | não | GPT-3.5 + regras de reversão | ajuste sugerido | não diretamente | serviço de ajuste |
| `manage_trade.py` | posição, preço, ATR, sentimento | cooldown em processo | GPT-3.5 + validação | sim | sim, `set_trading_stop` | agente operacional de risco |
| `tomar_decisao_comandante.py` | contexto e votos dos quatro agentes | persiste decisão em `Agente_IA/logs/decisoes_ia.db` | sem LLM no voto | sim | sim: fecha, parcial, aumenta ou ajusta trailing | caminho autônomo de maior risco |

Nenhum desses agentes possui memória semântica, aprendizagem online ou planejamento multi-etapas persistente. O termo “agente” descreve módulos de decisão/serviço; a autonomia real aparece principalmente quando a rota chama métodos da exchange.

## 5. Learning

O que existe:

- `Robo/feedback/feedback_trade.py` grava trades encerrados em `Robo/feedback/trades_log.json` e calcula win/loss por regime, estratégia e score baixo.
- `Robo/trade_quality/trade_classifier.py` tem infraestrutura extensa de dataset, features, calibração, validação, relatório e potencial treinamento/reconciliação.
- `Robo/utils/paper_mode.py` calcula elegibilidade para promoção de PAPER para `MICRO_READY` com dias ativos, trades, profit factor, drawdown e tracking error.
- O histórico inclui `Robo/trade_quality/trade_quality.db` com 131 amostras.

O que não foi demonstrado como ativo no caminho operacional:

- não há loop de aprendizagem online alterando parâmetros do worker a cada resultado;
- não há scheduler confirmado para retreinamento periódico;
- não há ligação comprovada entre o feedback JSON e uma nova decisão do worker;
- nas 131 amostras, campos de modelo/calibração como `probabilidade`, `flag_filtrado`, `teria_bloqueado` e `modelo_status` estão nulos;
- não há evidência de uma versão de estratégia/parametrização promovida automaticamente com rollback.

Risco metodológico: `ml_core` ajusta scaler antes da separação aleatória e usa `train_test_split` sem separação temporal; isso pode contaminar avaliação e superestimar generalização. O walk-forward existe como ferramenta separada, mas o relatório diário observado estava sem runs.

## 6. Machine Learning

### `ml_core`

Arquivo: `Robo/utils/ml_core.py`.

- Algoritmos: `RandomForestClassifier` e `XGBClassifier`.
- Features observadas: `close`, `volume`, `SMA_10`, `SMA_50`, `RSI`, `ATR`.
- Target: direção do próximo fechamento, `(close.shift(-1) > close).astype(int)`.
- Dados: CSVs/preprocessados e indicadores calculados localmente.
- Treino: split aleatório 80/20, `RandomizedSearchCV`, `cv=2`, métrica principal de acurácia; scaler e modelo persistidos via joblib.
- Inferência: `validar_com_modelo`; não foi observada uma interface operacional consistente de probabilidade calibrada.
- Ativação: o worker pode treinar/carregar Random Forest quando não encontra modelo; não há evidência equivalente de XGBoost sendo escolhido no fluxo live.

### Outros ML

Há scripts em `Robo/analise`, `Robo/automl`, `Robo/modelos` e cópias com TensorFlow/Keras, PyTorch e Stable Baselines. A presença de dependências e scripts não prova execução. Não foi encontrada uma cadeia de reinforcement learning ligada à ordem real.

## 7. LSTM

### Implementações

- `Robo/estrategias/lstm_rnn.py`: preparação de janelas e modelo Keras com features OHLCV selecionadas, duas camadas LSTM, dropout e saída sigmoid.
- `Robo/estrategias/lstm_service.py`: serviço HTTP `/predict`; recebe janela de 10x5, aplica scalers/modelo e retorna preço previsto, delta, direção e confiança.
- `Robo/trading_bot_geneses.py:1981` (`obter_previsao_next_candle`): consulta o serviço LSTM e, no fluxo observado, registra previsões em `predictions_lstm`.
- Há modelos `.keras` por ativo e versões `.h5`/scripts antigos em `Robo/estrategias`.

### Integração real

O worker prepara previsão, confiança e `error_percent`, mas `trades.decisao_lstm` está nulo em toda a amostra de trades observada. A tabela de previsões tem 115.632 registros, mas nenhum `error_percent` preenchido e predominância extrema de `hold`; portanto a LSTM existe e produz telemetria, mas a influência causal na decisão/score de entrada não está demonstrada.

`Robo/ops/services.json` lista `lstm_btc` e `lstm_eth`, ambos `enabled:false`. Há ainda versões duplicadas do serviço. Isso impede afirmar que um único endpoint/modelo é a autoridade atual.

## 8. XGBoost

`XGBClassifier` está implementado em `Robo/utils/ml_core.py` e aparece nas dependências. O algoritmo usa o mesmo conjunto geral de features do núcleo ML e pode ser treinado pelo caminho genérico de `treinar_modelo`. Não há evidência, no worker principal ou nos registros, de modelo XGBoost selecionado, carregado ou com peso próprio no score. Classificação: **existente como capacidade experimental, não comprovadamente ativa**.

## 9. LLMs

| Local | Modelo | Contexto/prompt | Uso da resposta | Fallback/telemetria |
|---|---|---|---|---|
| `Robo/utils/openai_module.py` | GPT-3.5 Turbo | prompt de análise de mercado | texto/JSON eventual | timeout env, `max_retries=0`, logs |
| `Robo/estrategias/analise_sentimento.py` | GPT-3.5 Turbo | notícia/trecho quando TextBlob é inconclusivo | sentimento | TextBlob primeiro, cooldown/cache |
| `Agente_IA/analyze_market.py` | GPT-3.5 Turbo | RSI, ATR, Bollinger, preço, volume | buy/sell/hold, justificativa, score | fallback técnico por RSI |
| `Agente_IA/analyze_trade.py` | GPT-3.5 Turbo | posição, preço, indicadores e contexto | SL/TP/trailing/ação | regras de reversão e validação |
| `Agente_IA/manage_trade.py` | GPT-3.5 Turbo | dados da posição e mercado | ajustes de proteção | validação de orientação/RRR e cooldown |
| `Agente_IA/trade_ia.py` | GPT-3.5 Turbo | histórico/performance de trades | sugestão textual | ramo técnico quando há indicadores |

O caminho do worker registra que a análise contextual OpenAI pode ser ignorada e seguir o funil local. Assim, chamadas LLM existem, mas não há um contrato único dizendo quando um LLM tem poder de veto, voto, peso ou apenas explicação. Custos não têm telemetria financeira própria; há logs e limites/cooldowns parciais.

## 10. Comandante atual

Há dois “Comandantes” diferentes.

### Comandante operacional local

`Robo/comandante/hierarquia.py:avaliar_decisao_comandante` recebe contexto, decisão base, score e regime. Usa `COMMANDER_MIN_SCORE` (default 4.0) e limiar alto (default 7.0), bloqueia score insuficiente e certos regimes/estratégias, e retorna autorização e ajustes de risco/trailing. É determinístico, sem LLM, sem memória, sem aprendizado e sem execução direta.

Ele é chamado pelo caminho de `Robo/compra1.py:1006`, antes do `execution_gate` em torno de `compra1.py:1202`. Não decide Top 6, não combina previsões LSTM/LLM por pesos e não registra uma decisão rica própria.

### Comandante da API de agentes

`Agente_IA/cerebro_ia/agente_comandante.py` combina quatro votos heurísticos com pesos dependentes do modo global `MODO_OPERACAO`. `Agente_IA/tomar_decisao_comandante.py` persiste timestamp, símbolo, decisão, votos, agentes e resposta da exchange em `Agente_IA/logs/decisoes_ia.db`. A rota pode executar fechamento total/parcial, aumento de posição e `set_trading_stop`.

Este segundo caminho é mais autônomo e não é equivalente ao primeiro. A coexistência é um risco de governança: duas semânticas de “Comandante”, duas persistências e diferentes guardas de autorização.

## 11. Top 6 / ranking

Não foi encontrada implementação comprovada de **Top 6 de ativos**.

O mecanismo equivalente atual é o ranking de candidatos dentro de uma janela de backtest em `Robo/estrategia_entrada/backtesting_geral.py`. Ele:

- detecta regime atual/histórico;
- seleciona candidatos por regime: BREAKOUT, MEAN_REVERSION, EARLY_TREND e MARKET_MAKING_LATERAL;
- aplica filtro de setup presente, evidência histórica, trades e custos;
- ordena candidatos e escolhe um vencedor;
- não mantém uma lista Top 6 de ativos, não possui rotina clara de permanência/saída de uma cesta e não recebe influência demonstrada do Learning ou do Comandante.

Há pesos nominais `PESOS` para LSTM/OpenAI/indicadores/volatilidade/tendência no arquivo, mas o caminho de seleção observado usa prioridade, setup e métricas do backtest; os pesos nominais parecem legado ou não são decisivos nessa função. Há rankings hardcoded em funções diagnósticas antigas.

## 12. Estratégias

| Estratégia | Implementação | Regime/condição | Situação |
|---|---|---|---|
| BREAKOUT | `estrategia_entrada/backtesting_geral.py`, módulos de entrada | regimes 1/2 e setup confirmado | principal/elegível no funil |
| MEAN_REVERSION | `estrategias/mean_reversion/*` e backtest geral | regimes 1/2/3 conforme FSM | presente; evidência histórica negativa em relatórios observados |
| MARKET_MAKING_LATERAL | `estrategia_entrada/Market_Making/*` | regime lateral 3 | presente, não necessariamente ativo |
| EARLY_TREND | `estrategias/early_trend/*` | regime 2 e `EARLY_TREND_ENABLED` | opcional |
| LEAD_LAG | rota/caminho condicional no worker | `MODO_ESTRATEGIA=LEAD_LAG` | condicional/experimental |

A confirmação final de entrada combina regime, indicadores, setup, idempotência, risco, autorização do Comandante e modo de produção. O sistema escolhe uma estratégia/candidato, não um portfólio Top 6.

## 13. Risk Engine

`Robo/utils/risk_guard.py` e `Robo/execution/execution_gate.py` formam a camada de risco.

Capacidades encontradas:

- limites de perda diária/semanal/rolling em USDT e percentual;
- drawdown, número de trades, perdas consecutivas e cooldown;
- limites globais e por símbolo;
- limite de trades abertos, exposição aberta, margem e risco por trade;
- snapshot de saldo da exchange com cache curto no gate;
- kill switch em `risk_kill.flag` e transição em `logs/risk_transition_state.json`;
- eventos críticos em `logs/critical_events.jsonl` e alerta Telegram opcional;
- duplicidade/TTL e idempotência por símbolo/timeframe/candle/side.

Limitações importantes: `risk_guard` consulta SQLite por caminho relativo (`trades.db`), enquanto o módulo oficial de banco defaulta PostgreSQL; `RISK_GUARD_REFERENCE_BALANCE` defaulta zero, o que desabilita comparações percentuais até configuração explícita; defaults e envs não estão centralizados em uma configuração versionada única.

## 14. Execução

`Robo/compra1.py` contém `executar_ordem_completa`, wrappers de criação/cancelamento, aplicação de SL/TP e integração com `execution_gate.py`.

O `execution_gate` valida contexto, risco, duplicidade, TTL, exposição, saldo e resposta da exchange antes de chamar o envio. O envio padrão usa `compra1.enviar_ordem_adaptativa`; existem caminhos auxiliares em `entrada_breakout_confirmada.py` e `ordem_limite_entrada.py`.

Capacidades de exchange encontradas:

- Bybit via `pybit`, cliente criado por `Robo/exchange/factory.py`;
- OKX via `Robo/exchange/okx_client.py`, normalizado para um contrato parecido com Bybit;
- leitura de candles, ticker, orderbook, instrumentos, funding, saldo e posições;
- colocação/cancelamento de ordens e `set_trading_stop` em caminhos Bybit/live;
- bloqueio de escrita por default no cliente OKX (`OKX_READ_ONLY=true` implícito).

O worker possui guardas `ENABLE_LIVE_TRADING`, `ALLOW_MAINNET_TRADING` e `ALLOW_MAINNET_FROM_ENVFILE`. A existência dos guardas é positiva, mas não elimina o risco da API de agentes paralela, que possui métodos diretos de ação.

## 15. Backtest

### Implementações

- `Robo/estrategia_entrada/backtesting_geral.py`: backtest/funil de estratégias, janela `BACKTEST_WINDOW` default 400, regime, setup, custos e escolha de vencedor.
- `Robo/Backtest/walk_forward_runner.py`: janelas treino/teste, mínimos de trades, PF, janelas positivas, drawdown, Monte Carlo, sensibilidade e stress de custo.
- `Robo/Backtest/backtest_multiativos.py`: backtest multiativos com sizing, risco e alavancagem.
- `Robo/backtest_module.py` e wrappers antigos: backtest simples/legado e geração de relatório/plot.
- `Robo/Backtest/data_quality_gate.py`: valida OHLCV, timestamps, gaps, duplicidade e hash.

### Dados, custos e métricas

Os dados vêm de CSVs em `Robo/dados_historicos`, candles obtidos da exchange e datasets de treino. O backtest geral inclui taxa maker/taker, funding, slippage por ativo e alavancagem configuráveis. O multiativo possui sizing e risco; o detalhe exato varia por implementador.

As métricas incluem trades, PnL, retorno, win rate, profit factor, drawdown, custo, Monte Carlo e elegibilidade. Os relatórios existentes não são uma série contínua: o diário consultado estava `SEM_DADOS`, e o paper tinha somente 1 trade.

### Diferenças prováveis para live

- backtest usa série de candles/execução simulada; live sofre latência, fill parcial, rejeição, funding real e estado externo;
- o worker pode chamar backtest como filtro, enquanto o resultado histórico não necessariamente vira score persistido no trade;
- persistência de custos/telemetria live está majoritariamente nula no `Robo/trades.db`;
- versões/custos/parâmetros não têm um identificador obrigatório e imutável em cada trade.

## 16. Banco de dados

### Persistências principais observadas

| Local/estrutura | Conteúdo | Observação |
|---|---|---|
| `Robo/trades.db:trades` | trades, lado, preços, SL/TP, PnL, estratégia, fonte, modelo, regime | 1.443 linhas; schema mais novo, porém muitos campos nulos |
| `Robo/trades.db:predictions_lstm` | previsão, direção prevista/real, confiança, janela, sequência | 115.632 linhas; erro nulo e hold dominante |
| `Robo/trades.db:mr_fsm_state` | estado de Mean Reversion por símbolo | 5 símbolos |
| `Robo/trades.db:log_events` | timestamp, nível, logger, estratégia, símbolo, mensagem | 660.160 linhas; maioria sem estratégia/símbolo |
| `Agente_IA/trades.db:trades` | histórico legado de trades | 1.675 linhas, BTCUSDT/Breakout/AI |
| `Agente_IA/logs/decisoes_ia.db:decisoes_ia` | decisões, votos, agentes, resposta exchange | 410 decisões |
| `Robo/trade_quality/trade_quality.db:trade_quality_samples` | features de qualidade, resultado, PnL, drawdown | 131 amostras |
| `Robo/banco_trade/trades.db` | schema legado/auxiliar | vazio nas tabelas observadas |
| `Robo/trades1.db` e `Robo/research/trades.db` | bases antigas/diagnóstico | não são fonte atual demonstrada |

### Inconsistência de fonte

`Robo/banco_trade/db_trades.py` declara `Robo/trades.db` como `DB_PATH`, mas `_database_url()` defaulta uma URL PostgreSQL e `_using_postgres()` retorna verdadeiro com esse default. `ui_backend/repository.py`, `trade_classifier.py`, `execution_gate.py` e `risk_guard.py` possuem decisões próprias sobre DB. `Robo/banco_trade/db_manager.py` ainda aponta para um caminho absoluto de outro computador. Isso cria risco de leituras/escritas em bases diferentes.

Não foram alteradas tabelas, dados, índices ou schemas nesta auditoria.

## 17. Trades históricos

O schema mais novo de `Robo/trades.db` prevê campos de decisão, modelo, regime, consenso, LSTM, sentimento, stops, execução e importação Bybit. Na prática, a cobertura é insuficiente:

- `model_version` está preenchido na maioria dos trades;
- `strategy`, `decision_source`, símbolo e status estão razoavelmente presentes, mas muitos são valores de importação/recovery;
- `entry_score`, sentimento, consenso, regime, tendência, SL, TP, trailing e telemetria de execução apresentam cobertura muito baixa ou nula;
- `order_id` está ausente em parte dos registros e `exec_id` quase sempre ausente;
- há trades importados/recovered que não carregam o contexto original da decisão.

Portanto, é possível reconstruir parcialmente **o que ocorreu**, mas não de forma confiável **por que o sistema entrou ou não entrou**. O material serve como histórico de execução e resultado; serve apenas parcialmente como dataset de ML/memória até que se faça reconciliação, marcação de origem e separação entre trades reais, importados, paper e testes.

## 18. Logs

### Canais

- arquivos diários em `logs/YYYY-MM-DD_trading_bot_geneses.log` via `Robo/utils/logger_config.py`;
- `Robo/trades.db:log_events` por `SQLiteHandler`, filtrando palavras-chave;
- `logs/critical_events.jsonl`, `logs/risk_transition_state.json` e `risk_kill.flag`;
- `Robo/feedback/trades_log.json`;
- `Robo/reports/*` para auditoria, data quality, paper e validação;
- `Agente_IA/logs/decisoes_ia.db` para decisões do Comandante IA;
- `Robo/ops/ops_supervisor.jsonl` e estados de supervisor.

O logger tem stream + arquivo e handler SQLite, mas não foi observada política uniforme de rotação/retenção. Muitos eventos do banco estão sem estratégia e símbolo. Há normalização de mojibake, indicando histórico de problemas de encoding.

### Reconstrução de decisão

**Não é possível reconstruir de forma determinística uma decisão passada a partir dos logs atuais.** Faltam, em grande parte dos trades, snapshot de indicadores, features/modelo, prompt e resposta LLM, versão de estratégia/parâmetros, ranking completo, decisão de cada agente, score final, regime, motivo de bloqueio, latência, preço de mercado no instante e vínculo único entre decisão, ordem, fills e posição.

## 19. APIs e ferramentas internas

Existem endpoints de consulta de health, resumo, posições abertas, trades, detalhe e filtros em `Robo/api_ui.py`. São os candidatos mais seguros para uma futura superfície read-only.

Na API de agentes existem consultas e ações: análise de mercado/trade, `status_sistema`, `performance_sistema`, `dashboard/*`, `/ordens_abertas`, criação de ordens, `/manage_trade`, cancelamento, decisão de saída, trailing, ajustes e `/iniciar_robo`.

Backtest e walk-forward são funções/CLIs reutilizáveis, mas não há endpoint read-only padronizado para executar/consultar/ comparar resultados. `risk_guard` oferece funções úteis de consulta, porém acopladas a caminhos e envs.

## 20. Integrações externas

| Integração | Uso | Estado observado |
|---|---|---|
| Bybit/pybit | mercado, saldo, posições, ordens, stops, closed PnL | integração central |
| OKX | cliente V5 com normalização | presente; leitura por padrão |
| OpenAI | análise, sentimento, ajuste e sugestão | presente em múltiplos caminhos |
| NewsAPI/fontes de notícias | captura de notícias para sentimento | presente com cache |
| TextBlob | polaridade inicial | presente |
| Telegram | alerta de transição de risco | opcional |
| PostgreSQL | persistência pretendida da API/worker | declarada, mas não validada em runtime |
| Docker/Render | empacotamento/deploy descrito | configuração presente; deploy não realizado |

## 21. MCP / RAG / Skills já existentes

Não foi encontrada implementação própria de:

- servidor ou cliente MCP;
- protocolo equivalente de exposição estruturada de tools;
- embeddings, vector store, pgvector, FAISS, Pinecone, Weaviate, Qdrant ou busca semântica;
- recuperação contextual antes de chamadas LLM;
- Skills/workflows versionados para execução por LLM.

Há funções e runbooks reutilizáveis (`ops`, backtest, healthcheck, rollback), mas eles são scripts/documentação convencionais, não Skills de agente. Há caches de notícias, funding, modelos e JSON, mas não são RAG.

## 22. Pontos candidatos para MCP

Recomendação exclusivamente de planejamento; nada foi implementado.

| Ferramenta candidata | Função existente | Dados acessados | Risco | Read-only? | Prioridade MCP |
|---|---|---|---|---|---|
| `get_system_health` | healthcheck/ops | processos, portas, endpoints | baixo | sim | P0 |
| `list_open_positions` | `api_ui.py`/exchange | posições | médio | sim | P0 |
| `list_trades` | `api_ui.py`/repository | trades persistidos | baixo | sim | P0 |
| `get_trade_detail` | `api_ui.py` | um trade e metadados | baixo | sim | P0 |
| `get_risk_status` | `evaluate_risk_limits` | limites, exposição, kill switch | médio | sim | P0 |
| `get_strategy_candidates` | backtest/ranking | regime, setup, métricas | baixo | sim | P1 |
| `get_lstm_predictions` | `predictions_lstm` | previsões e confiança | baixo | sim | P1 |
| `get_model_inventory` | arquivos/relatórios ML | versões/modelos/artefatos | baixo | sim | P1 |
| `get_sentiment_snapshot` | sentimento/cache | notícias e resultado | médio | sim | P1 |
| `get_commander_decision` | logs de decisões | votos, agentes, decisão | médio | sim | P1 |
| `get_backtest_report` | relatórios existentes | métricas e custos | baixo | sim | P1 |
| `compare_backtests` | existe parcialmente | dois relatórios/versões | baixo | sim | P2 |
| `run_backtest` | CLI/funções | datasets, CPU e arquivos | médio | não necessariamente; somente sandbox | P2 |
| `create_order` | existe na API | exchange/dinheiro | crítico | não | não recomendado inicialmente |
| `cancel_order`/`close_position` | existe na API | ordens/posição | crítico | não | não recomendado inicialmente |
| `modify_strategy` | não há contrato seguro | config/params | crítico | não | não recomendado |

## 23. Riscos e dívida técnica

### Crítico

- Há material com credenciais/chaves em arquivos de texto, cópias antigas e artefatos de resultados; nomes observados incluem arquivos de chave no root/Robo e resultados AutoML. O conteúdo não é reproduzido aqui. Isso exige tratar os segredos como potencialmente comprometidos.
- `Agente_IA/api_agente_ia.py` inclui rotas de ação exchange e inicialização do robô; CORS amplo e ausência de autenticação uniforme elevam o risco de execução não autorizada.
- Dois caminhos de decisão/execução: worker local e Comandante IA direto. Não existe uma autoridade única evidente.
- Fonte de dados ambígua: PostgreSQL pretendido versus SQLite efetivamente populado, com módulos usando defaults diferentes.
- `docker-compose.genesis.yml` contém credenciais locais previsíveis e segredo de autenticação de exemplo; perigoso se exposto fora de ambiente local.

### Alto

- Trade histórico não registra de forma confiável contexto, score, modelo, LLM, regime, telemetria e vínculo ordem/fill; auditoria causal fica comprometida.
- LSTM persistida sem erro e com hold dominante; não há prova de qualidade ou de peso decisório na entrada.
- Modelagem ML usa split aleatório e scaler ajustado antes da separação; risco de leakage/overfitting.
- Risk guard e execução usam caminhos relativos e podem consultar base diferente da escrita oficial.
- Código operacional contém side effects no import, duplicação e versões no mesmo arquivo; risco de comportamento dependente do diretório/processo.
- Não há Top 6 real, controle de portfólio ou ranking de ativos persistido.

### Médio

- Serviços ops/LSTM aparecem desabilitados e com caminhos absolutos antigos; contrato de deploy não está fechado.
- Logs sem retenção/rotação uniforme e muitos eventos sem símbolo/estratégia.
- Feedback e trade quality existem, mas não há evidência de influência efetiva na decisão live.
- Várias versões `Copia`, `Backup`, datadas e `Exe_antigo` dificultam definir código canônico.
- Há inconsistências de encoding e nomes de modelo (`LSTM_GÃ©neses`/`LSTM_Gêneses`).

### Baixo

- Healthcheck, release checklist, rollback, data quality gate e paper gate já formam bons blocos de operação, embora ainda não estejam integrados em uma governança única.
- A API do painel possui autenticação HMAC e separação de leitura, mas a segurança do ecossistema como um todo não é uniforme.

## 24. Lacunas de observabilidade

Para uma reconstrução completa faltam, por decisão/ordem:

- `decision_id` único propagado de sinal → ranking → Comandante → gate → ordem → fill → posição → saída;
- snapshot imutável das features, indicadores, regime e parâmetros;
- versão de código, estratégia, modelo, prompt e configuração em cada decisão;
- voto/justificativa de cada agente e resultado bruto de LLM;
- score bruto, score normalizado, limiares e motivos de veto;
- latência por etapa, request id, exchange order id e execution id;
- fill parcial, slippage, fee, funding e reconciliação com closed PnL;
- distinção inequívoca entre live, paper, backtest, importado e recovered;
- métricas de qualidade/calibração por modelo e por regime;
- retenção e rotação documentadas para logs e artefatos.

## 25. O que pode ser reutilizado sem alteração

Como base conceitual para uma futura superfície read-only, sem conceder escrita:

- endpoints de consulta de `Robo/api_ui.py` e autenticação de `ui_backend/auth.py`;
- leitura de trades/posições/filtros, após confirmar a fonte DB em ambiente controlado;
- `Robo/ops/healthcheck.py` e relatórios de data quality;
- funções de leitura de `risk_guard` e relatórios paper/backtest;
- tabela `predictions_lstm` e `decisoes_ia`, com os limites de completude documentados;
- `walk_forward_runner.py` e geradores de relatório como processos isolados, sem permitir escrita na exchange;
- runbooks de release/rollback como documentação operacional.

“Reutilizar sem alteração” aqui significa reutilizar como referência/camada de leitura; não significa que a integração atual esteja pronta para exposição pública.

## 26. O que precisaria ser adaptado futuramente

Sem implementar neste sprint:

- escolher e impor uma fonte de verdade de persistência, com contrato de schema e migrações controladas;
- separar código canônico de backups/cópias e retirar side effects de import;
- unificar o Comandante ou definir claramente autoridade, permissões e fronteira entre decisão e execução;
- criar contrato de evento/decision lineage para auditoria causal;
- transformar risco e execution gate em serviço único com DB/configuração consistentes;
- validar LSTM/XGB/RF fora da amostra, por série temporal e por regime, antes de usar score/probabilidade;
- integrar paper, walk-forward, promoção e rollback com evidência contínua;
- fechar autenticação/autorização da API de agentes e separar endpoints read-only de mutações;
- secret management e rotação das credenciais expostas;
- só então desenhar MCP read-only, com allowlist de ferramentas, schemas, timeouts, auditoria e bloqueio explícito de ordens;
- RAG/Skills apenas depois de definir fontes confiáveis, versionamento e política de atualização.

## 27. Conclusão sobre maturidade atual

O Gênesis não é um protótipo vazio: possui um worker real, integração de exchange, estratégias, guardas de risco, execução validada, dados históricos, LSTM, ML, LLM, paper mode, backtest e ferramentas de operação. O problema é a falta de uma linha de produção única e verificável. A arquitetura atual deve ser tratada como **experimental com trechos funcionais**, não como plataforma madura para autonomia ampliada.

O próximo passo seguro, fora deste Sprint 0, é estabelecer uma baseline canônica somente leitura: qual processo é ativo, qual banco é oficial, quais rotas são autorizadas, quais serviços estão realmente ligados e quais trades são live/paper/importados. Só depois faz sentido construir MCP, RAG, Skills ou um Comandante 2.0.

## Matriz final obrigatória

| Capacidade | Existe? | Ativa? | Onde está | Maturidade | Pode entrar no MCP? |
|---|---|---|---|---|---|
| Learning | parcial | não comprovada | `feedback`, `trade_quality`, `paper_mode` | experimental | sim, somente leitura |
| ML | sim | RF parcialmente; XGB não comprovado | `Robo/utils/ml_core.py` | parcial | sim |
| LSTM | sim | telemetria/predição; peso decisório não comprovado | `Robo/estrategias`, worker, `predictions_lstm` | parcial | sim |
| XGBoost | sim | não comprovada | `ml_core.py` | experimental | sim, inventário/resultado |
| Sentiment | sim | chamado no worker/serviço | `estrategias/analise_sentimento.py` | parcial | sim |
| LLM | sim | múltiplos caminhos; influência variável | OpenAI modules e `Agente_IA` | parcial | sim, logs/resultados; não executar |
| Agentes | sim | API paralela | `Agente_IA/*` | experimental | sim, somente decisões registradas |
| Comandante | sim, em duas versões | local no worker; API paralela | `Robo/comandante`, `Agente_IA/cerebro_ia` | parcial | sim, leitura |
| Top 6 | não | não | ranking escolhe um vencedor | inexistente | não |
| Estratégias | sim | Breakout principal; outras condicionais | `estrategia_entrada`, `estrategias` | funcional/parcial | sim, leitura |
| Risk Engine | sim | chamado no worker/gate | `risk_guard`, `execution_gate` | funcional/parcial | sim, leitura |
| Backtest | sim | scripts/funções; diário sem dados | `Backtest`, `backtesting_geral.py` | parcial | sim, leitura; execução isolada |
| Trades | sim | históricos/populados | `Robo/trades.db`, outras bases | funcional como armazenamento; baixa qualidade causal | sim |
| Logs | sim | vários canais | `log_events`, arquivos, JSONL, reports | parcial | sim |
| Memória | limitada | arquivos/DB, sem memória semântica | feedback, decisões, caches | experimental | sim, leitura |
| RAG | não | não | nenhum | inexistente | futuro |
| Skills | não | não | nenhum formato de skill do produto | inexistente | futuro |
| MCP | não | não | nenhum servidor/cliente | inexistente | objetivo futuro |

## Controle de alterações

- **Arquivos de código modificados:** nenhum.
- **Banco alterado:** não.
- **Migration criada:** não.
- **Dependências instaladas:** não.
- **Deploy realizado:** não.
- **Comportamento do sistema alterado:** não.
- **Única criação permitida:** este relatório `GENESIS_SPRINT0_RAIO_X.md`.
- **Git:** `git status` e `git log` na raiz informada retornaram “not a git repository”; nenhuma pasta `.git` foi encontrada sob a raiz auditada. Assim, não foi possível validar `git diff`/`git status` como controle de baseline. Não houve reversão nem alteração deliberada de mudanças preexistentes.
- **Limitações de leitura:** `tmptkv7wm4x` retornou acesso negado; diretórios de dependências, `__pycache__`, backups e cópias foram tratados como artefatos não canônicos e não como prova de operação ativa.
