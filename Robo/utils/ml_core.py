import logging
logger = logging.getLogger(__name__)  # Logger do módulo atual

# Logger separado para eventos relacionados à IA
logger_ia = logging.getLogger("agente_ia")
logger_ia.setLevel(logging.INFO)

logger.info("📌 Módulo ml_core carregado.")


import joblib
import os
import glob
import json
from datetime import datetime
from sklearn.model_selection import train_test_split, RandomizedSearchCV
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
from utils.indicadores import calculate_indicators
from utils.validadores import validar_indicadores_com_reset


def get_market_data_filename(symbol, interval):
    return f"market_data_{symbol}_{interval}m.csv"

def get_preprocessed_data_filename(symbol, interval):
    return f"preprocessed_data_{symbol}_{interval}m.csv"

def get_scaler_filename(symbol, interval):
    return f"scaler_{symbol}_{interval}m.pkl"

def get_model_filename(symbol, interval):
    return f"modelo_treinado_{symbol}_{interval}m.pkl"

def get_relatorio_filename(symbol, interval, timestamp):
    return f"relatorio_treinamento_{symbol}_{interval}m_{timestamp}.json"


def carregar_dados_ml(symbol, interval="15"):
    market_file = get_market_data_filename(symbol, interval)
    preproc_file = get_preprocessed_data_filename(symbol, interval)
    scaler_file = get_scaler_filename(symbol, interval)

    logger_ia.info(f"🔍 Verificando pré-processado: {preproc_file}")

    if os.path.exists(preproc_file):
        df = pd.read_csv(preproc_file)
    else:
        try:
            df = pd.read_csv(market_file, encoding="utf-8", sep=",", on_bad_lines="skip")
        except UnicodeDecodeError:
            df = pd.read_csv(market_file, encoding="latin1", sep=",", on_bad_lines="skip")

        # 🔍 Validação imediata após leitura
        colunas_esperadas = ["time", "open", "high", "low", "close", "volume"]
        if not all(col in df.columns for col in colunas_esperadas):
            logger_ia.error(f"🚨 CSV inválido! Colunas esperadas: {colunas_esperadas}, encontradas: {list(df.columns)}")
            return None



        df = calculate_indicators(df)
        if not validar_indicadores_com_reset(df):
            logger_ia.error("❌ Falha na validação dos indicadores.")
            return None
        df.dropna(inplace=True)

        # SCALER POR ATIVO/TIMEFRAME!
        if os.path.exists(scaler_file):
            scaler = joblib.load(scaler_file)
            logger_ia.info("✅ Scaler carregado.")
        else:
            scaler = MinMaxScaler()
            scaler.fit(df[['close', 'volume', 'SMA_10', 'SMA_50', 'RSI', 'ATR']])
            joblib.dump(scaler, scaler_file)
            logger_ia.info(f"📦 Scaler salvo como {scaler_file}.")

        df[['close', 'volume', 'SMA_10', 'SMA_50', 'RSI', 'ATR']] = scaler.transform(
            df[['close', 'volume', 'SMA_10', 'SMA_50', 'RSI', 'ATR']]
        )
        df['target'] = (df['close'].shift(-1) > df['close']).astype(int)
        df.dropna(inplace=True)

        df.to_csv(preproc_file, index=False)
        logger_ia.info(f"📁 Dados pré-processados salvos em {preproc_file}.")

    return df


def carregar_dados(symbol, interval="15"):
    """
    Compatibilidade: retorna dados PRÉ-PROCESSADOS para ML (features).

    ATENÇÃO: o DataFrame retornado aqui pode estar normalizado/escalado e NÃO deve ser
    usado como OHLC de mercado para regime/funil/estratégias/execução.
    """
    logger_ia.warning(
        "⚠️ [DATA-GOV] carregar_dados() retorna dados ML (pré-processados). "
        "Use dados de mercado (Bybit/CSV bruto) para decisões de trading."
    )
    return carregar_dados_ml(symbol, interval)









