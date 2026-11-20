# utils/risco.py

def calcular_rrr(entrada: float, sl: float, tp: float, min_rrr: float = 1.5) -> float:
    """
    Calcula e valida a relação risco/recompensa (RRR).

    :param entrada: Preço de entrada na operação.
    :param sl: Preço do Stop-Loss.
    :param tp: Preço do Take-Profit.
    :param min_rrr: Valor mínimo aceitável para a RRR.
    :return: Valor da RRR se for >= min_rrr, caso contrário retorna 0.
    """
    risco = abs(entrada - sl)
    retorno = abs(tp - entrada)
    if risco == 0:
        return 0
    rrr = retorno / risco
    return rrr if rrr >= min_rrr else 0
