from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from utils.worker_singleton import WorkerAlreadyRunning, WorkerInstanceLock
from ui_backend.lstm_manager import ensure_lstm_service

ROBO_ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROBO_ROOT / "pids" / "dashboard_workers.json"
WORKER_LOG_DIR = ROBO_ROOT / "logs" / "dashboard_workers"
ASSET_ENV_FILES = {
    "BTC": ".env-btc", "ETH": ".env-eth", "BNB": ".env-bnb", "SOL": ".env-sol", "XRP": ".env-xrp",
    "AVAX": "proximos_Ativos/.env-avax", "ADA": "proximos_Ativos/.env-ada", "LINK": "proximos_Ativos/.env-link",
    "LTC": "proximos_Ativos/.env-ltc", "DOT": "proximos_Ativos/.env-dot",
}
STRATEGIES = {
    "BREAKOUT": "Breakout", "SWING_TREND_FOLLOWING": "Swing trend following", "EARLY_TREND": "Early trend",
    "PULLBACK_TREND_FOLLOWING": "Pullback trend following",
    "MEAN_REVERSION": "Mean reversion", "MARKET_MAKING_LATERAL": "Market making lateral",
}

def _read_registry() -> dict[str, dict[str, Any]]:
    try:
        return json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

def _write_registry(registry: dict[str, dict[str, Any]]) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = PID_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, PID_FILE)

