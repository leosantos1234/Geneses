# regime_controller.py

"""
Armazena em MEMÓRIA os parâmetros de regime por ativo.

Este módulo NÃO usa classes. Apenas variáveis e funções simples.
"""

# Estado global que guarda os parâmetros do regime para cada ativo
regime_state = {}


def aplicar(ativo: str, parametros: dict):
    """
    Grava em memória os parâmetros de regime para o ativo.

    Exemplo de 'parametros':
    {
        "modo": "agressivo",
        "score_min": 4,
        "score_pct": 45,
    }
    """
    global regime_state
    regime_state[ativo] = parametros


def get_parametros(ativo: str):
    """
    Retorna os parâmetros de regime armazenados para o ativo.
    Se não existir, retorna None.
    """
    return regime_state.get(ativo)
