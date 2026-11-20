"""MCP V2 model inventory tool: get_model_inventory."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import time

from genesis_runtime import database_backend

from genesis_mcp.common import ErrorInfo, ModelArtifact, ModelInventoryResponse, call_tool


def _error(code: str, message: str) -> ErrorInfo:
    return ErrorInfo(code=code, message=message)


def _canonical_database_error() -> ErrorInfo | None:
    if database_backend() != "postgres":
        return ErrorInfo(
            code="DATABASE_UNAVAILABLE",
            message="MCP V2 inventory exige PostgreSQL canônico; SQLite não é uma fonte permitida"
        )
    return None


def get_model_inventory_impl(request_id: str = "") -> ModelInventoryResponse:
    """Escaneia filesystem para artefatos de modelos (weights, checkpoints, configs)."""
    def run() -> ModelInventoryResponse:
        models = []
        model_dirs = [
            Path(__file__).resolve().parents[2] / "models",
            Path(__file__).resolve().parents[2] / "checkpoints",
            Path(__file__).resolve().parents[2] / "weights",
            Path(__file__).resolve().parents[2] / "artifacts",
            # Canonical runtime model location used by the workers.
            Path(__file__).resolve().parents[2] / "estrategias",
        ]

        seen = set()
        for model_dir in model_dirs:
            if not model_dir.exists():
                continue

            try:
                for item in model_dir.rglob("*"):
                    if item.is_file() and item.name not in seen:
                        seen.add(item.name)

                        model_type = None
                        category = "artifact"
                        if item.suffix in (".pt", ".pth"):
                            model_type = "pytorch"
                        elif item.suffix in (".h5", ".keras"):
                            model_type = "keras"
                        elif item.suffix == ".pkl":
                            model_type = "pickle"
                        elif item.suffix == ".joblib":
                            model_type = "joblib"
                        elif item.suffix == ".onnx":
                            model_type = "onnx"

                        if item.name.lower().startswith("scaler_"):
                            category = "scaler"
                            model_type = model_type or "scaler"
                        elif model_type is not None:
                            category = "model"

                        stem = item.stem.lower()
                        symbol = None
                        for candidate in ("btc", "eth", "bnb", "sol", "xrp"):
                            if f"_{candidate}_" in f"_{stem}_" or stem.startswith(f"{candidate}_"):
                                symbol = f"{candidate.upper()}USDT"
                                break

                        stat = item.stat()
                        modified_ts = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

                        # Status based on concrete evidence only
                        # ACTIVE_IN_LIVE: requires worker config evidence (not filesystem alone)
                        # INACTIVE: requires deactivation evidence (not filesystem alone)
                        # AVAILABLE: file exists, no conflicting evidence
                        # UNKNOWN: insufficient evidence
                        status = "AVAILABLE"
                        evidence = f"File exists in filesystem"

                        version = None
                        if "_v" in item.name:
                            try:
                                version = item.name.split("_v")[1].split("_")[0]
                            except IndexError:
                                pass

                        models.append(ModelArtifact(
                            name=item.name,
                            version=version,
                            path=str(item.relative_to(Path(__file__).resolve().parents[2])),
                            size_bytes=stat.st_size,
                            modified_at=modified_ts,
                            model_type=model_type,
                            category=category,
                            symbol=symbol,
                            status=status,
                            operational_evidence=evidence
                        ))
            except OSError:
                continue

        models.sort(key=lambda m: m.modified_at or "", reverse=True)

        return ModelInventoryResponse(
            request_id=request_id,
            source="filesystem",
            count=len(models),
            models=models
        )

    result = call_tool("get_model_inventory", request_id, run)
    if isinstance(result, Exception):
        return ModelInventoryResponse(
            request_id=request_id,
            source="filesystem",
            count=0,
            error=_error("FILESYSTEM_ERROR", "Não foi possível escanear inventário de modelos")
        )
    return result
