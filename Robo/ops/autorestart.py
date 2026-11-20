import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from ops.healthcheck import run_healthcheck


STATE_PATH = os.path.join("logs", "ops_supervisor_state.json")
EVENTS_PATH = os.path.join("logs", "ops_supervisor.jsonl")


def _utc_now():
    return datetime.now(timezone.utc)


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _append_event(payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(EVENTS_PATH) or ".", exist_ok=True)
    with open(EVENTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _register_critical_event(payload: Dict[str, Any]) -> None:
    try:
        from utils.risk_guard import register_critical_event
        register_critical_event(
            event_type="ops_autorestart",
            message=payload.get("message", "autorestart"),
            severity="WARNING" if payload.get("action") == "restart" else "INFO",
            source="ops.autorestart",
            symbol=payload.get("service", "GLOBAL"),
            details=payload,
        )
    except Exception:
        return


def _parse_iso(ts: str):
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _can_restart(service: Dict[str, Any], state: Dict[str, Any]) -> bool:
    name = str(service.get("name") or "service")
    cool = int(service.get("restart_cooldown_sec", 180) or 180)
    last = (state.get("services") or {}).get(name, {}).get("last_restart")
    if not last:
        return True
    dt = _parse_iso(str(last))
    if dt is None:
        return True
    return _utc_now() >= (dt + timedelta(seconds=max(0, cool)))


def _mark_restart(service_name: str, state: Dict[str, Any]) -> None:
    state.setdefault("services", {})
    state["services"].setdefault(service_name, {})
    state["services"][service_name]["last_restart"] = _utc_now().isoformat()
    state["services"][service_name]["restarts"] = int(state["services"][service_name].get("restarts", 0)) + 1


def run_supervisor(config_path: str, dry_run: bool = False) -> Dict[str, Any]:
    cfg = _load_json(config_path)
    hc = run_healthcheck(config_path)
    state = _load_json(STATE_PATH)
    actions = []

    svc_cfg_by_name = {str((s or {}).get("name")): (s or {}) for s in cfg.get("services", [])}
    for svc in hc.get("services", []):
        if str(svc.get("status")).upper() != "CRIT":
            continue
        name = str(svc.get("name"))
        raw = svc_cfg_by_name.get(name, {})
        cmd = str(raw.get("restart_cmd") or "").strip()
        if not cmd:
            payload = {
                "ts": _utc_now().isoformat(),
                "service": name,
                "action": "skip",
                "message": "Servico critico sem restart_cmd configurado.",
                "details": svc,
            }
            _append_event(payload)
            _register_critical_event(payload)
            actions.append(payload)
            continue

        if not _can_restart(raw, state):
            payload = {
                "ts": _utc_now().isoformat(),
                "service": name,
                "action": "cooldown_skip",
                "message": "Servico critico em cooldown de restart.",
                "details": svc,
            }
            _append_event(payload)
            actions.append(payload)
            continue

        if dry_run:
            payload = {
                "ts": _utc_now().isoformat(),
                "service": name,
                "action": "dry_run_restart",
                "message": f"[DRY] Reinicio planejado: {cmd}",
                "details": svc,
            }
            _append_event(payload)
            actions.append(payload)
            continue

        try:
            subprocess.Popen(cmd, shell=True)
            _mark_restart(name, state)
            payload = {
                "ts": _utc_now().isoformat(),
                "service": name,
                "action": "restart",
                "message": f"Reinicio disparado com sucesso: {cmd}",
                "details": svc,
            }
            _append_event(payload)
            _register_critical_event(payload)
            actions.append(payload)
        except Exception as e:
            payload = {
                "ts": _utc_now().isoformat(),
                "service": name,
                "action": "restart_error",
                "message": f"Falha ao reiniciar: {e}",
                "details": svc,
            }
            _append_event(payload)
            _register_critical_event(payload)
            actions.append(payload)

    _save_json(STATE_PATH, state)
    return {"health": hc, "actions": actions}


def main() -> int:
    parser = argparse.ArgumentParser(description="Supervisor com autorestart para servicos do robo.")
    parser.add_argument("--config", default="ops/services.json", help="Config JSON dos servicos.")
    parser.add_argument("--dry-run", action="store_true", help="Nao reinicia de fato; apenas simula a acao.")
    parser.add_argument("--json-out", default="", help="Arquivo para salvar o resultado da rodada.")
    args = parser.parse_args()

    result = run_supervisor(args.config, dry_run=args.dry_run)

    if args.json_out:
        os.makedirs(os.path.dirname(args.json_out) or ".", exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    print(
        f"[SUPERVISOR] status={result.get('health', {}).get('global_status')} "
        f"| actions={len(result.get('actions', []))}"
    )
    for item in result.get("actions", []):
        print(f" - {item.get('service')}: {item.get('action')} | {item.get('message')}")

    status = str(result.get("health", {}).get("global_status", "OK")).upper()
    if status == "CRIT":
        return 2
    if status == "WARN":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
