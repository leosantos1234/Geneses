import requests
from textblob import TextBlob
from openai import OpenAI
import json
import time
import os
import logging
import hashlib
from datetime import datetime, timedelta
from utils.cache_paths import cache_path, load_json, save_json, load_text, save_text
from dotenv import load_dotenv
load_dotenv()

# ============================================
# 🔧 CONFIGURAÇÃO GLOBAL
# ============================================
logger = logging.getLogger(__name__)
logger_ia = logging.getLogger("agente_ia")

INTERVALO_ANALISE = timedelta(minutes=60)
FONTES = "coindesk.com,cointelegraph.com,forbes.com"
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_SENTIMENT_TIMEOUT_SECONDS = float(os.getenv("OPENAI_SENTIMENT_TIMEOUT_SECONDS", "5"))
OPENAI_CLIENT = (
    OpenAI(
        api_key=OPENAI_API_KEY,
        timeout=OPENAI_SENTIMENT_TIMEOUT_SECONDS,
        max_retries=0,
    )
    if OPENAI_API_KEY
    else None
)
OPENAI_SENTIMENT_MAX_CALLS = int(os.getenv("OPENAI_SENTIMENT_MAX_CALLS", "4"))
OPENAI_SENTIMENT_WINDOW_SECONDS = float(os.getenv("OPENAI_SENTIMENT_WINDOW_SECONDS", "60"))
OPENAI_SENTIMENT_MIN_INTERVAL = float(os.getenv("OPENAI_SENTIMENT_MIN_INTERVAL", "1.0"))
OPENAI_SENTIMENT_FAILURE_COOLDOWN_SECONDS = float(os.getenv("OPENAI_SENTIMENT_FAILURE_COOLDOWN_SECONDS", "60"))
_openai_sentiment_window_started = 0.0
_openai_sentiment_calls = 0
_openai_sentiment_last_call = 0.0
_openai_sentiment_disabled_until = 0.0

SENTIMENTOS_PATH = cache_path("sentimentos_cache.json")
RESULTADO_CACHE_PATH = cache_path("sentimento_cache.json")
TIMESTAMP_GLOBAL_PATH = cache_path("ultimo_sentimento_global.txt")

sentimentos_cache = {}


# ============================================
# 📦 FUNÇÕES AUXILIARES DE CACHE
# ============================================
def salvar_json(dados, arquivo):
    try:
        save_json(dados, arquivo)
    except Exception as e:
        logger.error(f"❌ Erro ao salvar JSON '{arquivo}': {e}")


def carregar_json(arquivo):
    try:
        return load_json(arquivo)
    except Exception as e:
        logger.warning(f"⚠️ Falha ao ler JSON '{arquivo}': {e}")
        return None


# ============================================
# 🕒 CONTROLE DE TEMPO GLOBAL
# ============================================
def carregar_timestamp_global():
    """Carrega o timestamp global compartilhado entre todos os ativos."""
    try:
        timestamp = load_text(TIMESTAMP_GLOBAL_PATH)
        if timestamp is not None:
            return float(timestamp)
    except Exception as e:
        logger.warning(f"⚠️ Erro ao ler timestamp global: {e}")
    ts = time.time()
    salvar_timestamp_global(ts)
    return ts


def salvar_timestamp_global(ts=None):
    """Salva o timestamp global usado para sincronizar múltiplos ativos."""
    ts = ts or time.time()
    try:
        save_text(str(ts), TIMESTAMP_GLOBAL_PATH)
        logger.info(f"💾 Timestamp global salvo: {datetime.fromtimestamp(ts)}")
    except Exception as e:
        logger.error(f"❌ Erro ao salvar timestamp global: {e}")


# ============================================
# 💾 SENTIMENTOS CACHE LOCAL
# ============================================
def carregar_sentimentos_cache():
    global sentimentos_cache
    dados = carregar_json(SENTIMENTOS_PATH)
    if dados:
        sentimentos_cache.update(dados)


def salvar_sentimentos_cache():
    salvar_json(sentimentos_cache, SENTIMENTOS_PATH)


