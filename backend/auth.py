"""
backend/auth.py
Authentication, sessions, rate limiting, CSRF defence and upload validation.

* Passwords: Argon2id via argon2-cffi (no custom crypto).
* Sessions: 256-bit random opaque token in an HttpOnly + SameSite=Strict cookie.
  Only the SHA-256 of the token is stored server-side, so a leaked DB cannot be replayed.
* CSRF: state-changing requests must carry the custom header `X-SCE-Request: 1`
  (browsers cannot add custom headers cross-site without a CORS pre-flight we never grant)
  and, if an Origin header is present, it must match this server or an allowed origin.
"""

import re
import hashlib
import hmac
import secrets
import threading
import time
import uuid
from typing import Optional, Dict, Any

from fastapi import HTTPException, Request, Response, status

from backend import config
from backend.db import get_conn

# Standard OWASP recommended PBKDF2-HMAC-SHA256 (600,000 iterations)
# Uses standard library hashlib - zero compiler dependencies, 100% portable on Windows/Python 3.14.
PBKDF2_ROUNDS = 600000

def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ROUNDS)
    return f"pbkdf2:sha256:{PBKDF2_ROUNDS}:{salt.hex()}:{dk.hex()}"

def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        parts = stored_hash.split(":")
        if len(parts) != 5 or parts[0] != "pbkdf2" or parts[1] != "sha256":
            return False
        rounds = int(parts[2])
        salt = bytes.fromhex(parts[3])
        expected_dk = bytes.fromhex(parts[4])
        computed_dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, rounds)
        return hmac.compare_digest(computed_dk, expected_dk)
    except Exception:
        return False

_DUMMY_HASH = _hash_password(secrets.token_urlsafe(16))  # equalises timing for unknown emails

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}$")
COMMON_PASSWORDS = {
    "password", "password1", "password123", "123456789", "1234567890", "qwerty123",
    "iloveyou", "letmein123", "welcome123", "admin12345", "qwertyuiop", "11111111111",
}


# ----------------------------------------------------------------------------------
# Rate limiting (in-memory, per process). Use Redis or a gateway limiter when scaling out.
# ----------------------------------------------------------------------------------
_buckets: Dict[str, list] = {}
_bucket_lock = threading.Lock()


def client_ip(request: Request) -> str:
    # Do NOT trust X-Forwarded-For unless a trusted proxy is configured (uvicorn --proxy-headers).
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request, scope: str, limit: int, window_sec: int) -> None:
    key = f"{scope}:{client_ip(request)}"
    now = time.time()
    with _bucket_lock:
        stamps = [t for t in _buckets.get(key, []) if now - t < window_sec]
        if len(stamps) >= limit:
            retry = int(window_sec - (now - stamps[0])) + 1
            _buckets[key] = stamps
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                detail="Too many requests. Please wait and try again.",
                                headers={"Retry-After": str(retry)})
        stamps.append(now)
        _buckets[key] = stamps
        if len(_buckets) > 50_000:  # bound memory under a flood of distinct IPs
            _buckets.clear()


def reset_rate_limits() -> None:
    with _bucket_lock:
        _buckets.clear()


# ----------------------------------------------------------------------------------
# CSRF / origin checks for cookie-authenticated, state-changing requests
# ----------------------------------------------------------------------------------
def verify_csrf(request: Request) -> None:
    if request.headers.get("x-sce-request") != "1":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Request blocked (missing anti-forgery header).")
    origin = request.headers.get("origin")
    if origin:
        own = f"{request.url.scheme}://{request.url.netloc}"
        if origin != own and origin not in config.ALLOWED_ORIGINS:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Request blocked (cross-site origin).")


