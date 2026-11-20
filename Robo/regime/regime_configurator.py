# regime_configurator.py

import os


class RegimeConfigurator:

    def __init__(self):
        # Configuração oficial do Robô Fluxo
        self.config = {
            1: {  # Tendência Forte
                "modo": "Agressivo – Tendência Forte",
                "score_min": 6, #6
                "score_pct": 55, #55
                "permissao_operar": True,
                "breakout_enabled": True,
                "breakout_min_score": 1,
                "breakout_confirmations": 2
            },
            2: {  # Tendência Moderada
                "modo": "Moderado – Tendência Moderada",
                "score_min": 5, #7
                "score_pct": 50, #65
                "permissao_operar": True,
                "breakout_enabled": True,
                "breakout_min_score": 2,
                "breakout_confirmations": 2
            },
            3: {  # Lateralidade / Range
                "modo": "Conservador – Lateralidade",
                "score_min": 999,
                "score_pct": 100,
                # Lateralidade agora permite operar, porém restrito ao Market Making.
                "permissao_operar": True,
                "breakout_enabled": False,
                "breakout_min_score": 999,
                "breakout_confirmations": 0
            },
            4: {  # Caos / Alta Volatilidade
                "modo": "Conservador – Caos",
                "score_min": 999,
                "score_pct": 100,
                "permissao_operar": False,
                "breakout_enabled": False,
                "breakout_min_score": 999,
                "breakout_confirmations": 0
            },
        }

    def get(self, regime):
        # Se vier algo inesperado → retorna regime 2 (moderado)
        base = dict(self.config.get(regime, self.config[2]))

        # Override operacional explícito: score agressivo para todos os
        # ativos/regimes elegíveis, preservando as defesas de lateralidade,
        # caos, dados insuficientes e risco.
        force_aggressive = os.getenv("FORCE_AGGRESSIVE_MODE", "false").strip().lower() in {
            "1", "true", "yes", "on"
        }
        if force_aggressive:
            base["modo"] = "Agressivo – override global"
            base["score_min"] = int(os.getenv("SCORE_MINIMO_AGRESSIVO", 4))
            base["score_pct"] = int(os.getenv("SCORE_PCT_AGRESSIVO", 45))

        return base