# ============================================
# 📰 CAPTURA DE NOTÍCIAS COM CACHE
# ============================================
def capturar_noticias(termo, limite=10):
    """Obtém notícias da NewsAPI com cache individual por ativo."""
    if not termo or not str(termo).strip():
        raise ValueError("⚠️ Termo para captura de notícias não pode ser vazio!")
    cache_path_arquivo = cache_path(f"noticias_cache_{termo.upper()}.json")
    agora = time.time()
    ultimo_global = carregar_timestamp_global()

    # Se ainda dentro do intervalo global, usa cache
    if agora - ultimo_global < INTERVALO_ANALISE.total_seconds():
        cache = carregar_json(cache_path_arquivo)
        if cache:
            logger.info(f"🗂️ [CACHE] Usando cache de notícias para {termo}")
            return cache

    # Caso precise atualizar (somente o primeiro ativo faz isso)
    url = "https://newsapi.org/v2/everything"
    params = {"q": termo, "domains": FONTES, "pageSize": limite, "apiKey": NEWS_API_KEY}

    try:
        response = requests.get(url, params=params)
        if response.status_code == 429:
            logger.warning("⚠️ Limite de requisições atingido. Aguardando 60s...")
            time.sleep(60)
            return capturar_noticias(termo, limite)

        response.raise_for_status()
        artigos = response.json().get("articles", [])
        noticias = [{"titulo": a.get("title", ""), "descricao": a.get("description", "")} for a in artigos]

        salvar_json(noticias, cache_path_arquivo)
        salvar_timestamp_global()  # 🔹 Atualiza o controle global
        logger.info(f"✅ Cache de notícias atualizado para {termo} ({len(noticias)} itens)")
        return noticias

    except Exception as e:
        logger.error(f"❌ Erro ao capturar notícias para {termo}: {e}")
        cache = carregar_json(cache_path_arquivo)
        if cache:
            logger.warning(f"⚠️ Falha na API — usando cache anterior de {termo}")
            return cache
        else:
            logger.warning(f"⚠️ Nenhum cache disponível para {termo}, retornando vazio.")
            return []



# ============================================
# 🧠 INTERPRETAÇÃO DE RESPOSTA OPENAI
# ============================================
def interpretar_resposta_openai(resposta_texto: str) -> str:
    """
    Interpreta a resposta textual da OpenAI e converte em 'positivo', 'negativo' ou 'neutro'.
    Evita falsos positivos quando o modelo responde 'hold', 'manter' ou textos ambíguos.
    """
    if not resposta_texto:
        return "neutro"

    r = resposta_texto.lower()

    # ⚙️ Regras de prioridade
    if any(x in r for x in ["hold", "manter", "esperar", "neutro", "sem sinal"]):
        return "neutro"
    elif any(x in r for x in ["negativo", "bearish", "baixista", "queda", "pessimista"]):
        return "negativo"
    elif any(x in r for x in ["positivo", "bullish", "altista", "alta", "otimista"]):
        return "positivo"
    elif any(x in r for x in ["sell", "venda", "vender"]):
        return "negativo"
    elif any(x in r for x in ["buy", "compra", "comprar"]):
        return "positivo"

    return "neutro"


# ============================================
# 🧠 ANÁLISE DE SENTIMENTO GPT CORRIGIDA
# ============================================
def analisar_sentimento_gpt(texto):
    """
    Consulta o modelo GPT para classificação de sentimento e interpreta com segurança.
    """
    try:
        global _openai_sentiment_window_started, _openai_sentiment_disabled_until
        global _openai_sentiment_calls, _openai_sentiment_last_call

        agora = time.monotonic()
        if agora < _openai_sentiment_disabled_until:
            logger.info("OpenAI em cooldown apos falha de conexao; usando fallback neutro.")
            return "neutro"
        if (
            agora - _openai_sentiment_window_started
            >= OPENAI_SENTIMENT_WINDOW_SECONDS
        ):
            _openai_sentiment_window_started = agora
            _openai_sentiment_calls = 0

        if _openai_sentiment_calls >= OPENAI_SENTIMENT_MAX_CALLS:
            logger.warning(
                "Limite local de chamadas OpenAI para sentimento atingido "
                "(%s/%ss); usando fallback neutro.",
                OPENAI_SENTIMENT_MAX_CALLS,
                int(OPENAI_SENTIMENT_WINDOW_SECONDS),
            )
            return "neutro"

        espera = OPENAI_SENTIMENT_MIN_INTERVAL - (agora - _openai_sentiment_last_call)
        if espera > 0:
            time.sleep(espera)

        prompt = (
            "Analise o texto a seguir e classifique o sentimento geral como 'positivo', 'neutro' ou 'negativo'.\n"
            f"Texto: {texto}"
        )
        if OPENAI_CLIENT is None:
            raise RuntimeError("OPENAI_API_KEY nao configurada")
        _openai_sentiment_calls += 1
        _openai_sentiment_last_call = time.monotonic()
        response = OPENAI_CLIENT.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5-nano"),
            messages=[
                {"role": "system", "content": "Você é um analista de sentimentos especializado em textos financeiros."},
                {"role": "user", "content": prompt}
            ],
        )
        resposta_texto = (response.choices[0].message.content or "").strip()
        sentimento_final = interpretar_resposta_openai(resposta_texto)

        logger.info(f"🧠 OpenAI → {resposta_texto} → interpretado como '{sentimento_final}'")
        return sentimento_final

    except Exception as e:
        _openai_sentiment_disabled_until = time.monotonic() + OPENAI_SENTIMENT_FAILURE_COOLDOWN_SECONDS
        logger_ia.warning(
            "OpenAI bloqueada temporariamente por %.0fs; proximas noticias usarao neutro.",
            OPENAI_SENTIMENT_FAILURE_COOLDOWN_SECONDS,
        )
        logger_ia.warning(f"⚠️ Erro ao consultar OpenAI: {e}")
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
        logger.warning(f"⚠️ Erro ao analisar sentimento: {e}")
        return "neutro"


