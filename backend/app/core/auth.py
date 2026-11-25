"""
Simple local login (SSO replaces this in Phase 4).

- Passwords are stored as scrypt hashes.
- A login token is "<base64 payload>.<HMAC signature>", signed with SECRET_KEY.
- Routes get the logged-in user with:  user: User = Depends(current_user)
"""

import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_db

# ---------- passwords ----------


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest.hex(), digest_hex)


# ---------- tokens ----------


def _sign(payload: str) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def create_token(user_id: str) -> str:
    expires = int(time.time()) + get_settings().token_ttl_hours * 3600
    payload = base64.urlsafe_b64encode(json.dumps({"uid": user_id, "exp": expires}).encode()).decode()
    return f"{payload}.{_sign(payload)}"


def read_token(token: str) -> str | None:
    """Return the user id if the token is valid and not expired."""
    payload, _, signature = token.partition(".")
    if not payload or not hmac.compare_digest(_sign(payload), signature):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        return None
    return data["uid"] if data.get("exp", 0) > time.time() else None


# ---------- FastAPI dependency ----------


def current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    user_id = read_token(token) if token else None
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="Please log in")
    return user
