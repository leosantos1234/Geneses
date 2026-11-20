# Configuração MCP local para Codex

## Dependência

SDK oficial Python `mcp==1.30.0` instalado no ambiente de teste; requisito do projeto: `mcp>=1.28,<2`. A linha v1 foi escolhida para manter compatibilidade com o servidor `FastMCP` deste sprint. Referência: https://github.com/modelcontextprotocol/python-sdk

## Transporte e comando

Transporte: STDIO, sem porta de rede.

No diretório raiz do projeto, configure as variáveis fora de arquivos versionados:

```powershell
$env:GENESIS_DATABASE_BACKEND = "postgres"
$env:GENESIS_DATABASE_URL = "postgresql://usuario:senha@host:5432/genesis_trading"
python -m Robo.mcp.server
```

Para registrar no cliente MCP/Codex, use o comando `python -m Robo.mcp.server`, diretório de trabalho `C:\Users\NPO\Desenvolvimento\genesis`, transporte `stdio` e as mesmas duas variáveis de ambiente. Não coloque senha no manifest nem no repositório.

## Teste e remoção

```powershell
python Robo\tests\test_mcp_stdio_protocol.py
```

Remover/desabilitar: retire a entrada do cliente MCP; nenhum processo de rede precisa ser encerrado. O servidor não inicia worker, exchange ou API.

## Estado da integração

O protocolo MCP local foi testado ponta a ponta por harness STDIO. A configuração está pronta para o Codex, mas não foi registrada automaticamente no host Codex nesta sessão; não há API de registro local disponível neste workspace.
