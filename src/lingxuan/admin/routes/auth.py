"""Auth routes: login, refresh, logout, change-password, me.

All endpoints are under ``/admin/api/auth``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from lingxuan.admin.auth import (
    InvalidTokenError,
    authenticate_user,
    consume_bootstrap_token,
    ensure_bootstrap_token,
    get_rate_limiter,
    hash_password,
    issue_token_pair,
    refresh_access_token,
    revoke_refresh_token,
    validate_bootstrap_token,
    verify_password,
)
from lingxuan.admin.deps import (
    AdminUserRepoDep,
    ConfigDep,
    CurrentUser,
    LogDep,
)
from lingxuan.protocols.logging import LogRecord


router = APIRouter(prefix="/auth", tags=["auth"])


def _log_info(log: object, msg: str) -> None:
    """Emit an INFO-level log record via the LogSink protocol."""
    from datetime import datetime, timezone

    record = LogRecord(
        ts=datetime.now(timezone.utc),
        level="INFO",
        logger="lingxuan.admin.auth",
        msg=msg,
    )
    # log is a LogSink — call emit()
    if hasattr(log, "emit"):
        log.emit(record)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)


class BootstrapLoginRequest(BaseModel):
    bootstrap_token: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str


class MeResponse(BaseModel):
    username: str
    role: str
    must_change_password: bool


class BootstrapInfoResponse(BaseModel):
    bootstrap_required: bool
    bootstrap_token: str | None = None


class MessageResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    config: ConfigDep,
    repo: AdminUserRepoDep,
    log: LogDep,
) -> TokenResponse:
    """Authenticate with username + password; returns token pair."""
    user_info = await authenticate_user(repo, config, body.username, body.password)
    if user_info is None:
        # Check if locked for a more specific message
        if get_rate_limiter().is_locked(body.username):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Account temporarily locked due to too many failed attempts",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    tokens = await issue_token_pair(
        config, user_info["username"], user_info["role"]
    )
    _log_info(log, f"admin login: user={user_info['username']}")
    return TokenResponse(**tokens)


@router.post("/bootstrap-login", response_model=TokenResponse)
async def bootstrap_login(
    body: BootstrapLoginRequest,
    config: ConfigDep,
    repo: AdminUserRepoDep,
    log: LogDep,
) -> TokenResponse:
    """First-run: create the initial admin using a bootstrap token.

    Only works when no admin users exist yet.  The bootstrap token is
    consumed after successful creation.
    """
    # Must have zero admin users
    count = await repo.count()
    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Admin users already exist; use /login instead",
        )

    # Validate bootstrap token
    data_root = config.get_str("DATA_ROOT")
    if not validate_bootstrap_token(data_root, body.bootstrap_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired bootstrap token",
        )

    # Create the first admin with must_change_password=True
    pw_hash = hash_password(body.password)
    await repo.create(
        username=body.username,
        password_hash=pw_hash,
        role="admin",
        must_change_password=True,
    )

    # Consume the bootstrap token
    consume_bootstrap_token(data_root)

    # Issue tokens
    tokens = await issue_token_pair(config, body.username, "admin")
    _log_info(log, f"admin bootstrap: user={body.username} created via bootstrap token")
    return TokenResponse(**tokens)


@router.get("/bootstrap-info", response_model=BootstrapInfoResponse)
async def bootstrap_info(
    config: ConfigDep,
    repo: AdminUserRepoDep,
) -> BootstrapInfoResponse:
    """Check whether bootstrap (first admin creation) is needed.

    If no admin users exist, generates and returns a bootstrap token.
    """
    token = await ensure_bootstrap_token(repo, config)
    return BootstrapInfoResponse(
        bootstrap_required=token is not None,
        bootstrap_token=token,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    config: ConfigDep,
) -> TokenResponse:
    """Exchange a valid refresh token for a new access + refresh pair."""
    try:
        tokens = await refresh_access_token(config, body.refresh_token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    return TokenResponse(**tokens)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    body: RefreshRequest,
    config: ConfigDep,
    user: CurrentUser,
    log: LogDep,
) -> MessageResponse:
    """Revoke the current refresh token."""
    await revoke_refresh_token(config, body.refresh_token)
    _log_info(log, f"admin logout: user={user['username']}")
    return MessageResponse(message="Logged out")


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    body: ChangePasswordRequest,
    config: ConfigDep,
    repo: AdminUserRepoDep,
    user: CurrentUser,
    log: LogDep,
) -> MessageResponse:
    """Change the current user's password. Clears must_change_password flag."""
    username = user["username"]
    row = await repo.get_by_username(username)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not verify_password(body.old_password, row.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Old password is incorrect",
        )

    new_hash = hash_password(body.new_password)
    await repo.set_password(username, new_hash, must_change_password=False)
    _log_info(log, f"admin password changed: user={username}")
    return MessageResponse(message="Password changed successfully")


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser) -> MeResponse:
    """Return the current user's info."""
    return MeResponse(
        username=user["username"],
        role=user["role"],
        must_change_password=user["must_change_password"],
    )
