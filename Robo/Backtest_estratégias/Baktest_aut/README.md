# Laboratório automático de backtests

O arquivo `Laboratorio de Backtests.bat`, na pasta `Robo/Entrar no sistema`,
executa este laboratório localmente. Ele usa somente os históricos em
`Robo/dados_historicos/swing_36m` e não abre conexão com corretora.

Uso:

```text
Laboratorio de Backtests.bat
Laboratorio de Backtests.bat --assets AVAXUSDT --min-return 300
```

Saídas:

- `candidatos/`: JSON das combinações acima do filtro;
- `relatorios/`: ranking CSV de todas as combinações;
- `logs/`: acompanhamento completo da execução;
- `aprovados/`: reservado para candidatos aprovados manualmente;
- `rejeitados/`: reservado para resultados descartados manualmente.

O resultado é apenas uma indicação de pesquisa. Nada é implementado no robô
automaticamente e nenhum candidato é enviado ao Render.
