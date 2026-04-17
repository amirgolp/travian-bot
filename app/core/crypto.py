"""Fernet-based symmetric encryption for account passwords at rest."""
from __future__ import annotations

from cryptography.fernet import Fernet

from app.core.config import get_settings


def _cipher() -> Fernet:
    key = get_settings().secret_key
    if not key:
        raise RuntimeError(
            "SECRET_KEY is not set. Run `python -m scripts.init_config` to generate one."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _cipher().decrypt(token.encode()).decode()
