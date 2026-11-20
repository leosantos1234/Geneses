def avaliar_decisao_final(
    score_base,
    score_final,
    required_score,
    breakout_confirmado,
    regime,
    setup_ok,
    contexto_ok,
):
    if breakout_confirmado and score_final >= (required_score - 1):
        signal_strength = "FORTE"
    elif score_final >= required_score:
        signal_strength = "MEDIO"
    else:
        signal_strength = "FRACO"

    if signal_strength == "FORTE":
        permitido = True
        motivo = "FORTE: breakout + score próximo"
    elif signal_strength == "MEDIO":
        permitido = True
        motivo = "MEDIO: score aprovado"
    else:
        permitido = False
        motivo = "FRACO: score insuficiente"

    if not setup_ok:
        permitido = False
        motivo = "Bloqueado: setup atual inválido"

    if not contexto_ok:
        permitido = False
        motivo = "Bloqueado: contexto inválido"

    return {
        "permitido": permitido,
        "motivo": motivo,
        "signal_strength": signal_strength,
    }
