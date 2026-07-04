"""JWT authentication, password hashing, bootstrap token, and login rate limiting.

Provides the core security primitives used by ``routes/auth.py`` and ``deps.py``:
- Password hashing via passlib argon2
- JWT access/refresh token issuance and validation (python-jose)
- In-memory refresh token revocation store
- Bootstrap token generation for first-run admin creation
- Login failure rate limiting (count + temporary lockout)
"""

from __future__ import annotations

import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.repositories import AdminUserRepository


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

_pwd_ctx = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Hash a plaintext password (argon2 preferred, bcrypt fallback)."""
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against its hash."""
    try:
        return _pwd_ctx.verify(plain, hashed)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def _secret_key(config: ConfigProvider) -> str:
    """Read SECRET_KEY from config; raise if empty."""
    key = config.get_str("SECRET_KEY")
    if not key:
        raise RuntimeError(
            "SECRET_KEY is not configured. Set it in .env or environment "
            "before starting the admin panel."
        )
    return key


def create_access_token(
    config: ConfigProvider,
    *,
    username: str,
    role: str,
) -> str:
    """Issue a short-lived access token."""
    now = time.time()
    ttl = config.get_int("JWT_ACCESS_TTL")
    payload: dict[str, Any] = {
        "sub": username,
        "role": role,
        "type": "access",
        "exp": now + ttl,
        "iat": now,
    }
    return jwt.encode(payload, _secret_key(config), algorithm="HS256")


def create_refresh_token(
    config: ConfigProvider,
    *,
    username: str,
    role: str,
    jti: str | None = None,
) -> tuple[str, str]:
    """Issue a long-lived refresh token. Returns (token, jti)."""
    jti = jti or secrets.token_hex(16)
    now = time.time()
    ttl = config.get_int("JWT_REFRESH_TTL")
    payload: dict[str, Any] = {
        "sub": username,
        "role": role,
        "type": "refresh",
        "jti": jti,
        "exp": now + ttl,
        "iat": now,
    }
    token = jwt.encode(payload, _secret_key(config), algorithm="HS256")
    return token, jti


def decode_token(config: ConfigProvider, token: str) -> dict[str, Any]:
    """Decode and validate a JWT. Raises ``InvalidTokenError`` on any failure."""
    try:
        payload = jwt.decode(
            token, _secret_key(config), algorithms=["HS256"]
        )
    except JWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    return payload


class InvalidTokenError(Exception):
    """Raised when a JWT is invalid, expired, or malformed."""


# ---------------------------------------------------------------------------
# Refresh token revocation store (in-memory)
# ---------------------------------------------------------------------------


class RefreshTokenStore:
    """In-memory store tracking valid refresh token JTIs.

    On logout the JTI is added to the revoked set; on refresh the old JTI
    is revoked and a new one is registered.
    """

    def __init__(self) -> None:
        self._valid: set[str] = set()

    def register(self, jti: str) -> None:
        self._valid.add(jti)

    def revoke(self, jti: str) -> None:
        self._valid.discard(jti)

    def is_valid(self, jti: str) -> bool:
        return jti in self._valid


# Module-level singleton — shared across the admin sub-app process.
_refresh_store = RefreshTokenStore()


def get_refresh_store() -> RefreshTokenStore:
    return _refresh_store


# ---------------------------------------------------------------------------
# Bootstrap token
# ---------------------------------------------------------------------------


def generate_bootstrap_token(data_root: str) -> str:
    """Generate a one-time bootstrap token and persist it to disk.

    The token is written to ``{data_root}/bootstrap_token.txt`` with
    restrictive permissions (0o600 on POSIX).  It is only valid when
    no admin users exist yet.
    """
    token = secrets.token_urlsafe(32)
    token_path = Path(data_root) / "bootstrap_token.txt"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token, encoding="utf-8")
    # Restrict file permissions to owner-only
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass
    return token


def validate_bootstrap_token(data_root: str, token: str) -> bool:
    """Check the provided token against the on-disk bootstrap token."""
    token_path = Path(data_root) / "bootstrap_token.txt"
    if not token_path.is_file():
        return False
    stored = token_path.read_text(encoding="utf-8").strip()
    return secrets.compare_digest(stored, token)