# ============================================
# 🎯 FUNÇÃO PRINCIPAL
# ============================================
def testar_sentimento(termo="Bitcoin"):
    carregar_sentimentos_cache()
    noticias = capturar_noticias(termo)
    if not noticias:
        logger.warning(f"⚠️ Nenhuma notícia encontrada para {termo}")
        return "neutro"

    resultados = []
    for noticia in noticias:
        texto = f"{noticia.get('titulo', '')} - {noticia.get('descricao', '')}"
        sentimento = analisar_sentimento(texto)
        resultados.append(sentimento)
        logger.info(f"📰 {termo}: {sentimento} | {texto}")

    positivo = resultados.count("positivo")
    negativo = resultados.count("negativo")

    sentimento_geral = (
        "positivo" if positivo > negativo
        else "negativo" if negativo > positivo
        else "neutro"
    )

    salvar_json(sentimento_geral, RESULTADO_CACHE_PATH)
    logger_ia.info(f"🧠 [{termo}] Sentimento final: {sentimento_geral}")
    return sentimento_geral


# ============================================
# 🤝 FUNÇÃO COMBINADA (BTC + ATIVOS)
# ============================================
def obter_sentimento_combinado():
    """
    Lê o ativo do .env, analisa sempre o Bitcoin como base
    e o ativo local definido no ambiente. Não exige listas fixas
    nem dicionários manuais — se amanhã houver um novo ativo,
    ele será incluído automaticamente.
    """
    # 🔹 Detecta ativo do ambiente atual (.env-btc, .env-eth etc)
    ativo_env = os.getenv("ATIVOS", "BTCUSDT").split(",")[0].strip().upper()

    # 🔹 Extrai apenas o símbolo (sem USDT, caso exista)
    termo_local = ativo_env.replace("USDT", "").strip()

    # 🔹 Normaliza o nome (usado para buscas e logs)
    nome_local = termo_local.capitalize()
    logger.info(f"🧩 Ambiente detectado: {ativo_env} → termo local '{nome_local}'")

    # 🔹 1. Sempre analisa o Bitcoin (base global de mercado)
    sentimento_btc = testar_sentimento("Bitcoin")
    mapa = {"positivo": 1, "neutro": 0, "negativo": -1}
    score_btc = mapa.get(sentimento_btc, 0)

    # 🔹 2. Analisa o ativo local (caso não seja o próprio BTC)
    if termo_local != "BTC":
        sentimento_local = testar_sentimento(nome_local)
        score_local = mapa.get(sentimento_local, 0)
        score_final = 0.7 * score_btc + 0.3 * score_local
    else:
        sentimento_local = sentimento_btc
        score_local = score_btc
        score_final = score_btc

    # 🔹 3. Determina sentimento final (ponderado)
    if score_final > 0.2:
        final = "positivo"
    elif score_final < -0.2:
        final = "negativo"
    else:
        final = "neutro"

    # 🔹 4. Registro de resultado
    logger_ia.info(
        f"🧭 [{ativo_env}] BTC={sentimento_btc} | Local={sentimento_local} | Final={final}"
    )

    # 🔹 5. Retorno para integração direta com o robô
    return final



# ============================================
# ▶️ EXECUÇÃO DIRETA
# ============================================
if __name__ == "__main__":
    resultado = obter_sentimento_combinado()
    print(f"Sentimento final combinado: {resultado}")