# Função principal de treinamento
def treinar_modelo(X, y, symbol, interval, algorithm='RandomForest', salvar_relatorio=False):
    """
    Treina o modelo para o ativo/símbolo e intervalo informados.
    Salva modelo e relatório de resultados individualizados.
    """
    import traceback

    try:
        import logging
        logger = logging.getLogger(__name__)
        # Verificações de entrada
        logger.info("🔄 Iniciando verificação de dados para o treinamento...")

        if X is None or y is None:
            logger.error("❌ X ou y está None. Abortando.")
            return None, None, None, None

        if len(X) == 0 or len(y) == 0:
            logger.error("❌ X ou y está vazio. Abortando.")
            return None, None, None, None

        if not isinstance(X, (pd.DataFrame, )):
            logger.error("❌ X não é um DataFrame.")
            return None, None, None, None

        if not isinstance(y, (pd.Series, )):
            logger.warning("⚠️ y não é uma Series. Tentando converter...")
            y = pd.Series(y)

        logger.info(f"🔢 Formato de X: {X.shape}, y: {y.shape}")
        logger.debug(f"📋 Colunas de X: {list(X.columns)}")

        # Divisão dos dados
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

        # Escolha do modelo e hiperparâmetros
        if algorithm == 'RandomForest':
            modelo_base = RandomForestClassifier(random_state=42)
            parametros = {'n_estimators': [50, 100], 'max_depth': [5, 10]}
        elif algorithm == 'XGBoost':
            modelo_base = XGBClassifier(use_label_encoder=False, eval_metric='logloss', random_state=42)
            parametros = {'n_estimators': [50, 100], 'max_depth': [3, 6], 'learning_rate': [0.1, 0.2]}
        else:
            raise ValueError("❌ Algoritmo não reconhecido.")

        # Random Search
        total_combinacoes = 1
        for key in parametros:
            total_combinacoes *= len(parametros[key])
        n_iter = min(4, total_combinacoes)

        logger.info(f"⚡ Iniciando busca por hiperparâmetros ({n_iter} iterações)...")
        search = RandomizedSearchCV(
            estimator=modelo_base,
            param_distributions=parametros,
            n_iter=n_iter,
            cv=2,
            scoring='accuracy',
            n_jobs=-1,
            verbose=1
        )
        search.fit(X_train, y_train)
        melhor_modelo = search.best_estimator_

        # Resultados
        logger.info(f"🏆 Melhores parâmetros: {search.best_params_}")
        y_pred = melhor_modelo.predict(X_test)
        acuracia = accuracy_score(y_test, y_pred)
        logger.info(f"🎯 Acurácia: {acuracia:.4f}")
        logger.info("📄 Classificação:\n" + classification_report(y_test, y_pred))
        logger.info("🔢 Confusão:\n" + str(confusion_matrix(y_test, y_pred)))

        # Salva modelo individualizado
        model_file = get_model_filename(symbol, interval)
        joblib.dump(melhor_modelo, model_file)
        logger.info(f"✅ Modelo salvo como {model_file}")

        # Salvando relatório individualizado
        if salvar_relatorio:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            rel_path = get_relatorio_filename(symbol, interval, timestamp)
            resultado = {
                "timestamp": timestamp,
                "modelo": algorithm,
                "symbol": symbol,
                "interval": interval,
                "best_params": search.best_params_,
                "acuracia": round(acuracia, 4),
                "total_amostras": len(X),
                "relatorio": classification_report(y_test, y_pred),
                "matriz_confusao": confusion_matrix(y_test, y_pred).tolist()
            }
            with open(rel_path, "w") as f:
                json.dump(resultado, f, indent=4)
            logger.info(f"📁 Relatório salvo em {rel_path}")

        return melhor_modelo, X_test, y_test, y_pred

    except Exception as e:
        import traceback
        logger = logging.getLogger(__name__)
        logger.error("❌ Erro completo no treinamento:\n" + traceback.format_exc())
        return None, None, None, None


def carregar_modelo_com_fallback(symbol=None, interval=None, verbose=False):
    """
    Carrega o modelo específico para symbol/interval ou, em fallback,
    o modelo padrão ou o mais recente disponível.
    """
    # 1. Tenta carregar o modelo individual (por ativo/timeframe)
    if symbol and interval:
        model_file = get_model_filename(symbol, interval)
        if os.path.exists(model_file):
            try:
                modelo = joblib.load(model_file)
                if verbose:
                    logging.info(f"✅ Modelo específico carregado: {model_file}")
                return modelo
            except Exception as e:
                logging.warning(f"⚠️ Falha ao carregar modelo '{model_file}': {e}")

    # 2. Modelo padrão
    modelo_padrao = "modelo_treinado.pkl"
    if os.path.exists(modelo_padrao):
        try:
            modelo = joblib.load(modelo_padrao)
            if verbose:
                logging.info("✅ Modelo padrão carregado com sucesso.")
            return modelo
        except Exception as e:
            logging.warning(f"⚠️ Falha ao carregar modelo padrão: {e}")

    # 3. Fallback: buscar o mais recente
    arquivos = glob.glob("modelo_treinado_*.pkl")
    if arquivos:
        mais_recente = max(arquivos, key=os.path.getmtime)
        try:
            modelo = joblib.load(mais_recente)
            if verbose:
                logging.info(f"⚠️ Usando o modelo mais recente: {mais_recente}")
            return modelo
        except Exception as e:
            logging.error(f"❌ Erro ao carregar {mais_recente}: {e}")

    logging.error("❌ Nenhum modelo disponível para carregar.")
    return None




def validar_com_modelo(df, modelo):
    try:
        colunas = ['close', 'volume', 'SMA_10', 'SMA_50', 'RSI', 'ATR']
        if not all(col in df.columns for col in colunas):
            logger_ia.warning("⚠️ Colunas ausentes para predição.")
            return None

        entrada = df[colunas].iloc[-1:]

        if entrada.isnull().any().any():
            logger_ia.warning("⚠️ Dados incompletos para predição.")
            return None

        predicao = modelo.predict(entrada)[0]
        logger_ia.info(f"🧠 Modelo previu: {'COMPRA' if predicao == 1 else 'VENDA'}")
        return predicao

    except Exception as e:
        logger_ia.error(f"❌ Erro na predição com modelo: {e}")
        return None

