import glob
import json
import os
from datetime import datetime

from dash import Dash, dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objects as go
import pandas as pd

app = Dash(__name__)


def _read_latest_daily_report() -> str:
    pattern = os.path.join("reports", "daily", "daily_walk_forward_*.md")
    files = glob.glob(pattern)
    if not files:
        return "Nenhum relatorio diario encontrado em reports/daily."
    latest = max(files, key=os.path.getmtime)
    try:
        with open(latest, "r", encoding="utf-8") as f:
            body = f.read()
    except Exception as e:
        return f"Falha ao ler relatorio diario: {e}"
    return f"[ARQUIVO] {latest}\n\n{body}"


def _read_critical_events_tail(n: int = 50) -> str:
    path = os.path.join("logs", "critical_events.jsonl")
    if not os.path.exists(path):
        return "Nenhum evento crítico estruturado encontrado em logs/critical_events.jsonl."
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
    except Exception as e:
        return f"Falha ao ler eventos críticos: {e}"

    out = [f"[ARQUIVO] {path}", ""]
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            evt = json.loads(raw)
            out.append(
                f"{evt.get('ts','')} | {evt.get('severity','')} | {evt.get('event_type','')} | "
                f"{evt.get('symbol','')} | {evt.get('source','')} | {evt.get('message','')}"
            )
        except Exception:
            out.append(raw)
    return "\n".join(out) if out else "Sem eventos críticos recentes."


