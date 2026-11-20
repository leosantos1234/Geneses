import logging
import os

from supervisor.servicos import (
    consultar_total_trades_db,
    fechar_symbol,
    fechar_todas,
    obter_analise_feedback,
    obter_resumo_feedback,
    obter_status_robo,
)


logger = logging.getLogger(__name__)


def usuario_autorizado(user_id: int | str | None) -> bool:
    autorizado = str(os.getenv("TELEGRAM_SUPERVISOR_USER_ID", "")).strip()
    return bool(autorizado) and str(user_id or "").strip() == autorizado


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def responder_status() -> str:
    status = obter_status_robo()
    regime = status.get("regime_atual") or "indisponivel"
    pnl = status.get("pnl_total")
    robo_ativo = "SIM" if status.get("robo_ativo") else "NAO"
    return (
        "STATUS DO ROBO\n"
        f"Ativo: {robo_ativo}\n"
        f"Trades ativos: {status.get('trades_ativos', 0)}\n"
        f"PnL atual: {pnl}\n"
        f"Regime atual: {regime}"
    )


def responder_resumo() -> str:
    resumo = obter_resumo_feedback(limit_recent=5)
    analise = resumo.get("analise") or {}
    total_db = consultar_total_trades_db()
    ultimas = resumo.get("ultimas_operacoes") or []

    linhas = [
        "RESUMO DO ROBO",
        f"Total de trades: {total_db if total_db is not None else analise.get('total_trades', 0)}",
        f"Win rate: {_fmt_pct(float(analise.get('win_rate') or 0.0))}",
        "Ultimas operacoes:",
    ]

    if not ultimas:
        linhas.append("- Nenhuma operacao registrada.")
    else:
        for trade in ultimas[:5]:
            linhas.append(
                f"- {trade.get('symbol', 'N/A')} | {trade.get('resultado', 'N/A')} | "
                f"pnl={trade.get('pnl', 'N/A')} | regime={trade.get('regime', 'N/A')}"
            )

    return "\n".join(linhas)


def responder_analise() -> str:
    analise = obter_analise_feedback(limit_recent=50)
    linhas = [
        "ANALISE DE FEEDBACK",
        f"Perdas por regime: {analise.get('perdas_por_regime', {})}",
        f"Perdas por estrategia: {analise.get('perdas_por_estrategia', {})}",
        f"Perdas com score baixo: {analise.get('perdas_com_score_baixo', 0)}",
        "Insights:",
    ]

    insights = analise.get("insights") or []
    if not insights:
        linhas.append("- Sem insights relevantes no momento.")
    else:
        for insight in insights:
            linhas.append(f"- {insight}")

    return "\n".join(linhas)


def responder_fechar_todas() -> str:
    resultado = fechar_todas()
    return resultado.get("mensagem", "Falha ao processar /fechar_todas.")


def responder_fechar_symbol(symbol: str) -> str:
    resultado = fechar_symbol(symbol)
    return resultado.get("mensagem", f"Falha ao processar /fechar {symbol}.")


def processar_comando(texto: str, user_id: int | str | None) -> str:
    logger.info("Comando recebido | user_id=%s | texto=%s", user_id, texto)
    print(f"[Telegram] user_id={user_id} | texto={texto}")

    texto = str(texto or "").strip().lower()

    if not usuario_autorizado(user_id):
        logger.warning("Usuario nao autorizado tentou usar supervisor | user_id=%s", user_id)
        return "⛔ Acesso não autorizado"

    comando = texto

    try:
        if comando == "/start":
            resposta = (
                "🤖 Bot ativo!\n\n"
                "Comandos disponíveis:\n"
                "/status\n"
                "/resumo\n"
                "/analise\n"
                "/fechar_todas\n"
                "/fechar BTCUSDT"
            )
        elif comando == "/help":
            resposta = (
                "📘 Ajuda:\n"
                "/status → status do robô\n"
                "/resumo → visão geral\n"
                "/analise → análise dos trades\n"
                "/fechar_todas → fecha tudo\n"
                "/fechar BTCUSDT → fecha ativo"
            )
        elif comando == "/status":
            resposta = responder_status()
        elif comando == "/resumo":
            resposta = responder_resumo()
        elif comando == "/analise":
            resposta = responder_analise()
        elif comando == "/fechar_todas":
            resposta = responder_fechar_todas()
        elif comando.startswith("/fechar "):
            symbol = comando.split(maxsplit=1)[1].strip().upper()
            resposta = responder_fechar_symbol(symbol)
        else:
            resposta = "❌ Comando não reconhecido. Use /help"

        logger.info("Resultado do comando | texto=%s | resposta=%s", comando, resposta)
        return resposta
    except Exception as e:
        logger.exception("Falha ao processar comando supervisor")
        return f"Não consegui processar o comando com segurança. Detalhe: {e}"
