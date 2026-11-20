from dash import html, dcc

DARK_STYLE = {
    "backgroundColor": "#181818",
    "color": "#E0E0E0"
}

layout = html.Div(style=DARK_STYLE, children=[
    html.H1("🚀 Painel Futurista do Robô Fluxo", style={
        "textAlign": "center",
        "marginTop": "25px",
        "marginBottom": "25px",
        "fontWeight": "bold",
        "color": "#00FFD0",
        "letterSpacing": "2px",
        "textShadow": "0px 0px 12px #00FFD0"
    }),

    html.Div([
        html.Label("Configuração de Indicadores Técnicos:", style={"fontWeight": "bold", "fontSize": "20px"}),
        html.Div([
            html.Label("Período SMA Curta:"),
            dcc.Slider(
                id="sma-short-slider",
                min=5,
                max=50,
                step=1,
                value=10,
                marks={i: str(i) for i in range(5, 51, 5)},
                tooltip={"placement": "bottom", "always_visible": True}
            ),
        ], style={"margin-bottom": "16px"}),

        html.Div([
            html.Label("Período SMA Longa:"),
            dcc.Slider(
                id="sma-long-slider",
                min=20,
                max=200,
                step=10,
                value=50,
                marks={i: str(i) for i in range(20, 201, 20)},
                tooltip={"placement": "bottom", "always_visible": True}
            ),
        ], style={"margin-bottom": "16px"}),

        html.Div([
            html.Label("Período RSI:"),
            dcc.Slider(
                id="rsi-slider",
                min=5,
                max=50,
                step=1,
                value=14,
                marks={i: str(i) for i in range(5, 51, 5)},
                tooltip={"placement": "bottom", "always_visible": True}
            ),
        ]),
    ], style={"width": "60%", "margin": "32px auto", "padding": "32px", "borderRadius": "20px", "boxShadow": "0px 2px 16px #222"}),

    html.Div([
        html.Label("Selecione o par de negociação:", style={"fontWeight": "bold", "fontSize": "16px"}),
        dcc.Dropdown(
            id="symbol-dropdown",
            options=[
                {"label": "Bitcoin (BTCUSDT)", "value": "BTCUSDT"},
                {"label": "Ethereum (ETHUSDT)", "value": "ETHUSDT"}
            ],
            value="BTCUSDT",
            style={"width": "50%", "margin": "0 auto", "color": "#111"}
        ),
    ], style={"textAlign": "center", "margin-bottom": "32px"}),

    html.Div([
        html.Div([
            html.H3("Gráfico de Velas (Candlestick)", style={
                "textAlign": "center", "color": "#00FFD0", "marginBottom": "12px"
            }),
            dcc.Graph(id="candlestick-chart", style={
                "height": "380px", "background": "#111", "borderRadius": "16px"
            })
        ], style={"margin-bottom": "32px"}),

        html.Div([
            html.H3("Indicadores Técnicos", style={
                "textAlign": "center", "color": "#00FFD0", "marginBottom": "12px"
            }),
            dcc.Graph(id="indicators-chart", style={
                "height": "320px", "background": "#111", "borderRadius": "16px"
            })
        ]),
    ], style={"marginTop": "20px", "marginBottom": "36px"}),

    html.Div([
        html.H4("Logs recentes", style={"color": "#00FFD0"}),
        html.Pre(id="log-output", style={
            "background": "#111", "color": "#0f0", "height": "160px",
            "overflowY": "scroll", "padding": "12px", "borderRadius": "12px", "fontSize": "13px"
        })
    ], style={"width": "80%", "margin": "0 auto", "marginBottom": "48px"})
])
