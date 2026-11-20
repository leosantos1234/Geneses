import os
import sys
import pickle
import logging
import socket
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
import numpy as np
import pandas as pd
from fastapi import FastAPI, Request
from dotenv import load_dotenv
import uvicorn

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# ======================================================
# 🔧 CARREGAR .ENV PASSADO COMO ARGUMENTO
# ======================================================

if len(sys.argv) > 1:
    env_path = os.path.abspath(sys.argv[1])
else:
    env_path = os.path.abspath(".env")

print(f"🔎 Carregando ENV: {env_path}")
load_dotenv(env_path)

# Must be set before importing TensorFlow to take effect.
if "TF_ENABLE_ONEDNN_OPTS" in os.environ:
    os.environ["TF_ENABLE_ONEDNN_OPTS"] = os.environ["TF_ENABLE_ONEDNN_OPTS"]

from tensorflow.keras.models import load_model

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="LSTM Next Candle Service")

MODEL_PATH = os.getenv("MODEL_PATH")
SCALER_PATH = os.getenv("SCALER_PATH")
SCALER_TARGET = os.getenv("SCALER_TARGET")
PORT = int(os.getenv("LSTM_SERVICE_PORT", 8505))
ASSET = Path(MODEL_PATH or "lstm_unknown_next.keras").stem.replace("lstm_", "").replace("_next", "").lower()
AUTO_RETRAIN_ENABLED = os.getenv("LSTM_AUTO_RETRAIN_ENABLED", "1") == "1"
AUTO_RETRAIN_AFTER = int(os.getenv("LSTM_AUTO_RETRAIN_AFTER", "3"))
AUTO_RETRAIN_COOLDOWN = int(os.getenv("LSTM_RETRAIN_COOLDOWN_SECONDS", "21600"))
CANDIDATE_DIR = Path(os.getenv("LSTM_RETRAIN_CANDIDATE_DIR", str(Path(__file__).parent / ".lstm_candidates")))
RETRAIN_SCRIPT = Path(__file__).with_name("auto_retrain_lstm.py")
_artifact_lock = threading.RLock()
_retrain_process = None
_retrain_log_handle = None
_retrain_state = {"status": "idle", "asset": ASSET, "reason": None, "started_at": None, "finished_at": None, "error": None}
_range_hits = 0
_last_retrain_start = 0.0


@app.get("/health")
def health():
    return {
        "status": "healthy" if "modelo" in globals() else "degraded",
        "service": "lstm",
        "asset": ASSET,
        "model_loaded": "modelo" in globals(),
        "auto_retrain": _retrain_state.copy(),
    }

logger.info(f"📁 MODEL_PATH = {MODEL_PATH}")
logger.info(f"📁 SCALER_PATH = {SCALER_PATH}")
logger.info(f"📁 SCALER_TARGET = {SCALER_TARGET}")
logger.info(f"🌐 Porta: {PORT}")

expected_env = os.getenv("ENV_FILE")
if expected_env and os.path.basename(env_path) != expected_env:
    logger.warning(
        "ENV_FILE divergente | esperado=%s | carregado=%s",
        expected_env,
        os.path.basename(env_path),
    )


def is_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def find_next_free_port(host: str, start_port: int, max_tries: int = 20):
    for p in range(start_port + 1, start_port + 1 + max_tries):
        if is_port_free(host, p):
            return p
    return None

# ======================================================
# 1️⃣ Carregar modelo e scalers
# ======================================================
def load_artifacts(model_path=MODEL_PATH, features_path=SCALER_PATH, target_path=SCALER_TARGET):
    return (
        load_model(model_path),
        pickle.load(open(features_path, "rb")),
        pickle.load(open(target_path, "rb")),
    )


try:
    modelo, scaler_features, scaler_target = load_artifacts()
    logger.info("✅ Modelo e scalers carregados com sucesso!")
except Exception as e:
    logger.error(f"❌ ERRO ao carregar modelo/scalers: {e}")
    raise e


def _candidate_paths():
    candidate = CANDIDATE_DIR / ASSET
    return candidate, candidate / f"lstm_{ASSET}_next.keras", candidate / f"scaler_{ASSET}_features.pkl", candidate / f"scaler_{ASSET}_target.pkl", candidate / "manifest.json"


