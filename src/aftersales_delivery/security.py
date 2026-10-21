"""Purpose-bound HMAC tokens and salted password hashes for the local demo."""

import base64
import hashlib
import hmac
import json
import secrets
import time


class AuthenticationError(ValueError):
    pass


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    return f"pbkdf2_sha256$310000${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$")
        if scheme != "pbkdf2_sha256" or int(iterations) != 310000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), _decode(salt), int(iterations))
        return hmac.compare_digest(actual, _decode(expected))
    except (ValueError, TypeError):
        return False


def issue_token(
    key: str,
    username: str,
    customer_id: str,
    role: str,
    *,
    purpose: str = "session",
    ttl: int = 3600,
    action_id: str | None = None,
) -> str:
    now = int(time.time())
    payload = {
        "sub": username,
        "customer_id": customer_id,
        "role": role,
        "purpose": purpose,
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_hex(12),
        "action_id": action_id,
    }
    body = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _encode(hmac.digest(key.encode(), body.encode(), "sha256"))
    return f"{body}.{signature}"


def decode_token(token: str, key: str, *, purpose: str = "session") -> dict:
    try:
        if len(token) > 4096:
            raise ValueError()
        body, signature = token.split(".")
        expected = hmac.digest(key.encode(), body.encode(), "sha256")
        if not hmac.compare_digest(_decode(signature), expected):
            raise ValueError()
        payload = json.loads(_decode(body))
        now = time.time()
        if (
            not isinstance(payload, dict)
            or payload.get("purpose") != purpose
            or type(payload.get("exp")) is not int
            or payload["exp"] <= now
            or type(payload.get("iat")) is not int
            or payload["iat"] > now + 5
            or payload.get("role") not in {"customer", "approver"}
            or not isinstance(payload.get("sub"), str)
            or not isinstance(payload.get("customer_id"), str)
        ):
            raise ValueError()
        return payload
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise AuthenticationError("INVALID_TOKEN") from None
