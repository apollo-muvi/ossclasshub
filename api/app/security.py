"""ClassHub OSS — Security helpers"""
import hashlib
import hmac
import secrets
import time

from app.config import get_settings


def create_token(payload: str) -> str:
    s = get_settings()
    ts = str(int(time.time()))
    msg = f"{ts}.{payload}"
    sig = hmac.new(s.secret_key.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}.{sig}"


def verify_token(token: str) -> str | None:
    """Returns payload if valid, None otherwise."""
    try:
        ts_str, payload, sig = token.rsplit(".", 2)
        msg = f"{ts_str}.{payload}"
        expected = hmac.new(
            get_settings().secret_key.encode(), msg.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        # check expiry
        elapsed = time.time() - int(ts_str)
        if elapsed > get_settings().access_token_expire_minutes * 60:
            return None
        return payload
    except (ValueError, KeyError):
        return None


def gen_invite_code() -> str:
    return secrets.token_urlsafe(16)
