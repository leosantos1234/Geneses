from .ensemble import carregar_modelos, prever_acoes
import logging

def validar_com_ensemble(df):
    try:
        carregar_modelos()
        previsoes, acuracia = prever_acoes(df)

        if previsoes is None or acuracia is None:
            logging.warning("⚠️ Ensemble não conseguiu prever ações.")
            return False

        logging.info(f"🎯 Ensemble: acurácia = {acuracia:.2f}, última previsão = {previsoes[-1]}")
        return previsoes[-1] == 1  # 1 indica 'comprar'

    except Exception as e:
        logging.error(f"❌ Erro na validação com ensemble: {e}")
        return False
