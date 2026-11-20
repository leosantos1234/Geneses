# Schema de Decision Lineage

## `decision_trace`

Chave `decision_id UUID`; timestamps; símbolo, timeframe, estratégia, ambiente e modo; `final_status`; vínculos opcionais de trade/order/position; versões; contexto JSONB sanitizado; fingerprint; reason code/message; `legacy`; `lineage_quality`.

## `decision_events`

`event_id BIGSERIAL`, `decision_id` com FK para `decision_trace`, timestamp, `stage`, `event_type`, `component`, `status`, `reason_code`, duração, payload JSONB sanitizado e `schema_version`. A tabela é somente append no serviço; não há update/delete de eventos.

## Compatibilidade

A alteração em `trades` é somente a coluna nullable `decision_id UUID` e índice. Trades históricos continuam sem vínculo (`decision_id IS NULL`) e não recebem identificador fabricado.
