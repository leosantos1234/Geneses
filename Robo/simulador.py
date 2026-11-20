import random

def sanitize_risk_params(entry_price, stop_loss, take_profit, trailing_stop, side="buy", max_dist_pct=0.1):
    max_dist = entry_price * max_dist_pct
    if stop_loss is None or stop_loss <= 0:
        stop_loss = round(entry_price * (0.995 if side.lower() == "buy" else 1.005), 2)
    if take_profit is None or take_profit <= 0:
        take_profit = round(entry_price * (1.01 if side.lower() == "buy" else 0.99), 2)
    if trailing_stop is None or trailing_stop <= 0:
        trailing_stop = max(int(entry_price * 0.00015), 1)
    if side.lower() == "buy":
        if take_profit <= entry_price:
            take_profit = round(entry_price * 1.01, 2)
        if stop_loss >= entry_price:
            stop_loss = round(entry_price * 0.995, 2)
        if abs(stop_loss - entry_price) > max_dist:
            stop_loss = round(entry_price - max_dist, 2)
    elif side.lower() == "sell":
        if take_profit >= entry_price:
            take_profit = round(entry_price * 0.99, 2)
        if stop_loss <= entry_price:
            stop_loss = round(entry_price * 1.005, 2)
        if abs(stop_loss - entry_price) > max_dist:
            stop_loss = round(entry_price + max_dist, 2)
    return stop_loss, take_profit, trailing_stop

def validar_ordem_para_envio(symbol, side, entry_price, qty, stop_loss, take_profit, trailing_offset):
    erros = []
    if stop_loss is None or take_profit is None:
        erros.append("SL/TP não pode ser None.")
    if side.lower() == "buy":
        if take_profit <= entry_price:
            erros.append(f"TP ({take_profit}) <= Preço ({entry_price})")
        if stop_loss >= entry_price:
            erros.append(f"SL ({stop_loss}) >= Preço ({entry_price})")
    elif side.lower() == "sell":
        if take_profit >= entry_price:
            erros.append(f"TP ({take_profit}) >= Preço ({entry_price})")
        if stop_loss <= entry_price:
            erros.append(f"SL ({stop_loss}) <= Preço ({entry_price})")
    if trailing_offset is None or trailing_offset < 0:
        erros.append("Trailing Stop deve ser >= 0.")
    return erros

def simular_ordem_market(entry_price, qty, acao, sl_raw, tp_raw, ts_raw, slippage_pct=0.0002):
    print(f"\nSimulando {acao.upper()} | Preço: {entry_price} | QTY: {qty} | SL: {sl_raw} | TP: {tp_raw} | TS: {ts_raw}")

    # 1️⃣ Calcula preço de execução simulando slippage de mercado
    exec_price = round(entry_price * (1 + (slippage_pct if acao == "buy" else -slippage_pct)), 2)
    print(f"Preço EXECUTADO MARKET (com slippage): {exec_price}")

    # 2️⃣ Aplica a sanitização dos parâmetros usando o preço executado
    sl, tp, ts = sanitize_risk_params(exec_price, sl_raw, tp_raw, ts_raw, acao)
    print(f"SL/TP/TS após sanitização (com preço executado): SL={sl} | TP={tp} | TS={ts}")

    # 3️⃣ Valida
    erros = validar_ordem_para_envio("BTCUSDT", acao, exec_price, qty, sl, tp, ts)
    if erros:
        print(f"❌ Proteções BLOQUEARAM ajuste: {erros}")
    else:
        print("✅ Stop/Take/Trailing seriam ENVIADOS para a posição aberta!")

    # 4️⃣ (Opcional) Simula a execução final (ex: preço bateu SL, TP ou trailing...)
    # Aqui você pode simular um resultado random (lucro/prejuízo)
    simulated_outcome = random.choice(["TP_hit", "SL_hit", "trailing_hit"])
    if simulated_outcome == "TP_hit":
        profit = round(abs(tp - exec_price), 2)
        print(f"🏁 Resultado simulado: Take-Profit atingido! Lucro: {profit}")
    elif simulated_outcome == "SL_hit":
        loss = round(abs(sl - exec_price), 2)
        print(f"🏁 Resultado simulado: Stop-Loss atingido! Prejuízo: {loss}")
    else:
        move = round(abs(exec_price - ts), 2)
        print(f"🏁 Resultado simulado: Trailing Stop ativado! Movimento capturado: {move}")

# Exemplos de cenários (incluindo casos extremos)
cenarios = [
    {"acao": "buy", "entry_price": 120_000, "qty": 0.001, "sl": 125_000, "tp": 119_000, "ts": -10},
    {"acao": "buy", "entry_price": 120_000, "qty": 0.001, "sl": None,    "tp": None,     "ts": None},
    {"acao": "sell", "entry_price": 120_000, "qty": 0.001, "sl": 115_000, "tp": 122_000, "ts": 0},
    {"acao": "sell", "entry_price": 120_000, "qty": 0.001, "sl": None,    "tp": None,    "ts": None},
    # Cenários randomizados
    *[
        {
            "acao": random.choice(["buy", "sell"]),
            "entry_price": random.randint(100_000, 140_000),
            "qty": 0.001,
            "sl": random.uniform(90_000, 150_000),
            "tp": random.uniform(90_000, 150_000),
            "ts": random.randint(-5, 200)
        }
        for _ in range(5)
    ]
]

for c in cenarios:
    simular_ordem_market(
        entry_price=c["entry_price"],
        qty=c["qty"],
        acao=c["acao"],
        sl_raw=c["sl"],
        tp_raw=c["tp"],
        ts_raw=c["ts"]
    )
