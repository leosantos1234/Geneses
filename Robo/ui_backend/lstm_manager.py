"""Lifecycle helper for the local LSTM HTTP services.

The trading worker is allowed to continue in fallback mode when an LSTM is
unavailable, but the dashboard should start the configured local service
before starting a worker.  This module deliberately does not enable exchange
orders; it only manages localhost model processes.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

ROBO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_EXE = ROBO_ROOT / ".venv_local" / "Scripts" / "python.exe"
SERVICE = ROBO_ROOT / "estrategias" / "lstm_service.py"
LOG_DIR = ROBO_ROOT / "logs" / "lstm"


def _value(env_file: str | Path, key: str) -> str | None:
    try:
        for line in (ROBO_ROOT / env_file).read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].split("#", 1)[0].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def ensure_lstm_service(env_file: str | Path) -> dict[str, object]:
    """Start the service declared by *env_file* if it is not listening.

    Returns a small status dictionary suitable for logs/UI.  Missing or
    disabled LSTM configuration is intentionally a no-op.
    """
    enabled = (_value(env_file, "LSTM_ENABLED") or "1").strip().lower()
    api_url = _value(env_file, "LSTM_API_URL")
    port_raw = _value(env_file, "LSTM_SERVICE_PORT")
    model_path = _value(env_file, "MODEL_PATH")
    if enabled in {"0", "false", "no", "off"} or not api_url or not port_raw or not model_path:
        return {"status": "disabled_or_unconfigured", "env_file": str(env_file)}

    try:
        port = int(port_raw)
    except ValueError:
        return {"status": "invalid_port", "env_file": str(env_file), "port": port_raw}

    host = _value(env_file, "LSTM_SERVICE_HOST") or "127.0.0.1"
    if _port_open(host, port):
        return {"status": "already_running", "env_file": str(env_file), "port": port}
    if not PYTHON_EXE.is_file() or not SERVICE.is_file():
        return {"status": "missing_runtime", "env_file": str(env_file), "port": port}

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    asset = Path(model_path).stem.replace("lstm_", "").replace("_next", "") or "unknown"
    log_handle = (LOG_DIR / f"{asset.lower()}.log").open("a", encoding="utf-8", buffering=1)
    log_handle.write(f"\n[supervisor] iniciando LSTM env={env_file} host={host} port={port}\n")
    child_env = os.environ.copy()
    child_env["ENV_FILE"] = Path(env_file).name
    child_env["PYTHONPATH"] = f"{ROBO_ROOT};{ROBO_ROOT.parent / 'Agente_IA'}"
    # O LSTM grava stdout/stderr nos logs; nao abrir uma janela de console
    # separada evita telas pretas sem informacao ao lado dos workers.
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        [str(PYTHON_EXE), str(SERVICE), str(ROBO_ROOT / env_file)],
        cwd=ROBO_ROOT,
        env=child_env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )
    # Give uvicorn/model loading a short window to bind, but never block the
    # worker indefinitely if TensorFlow or an artifact is broken.
    # TensorFlow can need several seconds to import and load the model on a
    # cold start. Do not report a false failure while it is still starting.
    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline:
        if _port_open(host, port):
            log_handle.close()
            return {"status": "started", "env_file": str(env_file), "port": port, "pid": process.pid}
        if process.poll() is not None:
            break
        time.sleep(0.25)
    log_handle.close()
    return {"status": "start_failed", "env_file": str(env_file), "port": port, "pid": process.pid}
