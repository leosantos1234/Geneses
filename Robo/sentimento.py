#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Neste script, não há importações externas necessárias.

def analisar_sentimento(texto):
    """
    Exemplo básico de análise de sentimento: 
    conta palavras positivas e negativas para retornar um score.
    """
    print("DEBUG: analisar_sentimento recebeu:", texto, "do tipo", type(texto))
    if not isinstance(texto, str):
        texto = str(texto)
        print("DEBUG: Convertido para string:", texto)
    
    palavras_positivas = ["bom", "excelente", "positivo", "alta", "crescimento"]
    palavras_negativas = ["ruim", "péssimo", "negativo", "baixa", "queda"]

    score = 0
    texto_lower = texto.lower()
    for palavra in palavras_positivas:
        if palavra in texto_lower:
            score += 1
    for palavra in palavras_negativas:
        if palavra in texto_lower:
            score -= 1
    return score

if __name__ == '__main__':
    # Teste com uma string válida
    teste_string = "Bitcoin está em alta com excelente crescimento"
    resultado_string = analisar_sentimento(teste_string)
    print("Resultado para string válida:", resultado_string)

    # Teste com um número (int)
    teste_int = 12345
    resultado_int = analisar_sentimento(teste_int)
    print("Resultado para int:", resultado_int)

    # Teste com outra string que contém números e palavras
    teste_misto = "Bitcoin: 12345, mercado em alta e crescimento positivo"
    resultado_misto = analisar_sentimento(teste_misto)
    print("Resultado para string mista:", resultado_misto)
