import os
import logging
from openai import OpenAI

def analisar_mercado_openai(prompt):
    """
    Consulta a OpenAI para analisar o mercado.
    """
    try:
        logging.info(f"Consultando OpenAI com prompt: {prompt}")
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY nao configurada")
        response = OpenAI(
            api_key=api_key,
            timeout=float(os.getenv("OPENAI_CONTEXT_TIMEOUT_SECONDS", "5")),
            max_retries=0,
        ).chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Você é um analista financeiro experiente."},
                {"role": "user", "content": prompt},
            ]
        )
        recomendacao = response.choices[0].message.content or ""
        logging.info(f"Recomendação da OpenAI: {recomendacao}")
        return recomendacao
    except Exception as e:
        logging.error(f"Erro ao consultar OpenAI: {e}")
        return None