def _load_daily_csv_rows() -> pd.DataFrame:
    pattern = os.path.join("reports", "daily", "daily_walk_forward_*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        return pd.DataFrame()
    frames = []
    for fp in files:
        try:
            d = pd.read_csv(fp)
            if d is None or d.empty:
                continue
            if "run_date" not in d.columns:
                base = os.path.basename(fp).replace("daily_walk_forward_", "").replace(".csv", "")
                d["run_date"] = base
            frames.append(d)
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    for c in [
        "profit_factor",
        "max_drawdown_pct",
        "mc_prob_loss_pct",
        "cost_stress_2x_pf",
        "total_pnl",
    ]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    if "approved" in out.columns:
        out["approved"] = out["approved"].astype(str).str.lower().isin(["true", "1", "yes", "y", "sim"])
    if "cost_stress_2x_approved" in out.columns:
        out["cost_stress_2x_approved"] = out["cost_stress_2x_approved"].astype(str).str.lower().isin(["true", "1", "yes", "y", "sim"])
    out["run_date"] = pd.to_datetime(out["run_date"], errors="coerce")
    out = out.dropna(subset=["run_date"]).sort_values("run_date")
    return out


def _build_risk_robustness_summary(df: pd.DataFrame) -> str:
    if df.empty:
        return "Sem dados de risco/robustez ainda. Gere relatórios em reports/daily."
    latest_date = df["run_date"].max()
    snap = df[df["run_date"] == latest_date].copy()
    if snap.empty:
        return "Sem snapshot recente de risco/robustez."
    avg_pf = float(snap["profit_factor"].mean()) if "profit_factor" in snap.columns else 0.0
    worst_dd = float(snap["max_drawdown_pct"].max()) if "max_drawdown_pct" in snap.columns else 0.0
    worst_mc = float(snap["mc_prob_loss_pct"].max()) if "mc_prob_loss_pct" in snap.columns else 0.0
    avg_pf_2x = float(snap["cost_stress_2x_pf"].mean()) if "cost_stress_2x_pf" in snap.columns else 0.0
    appr = float(snap["approved"].mean() * 100.0) if "approved" in snap.columns else 0.0
    appr2 = (
        float(snap["cost_stress_2x_approved"].mean() * 100.0)
        if "cost_stress_2x_approved" in snap.columns
        else 0.0
    )
    summary = (
        f"Data base: {latest_date.date()}\n"
        f"PF médio: {avg_pf:.3f}\n"
        f"DD pior: {worst_dd:.2f}%\n"
        f"Prob. perda MC (pior): {worst_mc:.2f}%\n"
        f"PF médio com custo 2x: {avg_pf_2x:.3f}\n"
        f"Aprovação base: {appr:.1f}% | Aprovação custo 2x: {appr2:.1f}%"
    )
    phase_path = os.path.join("reports", "paper", "paper_phase_status.json")
    if os.path.exists(phase_path):
        try:
            with open(phase_path, "r", encoding="utf-8") as f:
                phase = json.load(f) or {}
            m = phase.get("metrics", {}) if isinstance(phase.get("metrics"), dict) else {}
            summary += (
                f"\nPaper phase: {phase.get('phase_current', 'PAPER')} -> {phase.get('phase_next', 'PAPER')}"
                f"\nElegivel micro: {phase.get('eligible_for_micro', False)}"
                f"\nTracking error medio: {float(m.get('avg_tracking_error_pct', 0.0)):.4f}%"
                f"\nTrades paper (janela): {int(m.get('trades', 0))}"
            )
        except Exception:
            pass
    return summary


def _build_risk_robustness_figure(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if df.empty:
        fig.update_layout(title="Risco e Robustez (sem dados)", template="plotly_white", height=360)
        return fig
    daily = (
        df.groupby("run_date", as_index=False)
        .agg(
            pf_mean=("profit_factor", "mean"),
            dd_max=("max_drawdown_pct", "max"),
            mc_loss_max=("mc_prob_loss_pct", "max"),
            pf_2x_mean=("cost_stress_2x_pf", "mean"),
        )
        .sort_values("run_date")
    )
    fig.add_trace(go.Scatter(x=daily["run_date"], y=daily["pf_mean"], mode="lines+markers", name="PF médio"))
    fig.add_trace(go.Scatter(x=daily["run_date"], y=daily["pf_2x_mean"], mode="lines+markers", name="PF médio (2x custo)"))
    fig.add_trace(go.Scatter(x=daily["run_date"], y=daily["dd_max"], mode="lines+markers", name="DD máx %", yaxis="y2"))
    fig.add_trace(go.Scatter(x=daily["run_date"], y=daily["mc_loss_max"], mode="lines+markers", name="MC perda %", yaxis="y2"))
    fig.update_layout(
        title="Risco e Robustez - Evolução Diária",
        template="plotly_white",
        height=380,
        legend=dict(orientation="h"),
        yaxis=dict(title="PF"),
        yaxis2=dict(title="%", overlaying="y", side="right"),
        margin=dict(l=40, r=40, t=60, b=40),
    )
    return fig

# Layout
app.layout = html.Div([
    html.H1("Painel de Controle do Robô", style={"textAlign": "center"}),

    html.Div([
        html.Label("Período SMA Curta:"),
        dcc.Slider(
            id="sma-short-slider", min=5, max=50, step=1, value=10,
            marks={i: str(i) for i in range(5, 51, 5)}
        ),
        html.Label("Período SMA Longa:"),
        dcc.Slider(
            id="sma-long-slider", min=20, max=200, step=10, value=50,
            marks={i: str(i) for i in range(20, 201, 20)}
        ),
        html.Label("Período RSI:"),
        dcc.Slider(
            id="rsi-slider", min=5, max=50, step=1, value=14,
            marks={i: str(i) for i in range(5, 51, 5)}
        ),
        html.Label("Par de negociação:"),
        dcc.Dropdown(
            id="symbol-dropdown",
            options=[
                {"label": "Bitcoin (BTCUSDT)", "value": "BTCUSDT"},
                {"label": "Ethereum (ETHUSDT)", "value": "ETHUSDT"}
            ],
            value="BTCUSDT"
        )
    ], style={"width": "50%", "margin": "auto"}),

    html.Div([
        dcc.Graph(id="candlestick-chart", style={"height": "400px"}),
        dcc.Graph(id="indicators-chart", style={"height": "400px"})
    ]),
    html.Hr(),
    html.H3("Relatorio Diario Walk-Forward", style={"textAlign": "center"}),
    dcc.Interval(id="daily-report-interval", interval=30 * 1000, n_intervals=0),
    html.Pre(
        id="daily-report-content",
        style={
            "whiteSpace": "pre-wrap",
            "fontSize": "12px",
            "backgroundColor": "#f6f8fa",
            "padding": "12px",
            "border": "1px solid #ddd",
            "maxHeight": "420px",
            "overflowY": "auto",
            "margin": "20px",
        },
    ),
    html.Hr(),
    html.H3("Eventos Críticos (Estruturados)", style={"textAlign": "center"}),
    html.Pre(
        id="critical-events-content",
        style={
            "whiteSpace": "pre-wrap",
            "fontSize": "12px",
            "backgroundColor": "#fff8f8",
            "padding": "12px",
            "border": "1px solid #f0c2c2",
            "maxHeight": "280px",
            "overflowY": "auto",
            "margin": "20px",
        },
    ),
    html.Hr(),
    html.H3("Painel de Risco e Robustez", style={"textAlign": "center"}),
    html.Pre(
        id="risk-robustness-kpis",
        style={
            "whiteSpace": "pre-wrap",
            "fontSize": "12px",
            "backgroundColor": "#f7fbff",
            "padding": "12px",
            "border": "1px solid #c7defa",
            "margin": "20px",
        },
    ),
    dcc.Graph(id="risk-robustness-chart", style={"height": "400px"}),
])

# Placeholder para callbacks (substitua se quiser dados reais)
@app.callback(
    [Output("candlestick-chart", "figure"), Output("indicators-chart", "figure")],
    [Input("symbol-dropdown", "value"), Input("sma-short-slider", "value"),
     Input("sma-long-slider", "value"), Input("rsi-slider", "value")]
)
def update_charts(symbol, sma_short, sma_long, rsi_period):
    fig = go.Figure()
    return fig, fig


@app.callback(Output("daily-report-content", "children"), [Input("daily-report-interval", "n_intervals")])
def update_daily_report(_n):
    return _read_latest_daily_report()


@app.callback(Output("critical-events-content", "children"), [Input("daily-report-interval", "n_intervals")])
def update_critical_events(_n):
    return _read_critical_events_tail(50)


@app.callback(
    [Output("risk-robustness-kpis", "children"), Output("risk-robustness-chart", "figure")],
    [Input("daily-report-interval", "n_intervals")],
)
def update_risk_robustness(_n):
    df = _load_daily_csv_rows()
    return _build_risk_robustness_summary(df), _build_risk_robustness_figure(df)

def run_dash():
    app.run(debug=False, use_reloader=False)

if __name__ == "__main__":
    run_dash()