def _pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    if psutil is None:
        if os.name == "nt":
            try:
                import ctypes

                process_handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
                if process_handle:
                    ctypes.windll.kernel32.CloseHandle(process_handle)
                    return True
                return False
            except (AttributeError, OSError):
                return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError, PermissionError):
            return False
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (ImportError, psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False

def _resolve_worker_pid(launcher_pid: int, env_file: str, launched_at: float) -> int:
    """Find the long-lived Python worker when Windows creates a launcher process."""
    if _pid_alive(launcher_pid):
        return launcher_pid
    if psutil is None:
        return launcher_pid
    needle = Path(env_file).name.lower()
    try:
        candidates = []
        for process in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            info = process.info
            if int(info.get("pid", 0)) == os.getpid():
                continue
            if float(info.get("create_time", 0) or 0) < launched_at - 1:
                continue
            command_line = " ".join(info.get("cmdline") or []).lower()
            if "trading_bot_geneses.py" not in command_line or needle not in command_line:
                continue
            candidates.append((float(info.get("create_time", 0) or 0), int(info["pid"])))
        if candidates:
            return sorted(candidates, reverse=True)[0][1]
    except (OSError, ValueError, TypeError):
        pass
    return launcher_pid


def _discover_worker_pids(asset: str, env_file: str) -> list[int]:
    """Find workers started outside the dashboard, without touching lock files."""
    if psutil is None:
        return []
    needle = Path(env_file).name.lower()
    found: list[int] = []
    try:
        for process in psutil.process_iter(["pid", "cmdline"]):
            pid = int(process.info.get("pid", 0) or 0)
            if pid <= 0 or pid == os.getpid():
                continue
            command_line = " ".join(process.info.get("cmdline") or []).lower()
            if "trading_bot_geneses.py" in command_line and needle in command_line:
                found.append(pid)
    except (OSError, ValueError, TypeError):
        return []
    return sorted(set(found))

def available_assets() -> list[dict[str, str]]:
    configured = [item.strip().upper() for item in os.getenv("GENESIS_ASSET_LIST", "").split(",") if item.strip()]
    if configured:
        return [{"id": asset, "label": asset, "env_file": ASSET_ENV_FILES.get(asset, "")} for asset in configured if asset in ASSET_ENV_FILES]
    return [{"id": asset, "label": asset, "env_file": relative} for asset, relative in ASSET_ENV_FILES.items() if (ROBO_ROOT / relative).is_file()]

def status() -> list[dict[str, Any]]:
    registry = _read_registry()
    changed = False
    result = []
    for asset, item in registry.items():
        alive = _pid_alive(int(item.get("pid", 0) or 0))
        lock_active = not _lock_available(asset, item.get("env_file", ASSET_ENV_FILES.get(asset, "")))
        running = lock_active
        if not running and item.get("status") == "running":
            item["status"] = "stopped"
            item["stopped_at"] = time.time()
            changed = True
        elif running and item.get("status") != "running":
            item["status"] = "running"
            item.pop("stopped_at", None)
            changed = True
        result.append({**item, "asset": asset, "running": running, "lock_active": lock_active})
    if changed:
        _write_registry(registry)
    return sorted(result, key=lambda item: item["asset"])

def options() -> dict[str, Any]:
    return {"assets": available_assets(), "strategies": [{"id": key, "label": label} for key, label in STRATEGIES.items()], "workers": status()}

def _validate_assets(assets: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(str(asset).strip().upper() for asset in assets))
    invalid = [asset for asset in normalized if asset not in ASSET_ENV_FILES or not (ROBO_ROOT / ASSET_ENV_FILES[asset]).is_file()]
    if invalid:
        raise ValueError(f"Ativo(s) indisponível(is): {', '.join(invalid)}")
    return normalized

def _profile_value(relative_env: str, key: str) -> str | None:
    try:
        for line in (ROBO_ROOT / relative_env).read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith(f"{key}="):
                return line.split("=", 1)[1].split("#", 1)[0].strip().strip('"').strip("'")
    except OSError:
        return None
    return None

def _worker_symbol(asset: str, relative_env: str) -> str:
    return _profile_value(relative_env, "ATIVOS") or f"{asset}USDT"

def _lock_available(asset: str, relative_env: str) -> bool:
    """Probe the same OS lock used by the worker without deleting its file."""
    lock = WorkerInstanceLock(_worker_symbol(asset, relative_env))
    try:
        lock.acquire()
    except WorkerAlreadyRunning:
        return False
    lock.release()
    return True


def _worker_running(asset: str, relative_env: str, _pid: int) -> bool:
    """Use the symbol lock as authority; Windows PIDs can be reused."""
    return not _lock_available(asset, relative_env)


def _startup_watchdog(asset: str, env_file: str, launched_at: float, launcher_pid: int, timeout_seconds: float) -> None:
    """Encerra um worker que nao chegou ao primeiro heartbeat no prazo."""
    symbol = _worker_symbol(asset, env_file)
    heartbeat = ROBO_ROOT / "runtime" / "heartbeats" / f"{''.join(c if c.isalnum() else '_' for c in symbol.upper())}.json"
    deadline = time.time() + max(15.0, timeout_seconds)
    while time.time() < deadline:
        try:
            payload = json.loads(heartbeat.read_text(encoding="utf-8"))
            if float(payload.get("timestamp", 0.0)) >= launched_at and str(payload.get("symbol", "")).upper() == symbol.upper():
                return
        except (FileNotFoundError, OSError, ValueError, TypeError):
            pass
        time.sleep(2.0)

    registry = _read_registry()
    item = registry.get(asset, {})
    if item.get("status") != "running" or float(item.get("started_at", 0.0) or 0.0) != float(launched_at):
        return
    pids = {int(launcher_pid or 0), int(item.get("pid", 0) or 0)}
    for pid in sorted(pid for pid in pids if pid > 0):
        if _pid_alive(pid):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
    item["status"] = "stopped"
    item["running"] = False
    item["startup_timeout"] = True
    item["startup_timeout_seconds"] = timeout_seconds
    item["stopped_at"] = time.time()
    registry[asset] = item
    _write_registry(registry)

def start(assets: list[str], strategy: str) -> list[dict[str, Any]]:
    selected_assets = _validate_assets(assets)
    selected_strategy = str(strategy or "").strip().upper()
    if selected_strategy not in STRATEGIES:
        raise ValueError("Estratégia inválida")
    registry = _read_registry()
    started = []
    for asset in selected_assets:
        existing = registry.get(asset, {})
        existing_pid = int(existing.get("pid", 0) or 0)
        if existing.get("status") == "running" and _worker_running(asset, ASSET_ENV_FILES[asset], existing_pid):
            started.append({**existing, "asset": asset, "running": True, "already_running": True})
            continue
        env_file = ASSET_ENV_FILES[asset]
        lstm_status = ensure_lstm_service(env_file)
        if not _lock_available(asset, env_file):
            raise ValueError(f"Worker do ativo {asset} jÃ¡ estÃ¡ em execuÃ§Ã£o; inÃ­cio duplicado bloqueado")
        python_exe = ROBO_ROOT / ".venv_local" / "Scripts" / "python.exe"
        if not python_exe.is_file():
            raise ValueError("Ambiente Python do projeto não encontrado")
        WORKER_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = WORKER_LOG_DIR / f"{asset.lower()}.log"
        worker_env = os.environ.copy()
        worker_env["GENESIS_STRATEGY_SELECTION"] = selected_strategy
        if selected_strategy == "MARKET_MAKING_LATERAL":
            # A seleção explícita do worker tem precedência sobre o legado
            # BREAKOUT_ONLY presente em perfis antigos, como o .env-btc.
            worker_env["BREAKOUT_ONLY"] = "0"
        if selected_strategy == "SWING_AND_PULLBACK":
            worker_env["GENESIS_STRATEGY_SELECTION"] = "SWING_AND_PULLBACK"
            worker_env["SWING_ENABLED"] = "1"
            worker_env["PULLBACK_ENABLED"] = "1"
            worker_env["SWING_HISTORICAL_BACKTEST_ENABLED"] = "1"
            worker_env["PULLBACK_HISTORICAL_BACKTEST_ENABLED"] = "1"
            worker_env["SWING_LIVE_EXECUTOR_ENABLED"] = "1"
            worker_env["PULLBACK_LIVE_EXECUTOR_ENABLED"] = "1"
        if selected_strategy == "PULLBACK_TREND_FOLLOWING":
            worker_env["PULLBACK_ENABLED"] = "1"
            worker_env["PULLBACK_HISTORICAL_BACKTEST_ENABLED"] = "1"
        worker_env["GENESIS_LOG_FILE"] = str(log_path)
        # O worker inicializa o logger antes de carregar o ENV do ativo. Como
        # o dashboard pode ser iniciado em um shell sem o .env.ui carregado,
        # herdar explicitamente apenas os parâmetros operacionais do banco
        # evita que o processo encerre antes do primeiro ciclo.
        ui_env_path = ROBO_ROOT / ".env.ui"
        try:
            for line in ui_env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                key = key.strip()
                if key in {"GENESIS_DATABASE_BACKEND", "GENESIS_DATABASE_URL", "GENESIS_SQLITE_PATH"}:
                    worker_env[key] = value.strip().strip('"').strip("'")
        except OSError:
            pass
        active_exchange = _profile_value(env_file, "ACTIVE_EXCHANGE")
        if active_exchange:
            worker_env["ACTIVE_EXCHANGE"] = active_exchange
        worker_env["PYTHONPATH"] = f"{ROBO_ROOT};{ROBO_ROOT.parent / 'Agente_IA'}"
        # Acesso direto à OKX: proxies herdados do Windows/shell já causaram
        # ProxyError e abriram o circuit breaker antes do primeiro candle.
        for proxy_name in list(worker_env):
            if "PROXY" in proxy_name.upper():
                worker_env[proxy_name] = ""
        worker_env["NO_PROXY"] = "*"
        worker_env["no_proxy"] = "*"
        # A API carrega a URL operacional a partir do .env.ui. O worker
        # iniciado pelo dashboard deve herdar essa configuração; removê-la
        # faria ativos que só possuem GENESIS_DATABASE_BACKEND=postgres
        # encerrarem antes do primeiro ciclo.
        command = [str(python_exe), str(ROBO_ROOT / "trading_bot_geneses.py"), env_file]
        # O worker ganha uma janela própria para que o operador acompanhe os
        # logs em tempo real, como acontecia com os BATs manuais.
        # On Windows, expose each worker in its own console. CREATE_NO_WINDOW
        # silently hid the worker consoles even though the API start succeeded.
        if os.name == "nt":
            flags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            )
        else:
            flags = 0
        # O worker configura seu próprio arquivo via GENESIS_LOG_FILE. Deixe
        # stdout/stderr livres para a janela de console criada pelo dashboard,
        # permitindo acompanhar o processamento em tempo real.
        with log_path.open("a", encoding="utf-8", buffering=1) as log_handle:
            log_handle.write(f"\n[dashboard] iniciando ativo={asset} estrategia={selected_strategy} env={env_file}\n")
            log_handle.flush()
        launched_at = time.time()
        process = subprocess.Popen(
            command,
            cwd=ROBO_ROOT,
            env=worker_env,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            creationflags=flags,
        )
        time.sleep(0.5)
        worker_pid = _resolve_worker_pid(process.pid, env_file, launched_at)
        item = {"asset": asset, "pid": worker_pid, "launcher_pid": process.pid, "env_file": env_file, "strategy": selected_strategy, "log_file": str(log_path), "status": "running", "started_at": launched_at}
        registry[asset] = item
        started.append({**item, "running": True, "already_running": False, "lstm": lstm_status})
        timeout_raw = _profile_value(env_file, "GENESIS_STARTUP_WATCHDOG_SECONDS") or os.getenv("GENESIS_STARTUP_WATCHDOG_SECONDS", "90")
        try:
            startup_timeout = float(timeout_raw)
        except (TypeError, ValueError):
            startup_timeout = 90.0
        threading.Thread(
            target=_startup_watchdog,
            args=(asset, env_file, launched_at, process.pid, startup_timeout),
            daemon=True,
            name=f"startup-watchdog-{asset.lower()}",
        ).start()
    _write_registry(registry)
    return started

def stop(assets: list[str]) -> list[dict[str, Any]]:
    selected_assets = _validate_assets(assets)
    registry = _read_registry()
    stopped = []
    for asset in selected_assets:
        item = registry.get(asset)
        env_file = ASSET_ENV_FILES[asset]
        discovered_pids = _discover_worker_pids(asset, env_file)
        registered_pid = int((item or {}).get("pid", 0) or 0)
        pids = sorted(set(discovered_pids + ([registered_pid] if registered_pid > 0 else [])))
        if not item and not pids:
            stopped.append({"asset": asset, "status": "not_registered", "running": False})
            continue
        for pid in pids:
            if _pid_alive(pid):
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
        stopped_item = dict(item or {})
        stopped_item.update({"asset": asset, "status": "stopped", "stopped_at": time.time(), "running": False})
        stopped.append(stopped_item)
    _write_registry(registry)
    return stopped
