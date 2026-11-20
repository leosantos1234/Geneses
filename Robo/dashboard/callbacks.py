from dash import Input, Output
import plotly.graph_objects as go
from utils.indicadores import calculate_indicators
from utils.utils_market import get_market_data_bybit


def register_callbacks(app, session):
    @app.callback(
        [Output("candlestick-chart", "figure"),
         Output("indicators-chart", "figure")],
        [Input("symbol-dropdown", "value"),
         Input("sma-short-slider", "value"),
         Input("sma-long-slider", "value"),
         Input("rsi-slider", "value")]
    )
    def update_charts(symbol, sma_short, sma_long, rsi_period):
        df = get_market_data_bybit(symbol, session)
        if df is None:
            return go.Figure(), go.Figure()

        df = calculate_indicators(df)

        candlestick_fig = go.Figure(data=[
            go.Candlestick(
                x=df["time"],
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                name="Candlestick"
            )
        ])
        candlestick_fig.update_layout(title=f"Gráfico de Velas: {symbol}", template="plotly_white", height=400)

        indicators_fig = go.Figure()
        indicators_fig.add_trace(go.Scatter(x=df["time"], y=df["SMA_10"], mode="lines", name="SMA 10"))
        indicators_fig.add_trace(go.Scatter(x=df["time"], y=df["SMA_50"], mode="lines", name="SMA 50"))
        indicators_fig.add_trace(go.Scatter(x=df["time"], y=df["RSI"], mode="lines", name="RSI"))

        indicators_fig.update_layout(title="Indicadores Técnicos", template="plotly_white", height=400)

        return candlestick_fig, indicators_fig
