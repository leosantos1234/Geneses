import argparse
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen


def _load_psutil():
    try:
        import psutil  # type: ignore
        return psutil
    except Exception:
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("Config invalida: precisa ser objeto JSON.")
    data.setdefault("services", [])
    data.setdefault("global", {})
    return data


def _check_port(host: str, port: int, timeout_sec: float) -> Dict[str, Any]:
    started = time.perf_counter()
    ok = False
    err = None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout_sec)
        try:
            ok = s.connect_ex((host, int(port))) == 0
        except Exception as e:
            err = str(e)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    out = {"ok": ok, "elapsed_ms": round(elapsed_ms, 2), "host": host, "port": int(port)}
    if err:
        out["error"] = err
    return out


def _check_url(url: str, timeout_sec: float) -> Dict[str, Any]:
    started = time.perf_counter()
    code = None
    err = None
    ok = False
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout_sec) as resp:
            code = int(getattr(resp, "status", 0) or 0)
            ok = 200 <= code < 500
    except URLError as e:
        err = str(e)
    except Exception as e:
        err = str(e)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    out = {"ok": ok, "elapsed_ms": round(elapsed_ms, 2), "url": url, "status_code": code}
    if err:
        out["error"] = err
    return out


def _match_processes(tokens: List[str], psutil_mod) -> Dict[str, Any]:
    if not tokens:
        return {"ok": True, "count": 0, "pids": []}
    if psutil_mod is None:
        return {"ok": False, "count": 0, "pids": [], "error": "psutil indisponivel"}
    wanted = [str(t).lower() for t in tokens]
    pids: List[int] = []
    for proc in psutil_mod.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(proc.info.get("cmdline") or []).lower()
            if all(tok in cmd for tok in wanted):
                pids.append(int(proc.info.get("pid")))
        except Exception:
            continue
    return {"ok": len(pids) > 0, "count": len(pids), "pids": pids}


def _memory_snapshot(psutil_mod) -> Dict[str, Any]:
    if psutil_mod is None:
        return {"available": False}
    vm = psutil_mod.virtual_memory()
    return {
        "available": True,
        "percent": float(vm.percent),
        "used_mb": float(vm.used) / (1024.0 * 1024.0),
        "total_mb": float(vm.total) / (1024.0 * 1024.0),
    }


def run_healthcheck(config_path: str) -> Dict[str, Any]:
    cfg = _load_config(config_path)
    psutil_mod = _load_psutil()
    timeout_default = float((cfg.get("global") or {}).get("default_timeout_sec", 2.5) or 2.5)
    max_mem_pct = float((cfg.get("global") or {}).get("max_memory_pct", 0.0) or 0.0)
    max_mem_mb = float((cfg.get("global") or {}).get("max_memory_mb", 0.0) or 0.0)

    report: Dict[str, Any] = {
        "ts": _utc_now(),
        "config_path": os.path.abspath(config_path),
        "global_status": "OK",
        "memory": {},
        "services": [],
        "summary": {"ok": 0, "warn": 0, "crit": 0},
    }

    mem = _memory_snapshot(psutil_mod)
    report["memory"] = mem
    if mem.get("available"):
        mem_warn = False
        if max_mem_pct > 0 and float(mem["percent"]) >= max_mem_pct:
            mem_warn = True
        if max_mem_mb > 0 and float(mem["used_mb"]) >= max_mem_mb:
            mem_warn = True
        if mem_warn:
            report["summary"]["warn"] += 1
            report["global_status"] = "WARN"

    for raw in cfg.get("services", []):
        svc = dict(raw or {})
        name = str(svc.get("name") or "service")
        enabled = bool(svc.get("enabled", True))
        timeout_sec = float(svc.get("timeout_sec", timeout_default) or timeout_default)
        checks: Dict[str, Any] = {}
        ok = True

        if not enabled:
            status = "SKIP"
            report["services"].append({"name": name, "status": status, "enabled": False, "checks": checks})
            continue

        if svc.get("port"):
            host = str(svc.get("host") or "127.0.0.1")
            checks["port"] = _check_port(host, int(svc["port"]), timeout_sec)
            ok = ok and bool(checks["port"]["ok"])

        if svc.get("health_url"):
            checks["health_url"] = _check_url(str(svc["health_url"]), timeout_sec)
            ok = ok and bool(checks["health_url"]["ok"])

        if svc.get("process_match"):
            checks["process"] = _match_processes(list(svc.get("process_match") or []), psutil_mod)
            ok = ok and bool(checks["process"]["ok"])

        status = "OK" if ok else "CRIT"
        if status == "OK":
            report["summary"]["ok"] += 1
        else:
            report["summary"]["crit"] += 1
            report["global_status"] = "CRIT"

        report["services"].append(
            {"name": name, "status": status, "enabled": True, "checks": checks}
        )

    return report


def _print_report(report: Dict[str, Any]) -> None:
    print(f"[HEALTHCHECK] {report.get('ts')} | status={report.get('global_status')}")
    mem = report.get("memory") or {}
    if mem.get("available"):
        print(
            f"[MEM] {float(mem.get('percent', 0.0)):.1f}% | "
            f"used={float(mem.get('used_mb', 0.0)):.0f}MB / total={float(mem.get('total_mb', 0.0)):.0f}MB"
        )
    for svc in report.get("services", []):
        print(f"[SVC] {svc.get('name')} -> {svc.get('status')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Healthcheck operacional dos servicos do robo.")
    parser.add_argument("--config", default="ops/services.json", help="Caminho do JSON de servicos.")
    parser.add_argument("--json-out", default="", help="Salvar relatorio JSON neste arquivo.")
    parser.add_argument("--quiet", action="store_true", help="Nao imprimir resumo no stdout.")
    args = parser.parse_args()

    report = run_healthcheck(args.config)
    if args.json_out:
        os.makedirs(os.path.dirname(args.json_out) or ".", exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    if not args.quiet:
        _print_report(report)

    status = str(report.get("global_status", "OK")).upper()
    if status == "CRIT":
        return 2
    if status == "WARN":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

