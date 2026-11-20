from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import re

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

def _hash_password(password: str) -> str:
    if not password:
        raise ValueError("A senha não pode ficar vazia")
    salt = os.urandom(16)
    iterations = 600_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"

def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        salt = _unb64(salt_b64)
        expected = _unb64(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def _email(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise ValueError("Informe um e-mail válido")
    return normalized

def ensure_admin_user() -> dict[str, str]:
    from ui_backend import repository
    configured_email, configured_password = _configured_user()
    email = _email(configured_email)
    repository.ensure_ui_users_table()
    return repository.upsert_ui_admin(email, _hash_password(configured_password))

def authenticate(email: str, password: str) -> dict[str, str]:
    from ui_backend import repository
    admin = ensure_admin_user()
    user = repository.get_ui_user(email)
    if not user or not user.get("is_active") or not _verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")
    return {"id": str(user["id"]), "email": str(user["email"]), "role": str(user["role"])}

def create_user(email: str, password: str, role: str = "viewer") -> dict[str, str]:
    from ui_backend import repository
    normalized = _email(email)
    if len(password) < 10:
        raise ValueError("Use uma senha com pelo menos 10 caracteres")
    if role not in {"admin", "viewer"}:
        raise ValueError("Perfil inválido")
    repository.ensure_ui_users_table()
    if repository.get_ui_user(normalized):
        raise ValueError("Este e-mail já está cadastrado")
    return repository.create_ui_user(normalized, _hash_password(password), role)

def issue_token(user: dict[str, str], ttl_seconds: int = 8 * 60 * 60) -> str:
    payload = {"sub": user["id"], "email": user["email"], "role": user.get("role", "viewer"), "exp": int(time.time()) + ttl_seconds}
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
        return {"id": str(payload["sub"]), "email": str(payload["email"]), "role": str(payload.get("role", "viewer"))}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido ou expirado")
