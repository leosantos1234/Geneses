# Swing Trend Following

Estratégia de médio prazo baseada em tendência confirmada nos candles de 4h
e diário. O worker de 5 minutos pode consumir o sinal fechado desses
timeframes, sem abrir uma nova tela ou um novo worker.

Regras iniciais:

- regimes elegíveis: `1` e `2`;
- EMA 50/200 no 4h alinhada com EMA 20/50 no diário;
- rompimento da máxima/mínima dos 20 candles anteriores no 4h;
- stop, alvo e trailing calculados por ATR;
- nenhum envio de ordem neste módulo: a autorização continua no funil central.

O módulo permanece desativado até o backtest walk-forward incluir taxas,
funding, slippage e a gestão real da posição. Isso evita que uma estratégia
nova seja selecionada antes de existir evidência suficiente.