def consume_bootstrap_token(data_root: str) -> None:
    """Delete the bootstrap token file after first admin is created."""
    token_path = Path(data_root) / "bootstrap_token.txt"
    try:
        token_path.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Login rate limiting (in-memory per-username)
# ---------------------------------------------------------------------------


class LoginRateLimiter:
    """Simple in-memory rate limiter for login failures.

    After ``max_failures`` consecutive failures within ``window_seconds``,
    the account is locked for ``lockout_seconds``.
    """

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_seconds: float = 300.0,
        lockout_seconds: float = 300.0,
    ) -> None:
        self._max_failures = max_failures
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def is_locked(self, username: str) -> bool:
        """Return True if the account is currently locked out."""
        until = self._locked_until.get(username, 0)
        if time.time() < until:
            return True
        # Lockout expired — clean up
        self._locked_until.pop(username, None)
        return False

    def record_failure(self, username: str) -> None:
        """Record a failed login attempt; lock if threshold exceeded."""
        now = time.time()
        attempts = self._failures.setdefault(username, [])
        # Prune old attempts outside the window
        attempts[:] = [t for t in attempts if now - t < self._window]
        attempts.append(now)

        if len(attempts) >= self._max_failures:
            self._locked_until[username] = now + self._lockout
            self._failures.pop(username, None)

    def record_success(self, username: str) -> None:
        """Clear failure history on successful login."""
        self._failures.pop(username, None)
        self._locked_until.pop(username, None)


# Module-level singleton
_rate_limiter = LoginRateLimiter()


def get_rate_limiter() -> LoginRateLimiter:
    return _rate_limiter


# ---------------------------------------------------------------------------
# High-level auth operations (used by routes)
# ---------------------------------------------------------------------------


async def authenticate_user(
    repo: AdminUserRepository,
    config: ConfigProvider,
    username: str,
    password: str,
) -> dict[str, Any] | None:
    """Verify credentials and return user info dict, or None on failure.

    Checks rate limiting, verifies password, and records success/failure.
    Returns ``{"username", "role", "must_change_password"}`` on success.
    """
    limiter = get_rate_limiter()

    if limiter.is_locked(username):
        return None

    row = await repo.get_by_username(username)
    if row is None:
        limiter.record_failure(username)
        return None

    if not verify_password(password, row.password_hash):
        limiter.record_failure(username)
        return None

    limiter.record_success(username)
    await repo.touch_login(username)

    return {
        "username": row.username,
        "role": row.role,
        "must_change_password": row.must_change_password,
    }


async def issue_token_pair(
    config: ConfigProvider,
    username: str,
    role: str,
) -> dict[str, str]:
    """Issue an access + refresh token pair and register the refresh JTI."""
    access = create_access_token(config, username=username, role=role)
    refresh, jti = create_refresh_token(config, username=username, role=role)
    get_refresh_store().register(jti)
    return {"access_token": access, "refresh_token": refresh}


async def refresh_access_token(
    config: ConfigProvider,
    refresh_token: str,
) -> dict[str, str]:
    """Exchange a valid refresh token for a new access token.

    The old refresh JTI is revoked and a new refresh token is issued.
    """
    payload = decode_token(config, refresh_token)
    if payload.get("type") != "refresh":
        raise InvalidTokenError("Not a refresh token")

    jti = payload.get("jti", "")
    store = get_refresh_store()
    if not store.is_valid(jti):
        raise InvalidTokenError("Refresh token has been revoked")

    # Revoke old, issue new
    store.revoke(jti)
    username = payload["sub"]
    role = payload["role"]
    return await issue_token_pair(config, username=username, role=role)


async def revoke_refresh_token(
    config: ConfigProvider,
    refresh_token: str,
) -> None:
    """Revoke a refresh token (logout)."""
    try:
        payload = decode_token(config, refresh_token)
    except InvalidTokenError:
        return  # Already invalid — nothing to do
    jti = payload.get("jti", "")
    get_refresh_store().revoke(jti)


async def ensure_bootstrap_token(
    repo: AdminUserRepository,
    config: ConfigProvider,
) -> str | None:
    """Generate a bootstrap token if no admin users exist yet.

    Returns the token string, or None if admins already exist.
    """
    count = await repo.count()
    if count > 0:
        return None
    data_root = config.get_str("DATA_ROOT")
    return generate_bootstrap_token(data_root)
