from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)

def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

def _secret() -> bytes:
    secret = os.getenv("GENESIS_AUTH_SECRET", "").strip()
    if len(secret) < 32:
        raise RuntimeError("GENESIS_AUTH_SECRET precisa ter pelo menos 32 caracteres")
    return secret.encode("utf-8")

def _configured_user() -> tuple[str, str]:
    email = os.getenv("GENESIS_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("GENESIS_ADMIN_PASSWORD", "")
    if not email or not password:
        raise RuntimeError("Configure GENESIS_ADMIN_EMAIL e GENESIS_ADMIN_PASSWORD")
    return email, password

def authenticate(email: str, password: str) -> dict[str, str]:
    configured_email, configured_password = _configured_user()
    if not hmac.compare_digest(email.strip().lower(), configured_email) or not hmac.compare_digest(password, configured_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")
    return {"id": "admin", "email": configured_email}

def issue_token(user: dict[str, str], ttl_seconds: int = 8 * 60 * 60) -> str:
    payload = {"sub": user["id"], "email": user["email"], "exp": int(time.time()) + ttl_seconds}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _b64(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"

def require_auth(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict[str, str]:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Autenticação necessária")
    try:
        body, signature = credentials.credentials.split(".", 1)
        expected = _b64(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("assinatura inválida")
        payload = json.loads(_unb64(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("token expirado")
        return {"id": str(payload["sub"]), "email": str(payload["email"])}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido ou expirado")
