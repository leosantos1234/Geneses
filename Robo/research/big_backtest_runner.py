"""Runner de backtest grande para estrategia de breakout.

Executa:
1) carga de historico usando funcao existente de backtest_geral;
2) backtest breakout com funcao existente;
3) calculo de metricas com pandas;
4) salvamento do resultado em JSON.
"""

from __future__ import annotations

import importlib
import inspect
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# =========================================================
# Configuracao principal (edite aqui)
# =========================================================
SIMBOLO = "BTCUSDT"
TIMEFRAME = "1"
QTD_CANDLES = 5000
CAPITAL_INICIAL_PADRAO = 10000.0

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _to_builtin(value: Any) -> Any:
    """Converte tipos numpy/pandas para tipos serializaveis em JSON."""
    if isinstance(value, dict):
        return {k: _to_builtin(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_builtin(v) for v in value]
    if isinstance(value, float):
        if math.isinf(value) or math.isnan(value):
            return None
        return value
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    if isinstance(value, (float, int, str, bool)) or value is None:
        return value
    return str(value)


def _importar_dependencias() -> tuple[Any, Callable[..., Any], Callable[..., Any]]:
    """Importa modulos/funcoes com fallback para layout atual do projeto."""
    bt_module = None
    bt_errors: list[str] = []
    for mod_name in ("backtesting_geral", "estrategia_entrada.backtesting_geral"):
        try:
            bt_module = importlib.import_module(mod_name)
            break
        except Exception as exc:  # pragma: no cover - diagnostico
            bt_errors.append(f"{mod_name}: {exc}")

    if bt_module is None:
        raise ImportError("Falha ao importar backtesting_geral. " + " | ".join(bt_errors))

    carregar_fn = getattr(bt_module, "carregar_dados", None)
    if carregar_fn is None:
        carregar_fn = getattr(bt_module, "carregar_dados_historicos", None)
    if carregar_fn is None:
        raise AttributeError(
            "Nao encontrei carregar_dados/carregar_dados_historicos em backtesting_geral."
        )

    breakout_fn = None
    bo_errors: list[str] = []
    for mod_name in (
        "estrategia_breakout_filtros",
        "estrategia_entrada.estrategia_breakout_filtros",
    ):
        try:
            bo_module = importlib.import_module(mod_name)
            breakout_fn = getattr(bo_module, "executar_backtest_breakout", None)
            if breakout_fn is not None:
                break
        except Exception as exc:  # pragma: no cover - diagnostico
            bo_errors.append(f"{mod_name}: {exc}")

    if breakout_fn is None:
        raise ImportError(
            "Falha ao importar executar_backtest_breakout. " + " | ".join(bo_errors)
        )

    return bt_module, carregar_fn, breakout_fn


def _carregar_historico(
    bt_module: Any,
    carregar_fn: Callable[..., Any],
    symbol: str,
    timeframe: str,
    candles: int,
) -> pd.DataFrame:
    """Chama a funcao de carga respeitando a assinatura encontrada."""
    sig = inspect.signature(carregar_fn)
    params = sig.parameters
    kwargs: dict[str, Any] = {}

    if "session" in params:
        session_obj = getattr(bt_module, "session", None)
        if session_obj is None:
            from sessao_bybit import session as session_obj  # fallback local
        kwargs["session"] = session_obj

    if "symbol" in params:
        kwargs["symbol"] = symbol
    if "interval" in params:
        kwargs["interval"] = timeframe
    elif "timeframe" in params:
        kwargs["timeframe"] = timeframe

    if "limite" in params:
        kwargs["limite"] = candles
    elif "limit" in params:
        kwargs["limit"] = candles

    df = carregar_fn(**kwargs)
    if df is None:
        raise RuntimeError("carregar_dados retornou None.")

    df = pd.DataFrame(df).copy()
    if df.empty:
        raise RuntimeError("Historico vazio para os parametros informados.")

    for col in ("open", "high", "low", "close", "volume", "turnover"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "time" in df.columns:
        df = df.sort_values("time").reset_index(drop=True)
    else:
        df = df.reset_index(drop=True)

    return df


def _extrair_metricas(
    resultado_backtest: Any, capital_inicial: float = CAPITAL_INICIAL_PADRAO
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Calcula metricas robustas a partir do retorno do backtest."""
    if not isinstance(resultado_backtest, dict):
        return {
            "lucro_total": 0.0,
            "winrate": 0.0,
            "media_ganho": 0.0,
            "media_perda": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "total_trades": 0,
        }, []

    trades = resultado_backtest.get("trades")
    if not isinstance(trades, list):
        trades = resultado_backtest.get("relatorio_completo", {}).get("trades", [])
    if not isinstance(trades, list):
        trades = []

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return {
            "lucro_total": 0.0,
            "winrate": 0.0,
            "media_ganho": 0.0,
            "media_perda": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "total_trades": 0,
        }, []

    lucro_col = "lucro" if "lucro" in trades_df.columns else None
    if lucro_col is None:
        for candidate in ("profit_loss", "pnl", "resultado"):
            if candidate in trades_df.columns:
                lucro_col = candidate
                break
    if lucro_col is None:
        return {
            "lucro_total": 0.0,
            "winrate": 0.0,
            "media_ganho": 0.0,
            "media_perda": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "total_trades": int(len(trades_df)),
        }, trades_df.to_dict(orient="records")

    lucros = pd.to_numeric(trades_df[lucro_col], errors="coerce").fillna(0.0)
    ganhos = lucros[lucros > 0]
    perdas = lucros[lucros < 0]

    lucro_total = float(lucros.sum())
    total_trades = int(len(lucros))
    winrate = float((len(ganhos) / total_trades) * 100.0) if total_trades else 0.0
    media_ganho = float(ganhos.mean()) if not ganhos.empty else 0.0
    media_perda = float(perdas.mean()) if not perdas.empty else 0.0

    gross_profit = float(ganhos.sum()) if not ganhos.empty else 0.0
    gross_loss_abs = float(abs(perdas.sum())) if not perdas.empty else 0.0
    if gross_loss_abs == 0.0:
        profit_factor = math.inf if gross_profit > 0 else 0.0
    else:
        profit_factor = gross_profit / gross_loss_abs

    equity_curve = pd.concat(
        [
            pd.Series([float(capital_inicial)]),
            pd.Series(float(capital_inicial) + lucros.cumsum()),
        ],
        ignore_index=True,
    )
    rolling_max = equity_curve.cummax()
    drawdown = (equity_curve - rolling_max) / rolling_max.replace(0, pd.NA)
    max_drawdown_pct = float(abs(drawdown.min()) * 100.0) if not drawdown.empty else 0.0

    metricas = {
        "lucro_total": lucro_total,
        "winrate": winrate,
        "media_ganho": media_ganho,
        "media_perda": media_perda,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown_pct,
        "total_trades": total_trades,
    }
    return metricas, trades_df.to_dict(orient="records")


def _print_metricas(metricas: dict[str, Any], symbol: str, timeframe: str, candles: int) -> None:
    print("\n=== BIG BACKTEST BREAKOUT ===")
    print(f"Simbolo: {symbol}")
    print(f"Timeframe: {timeframe}")
    print(f"Candles: {candles}")
    print("-" * 34)
    print(f"Total de trades : {metricas['total_trades']}")
    print(f"Lucro total     : {metricas['lucro_total']:.6f}")
    print(f"Winrate         : {metricas['winrate']:.2f}%")
    print(f"Media de ganho  : {metricas['media_ganho']:.6f}")
    print(f"Media de perda  : {metricas['media_perda']:.6f}")
    pf = metricas["profit_factor"]
    pf_fmt = "inf" if isinstance(pf, float) and math.isinf(pf) else f"{pf:.4f}"
    print(f"Profit factor   : {pf_fmt}")
    print(f"Max drawdown    : {metricas['max_drawdown_pct']:.2f}%")


def rodar_big_backtest() -> None:
    bt_module, carregar_fn, breakout_fn = _importar_dependencias()
    df = _carregar_historico(
        bt_module=bt_module,
        carregar_fn=carregar_fn,
        symbol=SIMBOLO,
        timeframe=TIMEFRAME,
        candles=QTD_CANDLES,
    )

    try:
        resultado = breakout_fn(df_original=df, symbol=SIMBOLO)
    except TypeError:
        # fallback para implementacoes antigas
        resultado = breakout_fn(df)

    capital_inicial = float(
        (resultado or {}).get("saldo_inicial", CAPITAL_INICIAL_PADRAO)
        if isinstance(resultado, dict)
        else CAPITAL_INICIAL_PADRAO
    )
    metricas, trades = _extrair_metricas(resultado, capital_inicial=capital_inicial)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"breakout_{SIMBOLO}_{TIMEFRAME}_{QTD_CANDLES}_{ts}.json"

    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "parametros": {
            "symbol": SIMBOLO,
            "timeframe": TIMEFRAME,
            "candles": QTD_CANDLES,
        },
        "metricas": metricas,
        "resultado_backtest": resultado if isinstance(resultado, dict) else {"raw": str(resultado)},
        "trades": trades,
    }

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(_to_builtin(payload), f, ensure_ascii=False, indent=2)

    _print_metricas(metricas, SIMBOLO, TIMEFRAME, QTD_CANDLES)
    print(f"\nResultado salvo em: {out_path}")


if __name__ == "__main__":
    rodar_big_backtest()
