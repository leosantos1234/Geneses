
import requests
from textblob import TextBlob
from openai import OpenAI
import json
import time
import os
import logging
import hashlib
from datetime import datetime, timedelta
from utils.logger_config import configurar_logger_global
from utils.cache_paths import cache_path, load_json, save_json, load_text, save_text

# Configuração global do logger
configurar_logger_global(os.getenv("NOME_LOG", "analise_sentimento.log"))
logger = logging.getLogger(__name__)
logger_ia = logging.getLogger("agente_ia")

# === CONFIGURAÇÕES ===
INTERVALO_ANALISE = timedelta(minutes=60)
FONTES = "coindesk.com,cointelegraph.com,forbes.com"
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "ef4c0249731342fc8f473616c1b83c6f")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# === ARQUIVOS DE CACHE ===
ULTIMO_TIMESTAMP_PATH = cache_path("ultimo_sentimento.txt")
SENTIMENTOS_PATH = cache_path("sentimentos_cache.json")
NOTICIAS_CACHE_PATH = cache_path("noticias_cache.json")
RESULTADO_CACHE_PATH = cache_path("sentimento_cache.json")

# === MEMÓRIA DE EXECUÇÃO ===
sentimentos_cache = {}

# === FUNÇÕES AUXILIARES DE CACHE ===
def salvar_json(dados, arquivo):
    save_json(dados, arquivo)

def carregar_json(arquivo):
    try:
        return load_json(arquivo)
    except Exception as e:
        logger.warning(f"⚠️ Falha ao ler JSON '{arquivo}': {e}")
        return None

# === CONTROLE DE TEMPO DE EXECUÇÃO ===
def salvar_ultimo_timestamp():
    save_text(str(time.time()), ULTIMO_TIMESTAMP_PATH)

def carregar_ultimo_timestamp():
    try:
        timestamp = load_text(ULTIMO_TIMESTAMP_PATH)
        if timestamp is not None:
            return float(timestamp)
    except Exception as e:
        logger.warning(f"⚠️ Erro ao ler timestamp. Gerando novo. Detalhes: {e}")

    # Se não existir ou falhar, salva o timestamp atual e retorna
    timestamp_atual = time.time()
    save_text(str(timestamp_atual), ULTIMO_TIMESTAMP_PATH)
    logger.info("🆕 Arquivo de timestamp criado automaticamente.")
    return timestamp_atual


# === SENTIMENTOS CACHE EM DISCO ===
def carregar_sentimentos_cache():
    global sentimentos_cache
    dados = carregar_json(SENTIMENTOS_PATH)
    if dados:
        sentimentos_cache.update(dados)

def salvar_sentimentos_cache():
    salvar_json(sentimentos_cache, SENTIMENTOS_PATH)



# === CAPTURA DE NOTÍCIAS COM CACHE ===
def capturar_noticias(termo, limite=10, usar_cache=True):
    # 🆕 Verifica o intervalo de tempo antes de tentar nova consulta
    agora = time.time()
    ultimo = carregar_ultimo_timestamp()

    if usar_cache and agora - ultimo < INTERVALO_ANALISE.total_seconds():
        cache = carregar_json(NOTICIAS_CACHE_PATH)
        if cache:
            logger.info("🗂️ Usando cache recente de notícias (intervalo de análise ativo)...")
            return cache

    url = "https://newsapi.org/v2/everything"
    params = {"q": termo, "domains": FONTES, "pageSize": limite, "apiKey": NEWS_API_KEY}

    for tentativa in range(3):
        try:
            response = requests.get(url, params=params)
            if response.status_code == 429:
                logger.warning("⚠️ Limite de requisições atingido. Dormindo 60s...")
                time.sleep(60)
                continue

            response.raise_for_status()
            artigos = response.json().get("articles", [])
            noticias = [{"titulo": a.get("title", ""), "descricao": a.get("description", "")} for a in artigos]

            if usar_cache:
                salvar_json(noticias, NOTICIAS_CACHE_PATH)
                salvar_ultimo_timestamp()  # 🆕 Salva timestamp atualizado

            return noticias

        except Exception as e:
            logger.error(f"❌ Tentativa {tentativa+1}/3 falhou ao obter notícias: {e}")
            time.sleep(2)

    logger.error("🚫 Falha total ao capturar notícias.")
    return []


# === ANÁLISE DE SENTIMENTO ===
def analisar_sentimento_gpt(texto):
    try:
        prompt = f"Classifique o sentimento como positivo, neutro ou negativo: {texto}"
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY nao configurada")
        response = OpenAI(
            api_key=api_key,
            timeout=float(os.getenv("OPENAI_SENTIMENT_TIMEOUT_SECONDS", "5")),
            max_retries=0,
        ).chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5-nano"),
            messages=[
                {"role": "system", "content": "Você é um analista de sentimentos."},
                {"role": "user", "content": prompt}
            ]
        )
        return (response.choices[0].message.content or "").strip().lower()
    except Exception as e:
        logger_ia.warning(f"Erro ao consultar OpenAI: {e}")
        return "neutro"

def hash_texto(texto):
    return hashlib.md5(texto.encode("utf-8")).hexdigest()

def analisar_sentimento(texto):
    h = hash_texto(texto)
    if h in sentimentos_cache:
        return sentimentos_cache[h]

    try:
        polaridade = TextBlob(texto).sentiment.polarity
        if abs(polaridade) < 0.2:
            sentimento = analisar_sentimento_gpt(texto)
        else:
            sentimento = "positivo" if polaridade > 0 else "negativo"

        sentimentos_cache[h] = sentimento
        salvar_sentimentos_cache()
        return sentimento

    except Exception as e:
        logger.warning(f"Erro ao analisar sentimento: {e}")
        return "neutro"

# === FUNÇÃO PRINCIPAL ===
def testar_sentimento():
    agora = time.time()
    ultimo = carregar_ultimo_timestamp()

    if agora - ultimo < INTERVALO_ANALISE.total_seconds():
        proxima = datetime.fromtimestamp(ultimo + INTERVALO_ANALISE.total_seconds())
        logger.info(f"⏳ Intervalo ativo. Próxima análise: {proxima.strftime('%H:%M:%S')}")
        cache = carregar_json(RESULTADO_CACHE_PATH)
        return cache if cache else "neutro"

    carregar_sentimentos_cache()
    noticias = capturar_noticias("Bitcoin")

    if not noticias:
        logger.warning("⚠️ Nenhuma notícia encontrada.")
        return "neutro"

    resultados = []
    for noticia in noticias:
        texto = f"{noticia.get('titulo', '')} - {noticia.get('descricao', '')}"
        sentimento = analisar_sentimento(texto)
        logger.info(f"📰 Texto: {texto} | Sentimento: {sentimento}")
        resultados.append(sentimento)

    sentimento_geral = (
        "positivo" if resultados.count("positivo") > resultados.count("negativo")
        else "negativo" if resultados.count("negativo") > resultados.count("positivo")
        else "neutro"
    )

    salvar_ultimo_timestamp()
    salvar_json(sentimento_geral, RESULTADO_CACHE_PATH)
    logger_ia.info(f"🧠 Sentimento final: {sentimento_geral}")
    return sentimento_geral

# Execução direta
if __name__ == "__main__":
    resultado = testar_sentimento()
    print(f"Sentimento final: {resultado}")
