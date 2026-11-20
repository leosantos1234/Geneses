# Retreinamento automático do LSTM

O `lstm_service.py` detecta entradas fora do intervalo aprendido pelo scaler.
Após `LSTM_AUTO_RETRAIN_AFTER` ocorrências, inicia o processo isolado
`auto_retrain_lstm.py` para o ativo configurado no `.env`.

O processo:

1. Busca candles recentes diretamente da OKX.
2. Treina modelo e scalers juntos.
3. Mede MAPE em uma janela de validação temporal.
4. Rejeita candidatos acima de `LSTM_RETRAIN_MAX_MAPE_PCT`.
5. Publica os três artefatos, preservando backup.
6. Recarrega o modelo em memória e informa o resultado em `/retrain-status`.

Configuração recomendada nos `.env-*`:

```dotenv
LSTM_AUTO_RETRAIN_ENABLED=1
LSTM_AUTO_RETRAIN_AFTER=3
LSTM_RETRAIN_COOLDOWN_SECONDS=21600
LSTM_RETRAIN_CANDLES=300
LSTM_RETRAIN_EPOCHS=25
LSTM_RETRAIN_MAX_MAPE_PCT=5.0
LSTM_RETRAIN_BAR=5m
```

O retreinamento não envia ordens. Enquanto o treinamento está em andamento,
o modelo atual continua carregado; a camada de decisão deve continuar tratando
`out_of_range=true` como `fallback_poucos_dados` e não autorizar uma entrada
baseada no LSTM. Para testar sem ativação automática, use
`LSTM_AUTO_RETRAIN_ENABLED=0`.
