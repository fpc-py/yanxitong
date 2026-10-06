"""Auth endpoints: register / login / logout / me.

Mounted by ``src.api.routes`` under ``/api/auth``. Errors carry a
machine-readable ``detail.code`` (e.g. ``QUOTA_EXCEEDED``, ``USERNAME_TAKEN``)
so the frontend can branch without parsing Chinese text.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from src.api.deps import extract_bearer_token, get_identity
from src.api.schemas import AuthResponse, LoginRequest, MeResponse, QuotaInfo, RegisterRequest, UserInfo
from src.auth.service import AuthError, get_auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _http_error(exc: AuthError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


@router.post("/register", response_model=AuthResponse)
async def register(req: RegisterRequest):
    """Create an account and return a bearer token (auto sign-in)."""
    try:
        result = await get_auth_service().register(req.username, req.password)
    except AuthError as exc:
        raise _http_error(exc) from exc
    return AuthResponse(token=result["token"], user=UserInfo(**result["user"]))


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest):
    try:
        result = await get_auth_service().login(req.username, req.password)
    except AuthError as exc:
        raise _http_error(exc) from exc
    return AuthResponse(token=result["token"], user=UserInfo(**result["user"]))


@router.post("/logout")
async def logout(request: Request):
    token = extract_bearer_token(request)
    await get_auth_service().logout(token)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(request: Request):
    """Current user plus anonymous quota state (quota is null once signed in)."""
    identity = await get_identity(request)
    if identity.user:
        return MeResponse(user=UserInfo(**identity.user), quota=None)
    quota = await get_auth_service().peek_quota(identity.anon_id or "")
    return MeResponse(user=None, quota=QuotaInfo(**quota) if quota else None)
