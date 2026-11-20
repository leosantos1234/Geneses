import sqlite3
import pandas as pd
import dash
from dash import dcc, html, dash_table
import plotly.graph_objs as go
from dash.dependencies import Input, Output

# ✅ Conecta ao banco e carrega os trades
def carregar_trades():
    conn = sqlite3.connect("trades.db")
    df = pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp DESC", conn)
    conn.close()

    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["profit_loss"] = df["profit_loss"].astype(float)
        df["side"] = df["side"].str.upper()
        df["lucro_acumulado"] = df["profit_loss"].cumsum()
    return df

# ✅ Inicializa o app Dash
app = dash.Dash(__name__)
app.title = "Painel de Trades - ROBO_TRADE"

app.layout = html.Div([
    html.H1("📈 Painel de Trades - ROBO_TRADE", style={"textAlign": "center"}),

    dcc.Interval(
        id="intervalo-refresh",
        interval=10 * 1000,  # 10 segundos
        n_intervals=0
    ),

    html.Div([
        dcc.Graph(id="grafico_lucro"),
        dash_table.DataTable(
            id="tabela_trades",
            columns=[
                {"name": "⏱️ Timestamp", "id": "timestamp"},
                {"name": "🔁 Side", "id": "side"},
                {"name": "📊 Estratégia", "id": "strategy"},
                {"name": "🎯 Entrada", "id": "entry_price"},
                {"name": "🏁 Saída", "id": "exit_price"},
                {"name": "💰 Lucro/Prejuízo", "id": "profit_loss"},
            ],
            style_cell={"textAlign": "center"},
            style_data_conditional=[
                {
                    "if": {"column_id": "profit_loss", "filter_query": "{profit_loss} < 0"},
                    "backgroundColor": "#FFCCCC",
                    "color": "black"
                },
                {
                    "if": {"column_id": "profit_loss", "filter_query": "{profit_loss} >= 0"},
                    "backgroundColor": "#CCFFCC",
                    "color": "black"
                }
            ],
            style_header={"backgroundColor": "#f2f2f2", "fontWeight": "bold"},
            page_size=15
        )
    ], style={"width": "90%", "margin": "auto"})
])


@app.callback(
    [Output("grafico_lucro", "figure"),
     Output("tabela_trades", "data")],
    Input("intervalo-refresh", "n_intervals")
)
def atualizar_painel(_):
    df = carregar_trades()

    if df.empty:
        return go.Figure(), []

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["timestamp"],
        y=df["lucro_acumulado"],
        mode="lines+markers",
        line=dict(color="green"),
        name="Lucro Acumulado"
    ))
    fig.update_layout(title="💸 Lucro Acumulado", xaxis_title="Data/Hora", yaxis_title="USDT")

    return fig, df.to_dict("records")


if __name__ == "__main__":
    app.run_server(debug=True)
