from utils.logger_config import configurar_logger_global
configurar_logger_global("trading_bot_geneses.log")  # ✅ define saída para arquivo + console

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




def carregar_dados(filename='market_data.csv'):
    logger_ia.info("🔍 Verificando dados processados...")

    if os.path.exists('preprocessed_data.csv'):
        logger_ia.info("📂 Dados pré-processados encontrados. Carregando...")
        df = pd.read_csv('preprocessed_data.csv')
    else:
        logger_ia.info("📥 Carregando dados brutos e processando...")
        df = pd.read_csv(filename)
        df = calculate_indicators(df)

        if not validar_indicadores_com_reset(df):
            logger_ia.error("❌ Falha na validação dos indicadores.")
            return None

        df.dropna(inplace=True)

        if os.path.exists('scaler.pkl'):
            scaler = joblib.load('scaler.pkl')
            logger_ia.info("✅ Scaler carregado.")
        else:
            scaler = MinMaxScaler()
            scaler.fit(df[['close', 'volume', 'SMA_10', 'SMA_50', 'RSI', 'ATR']])
            joblib.dump(scaler, 'scaler.pkl')
            logger_ia.info("📦 Scaler salvo.")

        df[['close', 'volume', 'SMA_10', 'SMA_50', 'RSI', 'ATR']] = scaler.transform(
            df[['close', 'volume', 'SMA_10', 'SMA_50', 'RSI', 'ATR']]
        )
        df['target'] = (df['close'].shift(-1) > df['close']).astype(int)
        df.dropna(inplace=True)

        df.to_csv('preprocessed_data.csv', index=False)
        logger_ia.info("📁 Dados pré-processados salvos.")

    return df





def treinar_modelo(X, y, algorithm='RandomForest', salvar_relatorio=False):
    import traceback

    try:
        logging.info("🔄 Iniciando verificação de dados para o treinamento...")

        if X is None or y is None:
            logging.error("❌ X ou y está None. Abortando.")
            return None, None, None, None

        if len(X) == 0 or len(y) == 0:
            logging.error("❌ X ou y está vazio. Abortando.")
            return None, None, None, None

        if not isinstance(X, pd.DataFrame):
            logging.error("❌ X não é um DataFrame.")
            return None, None, None, None

        if not isinstance(y, pd.Series):
            logging.warning("⚠️ y não é uma Series. Tentando converter...")
            y = pd.Series(y)

        logging.info(f"🔢 Formato de X: {X.shape}, y: {y.shape}")
        logging.debug(f"📋 Colunas de X: {list(X.columns)}")

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

        logging.info(f"⚡ Iniciando busca por hiperparâmetros ({n_iter} iterações)...")
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
        logging.info(f"🏆 Melhores parâmetros: {search.best_params_}")
        y_pred = melhor_modelo.predict(X_test)
        acuracia = accuracy_score(y_test, y_pred)
        logging.info(f"🎯 Acurácia: {acuracia:.4f}")
        logger_ia.info("📄 Classificação:\n" + classification_report(y_test, y_pred))
        logging.info("🔢 Confusão:\n" + str(confusion_matrix(y_test, y_pred)))

        # Salvando relatório
        if salvar_relatorio:
            resultado = {
                "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
                "modelo": algorithm,
                "best_params": search.best_params_,
                "acuracia": round(acuracia, 4),
                "total_amostras": len(X),
                "relatorio": classification_report(y_test, y_pred),
                "matriz_confusao": confusion_matrix(y_test, y_pred).tolist()
            }
            rel_path = f"relatorio_treinamento_{resultado['timestamp']}.json"
            with open(rel_path, "w") as f:
                json.dump(resultado, f, indent=4)
            logging.info(f"📁 Relatório salvo em {rel_path}")

        return melhor_modelo, X_test, y_test, y_pred

    except Exception as e:
        logging.error("❌ Erro completo no treinamento:\n" + traceback.format_exc())
        return None, None, None, None


def carregar_modelo_com_fallback(verbose=False):
    """
    Tenta carregar o modelo padrão 'modelo_treinado.pkl' ou o mais recente com timestamp.
    """
    modelo_padrao = "modelo_treinado.pkl"

    if os.path.exists(modelo_padrao):
        try:
            modelo = joblib.load(modelo_padrao)
            if verbose:
                logging.info("✅ Modelo padrão carregado com sucesso.")
            return modelo
        except Exception as e:
            logging.warning(f"⚠️ Falha ao carregar modelo padrão: {e}")

    # Fallback: buscar o mais recente
    arquivos = glob.glob("modelo_treinado_*.pkl")
    if arquivos:
        mais_recente = max(arquivos, key=os.path.getmtime)
        try:
            modelo = joblib.load(mais_recente)
            if verbose:
                logging.info(f"⚠️ Modelo padrão ausente. Usando mais recente: {mais_recente}")
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