def _activate_candidate():
    candidate, candidate_model, candidate_features, candidate_target, manifest_path = _candidate_paths()
    if not manifest_path.exists() or not candidate_model.exists() or not candidate_features.exists() or not candidate_target.exists():
        raise FileNotFoundError("candidato incompleto")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    new_model, new_features, new_target = load_artifacts(str(candidate_model), str(candidate_features), str(candidate_target))
    backup_dir = Path(MODEL_PATH).parent / ".lstm_backups" / ASSET / time.strftime("%Y%m%d_%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in (MODEL_PATH, SCALER_PATH, SCALER_TARGET):
        if path and os.path.exists(path):
            shutil.copy2(path, backup_dir / Path(path).name)
    # Publicação individual com os arquivos temporários no mesmo diretório;
    # os backups acima permitem rollback e nenhum arquivo parcial é deixado.
    publications = (
        (candidate_model, Path(MODEL_PATH)),
        (candidate_features, Path(SCALER_PATH)),
        (candidate_target, Path(SCALER_TARGET)),
    )
    for source, destination in publications:
        temporary = destination.with_name(destination.name + ".retrain.tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    with _artifact_lock:
        global modelo, scaler_features, scaler_target
        modelo, scaler_features, scaler_target = new_model, new_features, new_target
    logger.info("✅ Candidato LSTM ativado | ativo=%s | mape=%.4f%% | backup=%s", ASSET, float(manifest.get("mape_pct", 0)), backup_dir)
    return manifest


def _poll_retrain():
    global _retrain_process, _retrain_log_handle, _retrain_state
    if _retrain_process is None:
        return
    code = _retrain_process.poll()
    if code is None:
        return
    try:
        if code != 0:
            retrain_log = _retrain_state.get("log_file")
            detail = ""
            if retrain_log and Path(retrain_log).exists():
                lines = Path(retrain_log).read_text(encoding="utf-8", errors="replace").splitlines()
                detail = " | " + "\\n".join(lines[-12:]) if lines else ""
            raise RuntimeError(f"processo terminou com codigo {code}{detail}")
        manifest = _activate_candidate()
        _retrain_state.update(status="activated", finished_at=time.time(), error=None, manifest=manifest)
    except Exception as exc:
        logger.exception("❌ Retreinamento rejeitado/nao ativado: %s", exc)
        _retrain_state.update(status="failed", finished_at=time.time(), error=str(exc))
    finally:
        if _retrain_log_handle is not None:
            _retrain_log_handle.close()
            _retrain_log_handle = None
        _retrain_process = None


def _maybe_start_retrain(reason: str):
    global _retrain_process, _retrain_log_handle, _last_retrain_start, _range_hits, _retrain_state
    _poll_retrain()
    if not AUTO_RETRAIN_ENABLED or _range_hits < AUTO_RETRAIN_AFTER or _retrain_process is not None:
        return
    now = time.time()
    if now - _last_retrain_start < AUTO_RETRAIN_COOLDOWN:
        return
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    candidate_dir = CANDIDATE_DIR / ASSET
    if candidate_dir.exists():
        shutil.rmtree(candidate_dir)
    cmd = [sys.executable, str(RETRAIN_SCRIPT), "--asset", ASSET, "--output-dir", str(CANDIDATE_DIR)]
    log_dir = Path(os.getenv("LSTM_RETRAIN_LOG_DIR", str(Path(__file__).parent.parent / "logs" / "lstm")))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{ASSET}_retrain.log"
    _retrain_log_handle = log_file.open("a", encoding="utf-8", buffering=1)
    _retrain_log_handle.write(f"\\n--- inicio retreinamento {time.strftime('%Y-%m-%d %H:%M:%S')} ---\\n")
    _retrain_process = subprocess.Popen(cmd, stdout=_retrain_log_handle, stderr=subprocess.STDOUT, text=True)
    _last_retrain_start = now
    _range_hits = 0
    _retrain_state = {"status": "running", "asset": ASSET, "reason": reason, "started_at": now, "finished_at": None, "error": None, "log_file": str(log_file)}
    logger.warning("🔄 Retreinamento automático iniciado | ativo=%s | motivo=%s", ASSET, reason)



# ======================================================
# 2️⃣ Endpoint de Previsão
# ======================================================
@app.post("/predict")
async def predict(request: Request):
    global _range_hits
    payload = await request.json()
    data = payload.get("data", None)

    if data is None:
        return {"erro": "Nenhum campo 'data' enviado."}

    # Criar DataFrame com 5 colunas
    df = pd.DataFrame(data, columns=["open", "high", "low", "close", "volume"])

    # Verificar shape
    if df.shape != (10, 5):
        return {"erro": f"Shape inválido. Esperado (10,5), recebido {df.shape}"}

    try:
        df_model = df[["open", "high", "low", "close", "volume"]]

        data_min = getattr(scaler_features, "data_min_", None)
        data_max = getattr(scaler_features, "data_max_", None)
        out_of_range = False
        if data_min is not None and data_max is not None:
            df_min = df_model.min().values
            df_max = df_model.max().values
            below = df_min < data_min
            above = df_max > data_max
            out_of_range = bool(np.any(below) or np.any(above))
            if out_of_range:
                _range_hits += 1
                logger.warning(
                    "Input fora do range do scaler | below=%s above=%s",
                    below.tolist(),
                    above.tolist(),
                )
                if os.getenv("LSTM_LOG_RANGE_DETAILS", "0") == "1":
                    logger.warning(
                        "Input range details | min=%s max=%s",
                        df_min.tolist(),
                        df_max.tolist(),
                    )
                    logger.warning(
                        "Scaler range details | min=%s max=%s",
                        data_min.tolist(),
                        data_max.tolist(),
                    )
                if os.getenv("LSTM_CLIP_TO_SCALER_RANGE", "0") == "1":
                    df_model = df_model.clip(lower=data_min, upper=data_max, axis=1)
                    logger.warning("Input clipado ao range do scaler")

        scaled = scaler_features.transform(df_model)
        X = scaled.reshape((1, 10, 5))

    except Exception as e:
        return {"erro": f"Erro ao aplicar scaler_features: {e}"}


    # Fazer previsão
    # Fazer previsão
    try:
        with _artifact_lock:
            pred_scaled = modelo.predict(X, verbose=0)
            pred = scaler_target.inverse_transform(pred_scaled)[0][0]

        current_close = float(df["close"].iloc[-1])
        delta = pred - current_close

        # ATR implícito simples da janela
        atr_proxy = np.mean(np.abs(np.diff(df["close"].values)))
        raw_confidence = abs(delta) / max(atr_proxy, 1e-8)
        conf_cap = float(os.getenv("LSTM_CONFIDENCE_CAP", 10.0))
        confidence = min(raw_confidence, conf_cap)

        direction = "up" if delta > 0 else "down"

    except Exception as e:
        return {"erro": f"Erro ao prever: {e}"}

    logger.info(
        f"🔮 LSTM | close_atual={current_close:.2f} | "
        f"previsto={pred:.2f} | delta={delta:.2f} | conf={confidence:.4f}"
    )

    _maybe_start_retrain("input_out_of_scaler_range") if out_of_range else _poll_retrain()
    return {
        "predicted_price": float(pred),
        "current_close": float(current_close),
        "delta": float(delta),
        "direction": str(direction),
        "confidence": float(confidence),
        "out_of_range": bool(out_of_range)
    }


@app.get("/retrain-status")
def retrain_status():
    _poll_retrain()
    return _retrain_state.copy()






# ======================================================
# 3️⃣ Rodar servidor
# ======================================================
if __name__ == "__main__":
    host = os.getenv("LSTM_SERVICE_HOST", "0.0.0.0")
    allow_fallback = os.getenv("LSTM_ALLOW_PORT_FALLBACK", "0") == "1"
    final_port = PORT

    if not is_port_free(host, PORT):
        if allow_fallback:
            alt_port = find_next_free_port(host, PORT)
            if alt_port is not None:
                final_port = alt_port
                logger.warning(
                    "Porta %s ocupada. Usando fallback na porta %s. "
                    "Atualize LSTM_API_URL se necessario.",
                    PORT,
                    final_port,
                )
            else:
                logger.error(
                    "Porta %s ocupada e nao foi encontrada porta livre no fallback.",
                    PORT,
                )
                raise SystemExit(1)
        else:
            logger.error(
                "Porta %s ocupada. Finalize o processo da porta ou altere "
                "LSTM_SERVICE_PORT no .env. Opcional: LSTM_ALLOW_PORT_FALLBACK=1.",
                PORT,
            )
            raise SystemExit(1)

    logger.info(f"Iniciando servico LSTM na porta {final_port} ...")
    uvicorn.run(app, host=host, port=final_port)
