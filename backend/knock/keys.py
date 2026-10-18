"""Install-local secrets. One random key per installation (per country/utility deployment).

The key signs session cookies and the trained model file. It is created on first use with owner-only
permissions, never committed (config/ is git-ignored), and can be supplied instead through KNOCK_SECRET
(e.g. from a cloud secret manager). Rotating it logs everyone out and forces a model retrain.
"""
from __future__ import annotations

import hashlib
import hmac
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CONFIG_DIR = os.path.join(ROOT, 'config')
KEY_PATH = os.path.join(CONFIG_DIR, 'secret.key')
_KEY: bytes | None = None


def secret() -> bytes:
    global _KEY
    if _KEY is not None:
        return _KEY
    env = os.environ.get('KNOCK_SECRET')
    if env:
        _KEY = hashlib.sha256(env.encode()).digest()
        return _KEY
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(KEY_PATH):
        fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(os.urandom(32))
    with open(KEY_PATH, 'rb') as f:
        _KEY = f.read()
    if len(_KEY) < 32:
        raise RuntimeError('config/secret.key is too short; delete it to regenerate.')
    return _KEY


def sign(data: bytes, purpose: str) -> str:
    """HMAC-SHA256 with domain separation, so a cookie signature can never be replayed as a model signature."""
    return hmac.new(secret(), purpose.encode() + b'\x00' + data, hashlib.sha256).hexdigest()


def verify(data: bytes, purpose: str, sig: str) -> bool:
    return hmac.compare_digest(sign(data, purpose), sig or '')
