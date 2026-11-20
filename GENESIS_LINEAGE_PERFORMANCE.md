# Performance — Sprint 4

O caminho quente apenas gera UUID, atualiza contexto local e coloca jobs em uma fila daemon; a escrita PostgreSQL ocorre fora da decisão. `flush()` existe para testes e encerramento controlado.

Microbenchmark local do caminho de enfileiramento, com writer substituído por mock: 1.000 decisões / 2.000 jobs em 13,68 ms, média de 0,0137 ms por decisão. Esse número não representa latência PostgreSQL e deve ser acompanhado em ambiente operacional.

O teste executado não chamou exchange nem execution function. O contrato de falha é best-effort: exceções do writer são contadas como `lineage_write_failures` e registradas sem serem lançadas ao worker de trading.

Medição operacional recomendada no próximo ciclo: acompanhar `lineage_write_latency_ms`, `lineage_write_failures`, tamanho da fila e proporção de decisões sem evento final. O serviço já mantém os contadores básicos em memória; não foi criado armazenamento paralelo.