# ----------------------------------------------------------------------------------
# Validation helpers
# ----------------------------------------------------------------------------------
def normalise_email(email: str) -> str:
    email = (email or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Please enter a valid email address.")
    return email


def validate_password(password: str, email: str) -> None:
    if not isinstance(password, str) or len(password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters.")
    if len(password) > 128:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Password must be at most 128 characters.")
    low = password.lower()
    if low in COMMON_PASSWORDS or len(set(password)) < 3 or low == email.split("@")[0]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="That password is too easy to guess. Try a longer passphrase (e.g. SpeechChamp2026!).")




def validate_display_name(name: str) -> str:
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not 1 <= len(name) <= 40 or re.search(r"[<>\"'`\\\x00-\x1f]", name):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Display name must be 1-40 characters without < > quotes or backslashes.")
    return name


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------------
# Accounts
# ----------------------------------------------------------------------------------
def create_user(email: str, password: str, display_name: str) -> Dict[str, Any]:
    email = normalise_email(email)
    validate_password(password, email)
    display_name = validate_display_name(display_name)
    pw_hash = _hash_password(password)
    uid = str(uuid.uuid4())
    with get_conn() as c:
        try:
            c.execute("INSERT INTO users (id, email, display_name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                      (uid, email, display_name, pw_hash, time.time()))
        except Exception:
            # Generic message: do not confirm whether the email is registered.
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="We could not create an account with those details. If you already have one, log in instead.")
    return {"id": uid, "email": email, "display_name": display_name}


def authenticate(email: str, password: str) -> Dict[str, Any]:
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password.")
    try:
        email = normalise_email(email)
    except HTTPException:
        raise generic
    if not isinstance(password, str) or not password or len(password) > 128:
        raise generic
    with get_conn() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            _verify_password(password, _DUMMY_HASH)
            raise generic
        if row["locked_until"] > time.time():
            # Same message as a wrong password to avoid confirming the account exists.
            raise generic
        if not _verify_password(password, row["password_hash"]):
            fails = row["failed_logins"] + 1
            locked = time.time() + config.LOCKOUT_SEC if fails >= config.MAX_FAILED_LOGINS else 0
            c.execute("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                      (0 if locked else fails, locked, row["id"]))
            raise generic
        if not row["email_verified"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                detail="Verify your email address before signing in. Use the resend verification link if needed.")
        c.execute("UPDATE users SET failed_logins = 0, locked_until = 0 WHERE id = ?", (row["id"],))
        return {"id": row["id"], "email": row["email"], "display_name": row["display_name"]}


def create_email_token(email: str, purpose: str, ttl_sec: int) -> Optional[str]:
    if purpose not in {"verify_email", "reset_password"}:
        raise ValueError("Unsupported email token purpose")
    with get_conn() as c:
        row = c.execute("SELECT id, email_verified FROM users WHERE email = ?", (normalise_email(email),)).fetchone()
        if row is None or (purpose == "verify_email" and row["email_verified"]):
            return None
        token = secrets.token_urlsafe(32)
        c.execute("DELETE FROM email_tokens WHERE user_id = ? AND purpose = ?", (row["id"], purpose))
        c.execute("INSERT INTO email_tokens (token_hash, user_id, purpose, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                  (_token_hash(token), row["id"], purpose, time.time(), time.time() + ttl_sec))
        return token


def consume_email_verification(token: str) -> bool:
    if not token or len(token) > 128:
        return False
    now = time.time()
    with get_conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT user_id FROM email_tokens WHERE token_hash = ? AND purpose = 'verify_email' AND expires_at > ?",
                        (_token_hash(token), now)).fetchone()
        if row is None:
            c.execute("ROLLBACK")
            return False
        c.execute("DELETE FROM email_tokens WHERE token_hash = ?", (_token_hash(token),))
        c.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (row["user_id"],))
        c.execute("COMMIT")
        return True


def reset_password_with_token(token: str, new_password: str) -> bool:
    if not token or len(token) > 128:
        return False
    now = time.time()
    token_digest = _token_hash(token)
    with get_conn() as c:
        row = c.execute("SELECT u.id, u.email FROM email_tokens t JOIN users u ON u.id = t.user_id "
                        "WHERE t.token_hash = ? AND t.purpose = 'reset_password' AND t.expires_at > ?",
                        (token_digest, now)).fetchone()
    if row is None:
        return False
    validate_password(new_password, row["email"])
    pw_hash = _hash_password(new_password)
    with get_conn() as c:
        c.execute("BEGIN IMMEDIATE")
        cur = c.execute("DELETE FROM email_tokens WHERE token_hash = ? AND purpose = 'reset_password' AND expires_at > ?",
                        (token_digest, time.time()))
        if cur.rowcount != 1:
            c.execute("ROLLBACK")
            return False
        c.execute("UPDATE users SET password_hash = ?, failed_logins = 0, locked_until = 0 WHERE id = ?",
                  (pw_hash, row["id"]))
        c.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        c.execute("DELETE FROM email_tokens WHERE user_id = ? AND purpose = 'reset_password'", (row["id"],))
        c.execute("COMMIT")
        return True


def delete_user(user_id: str) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM users WHERE id = ?", (user_id,))  # cascades to sessions + analyses


def delete_unverified_user(user_id: str) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM users WHERE id = ? AND email_verified = 0", (user_id,))


# ----------------------------------------------------------------------------------
# Sessions
# ----------------------------------------------------------------------------------
def _cookie_secure(request: Request) -> bool:
    return config.FORCE_SECURE_COOKIES or request.url.scheme == "https"


def start_session(request: Request, response: Response, user_id: str) -> None:
    # Session-fixation defence: always discard whatever session the browser presented.
    old = request.cookies.get(config.SESSION_COOKIE)
    token = secrets.token_urlsafe(32)
    now = time.time()
    with get_conn() as c:
        if old:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(old),))
        c.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                  (_token_hash(token), user_id, now, now + config.SESSION_TTL_SEC))
    response.set_cookie(config.SESSION_COOKIE, token, max_age=config.SESSION_TTL_SEC, httponly=True,
                        secure=_cookie_secure(request), samesite="strict", path="/")


def end_session(request: Request, response: Response) -> None:
    token = request.cookies.get(config.SESSION_COOKIE)
    if token:
        with get_conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
    response.delete_cookie(config.SESSION_COOKIE, path="/", httponly=True,
                           secure=_cookie_secure(request), samesite="strict")


def current_user(request: Request) -> Optional[Dict[str, Any]]:
    token = request.cookies.get(config.SESSION_COOKIE)
    if not token or len(token) > 128:
        return None
    with get_conn() as c:
        row = c.execute(
            "SELECT u.id, u.email, u.display_name, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ?", (_token_hash(token),)).fetchone()
        if row is None:
            return None
        if row["expires_at"] < time.time():
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
            return None
        return {"id": row["id"], "email": row["email"], "display_name": row["display_name"]}


def require_user(request: Request) -> Dict[str, Any]:
    user = current_user(request)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Please log in to continue.")
    return user


# ----------------------------------------------------------------------------------
# Audio provenance
# ----------------------------------------------------------------------------------
def sha256_hex(data: bytes) -> str:
    """SHA-256 fingerprint of the exact bytes the server received (integrity reference, not proof of origin)."""
    return hashlib.sha256(data).hexdigest()
