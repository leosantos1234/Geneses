# Genesis: serviços separados

## API do painel

`iniciar_api_painel.bat` inicia apenas a API de consulta/autenticação na porta 8010.

## Painel/PWA

`iniciar_painel.bat` inicia apenas o frontend React/Vite na porta 8080.

## Robô

`iniciar_robo.bat .env-btc` inicia apenas o worker de negociação. Troque o arquivo por `.env-eth`, `.env-sol`, `.env-bnb` ou `.env-xrp` quando necessário.

Cada processo tem sua própria janela e pode ser reiniciado sem derrubar os demais. Em produção, a API e o painel ficam em serviços web separados e o robô em um Background Worker.
