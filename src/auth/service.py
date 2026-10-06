"""Auth business logic: registration, sign-in, tokens and quota enforcement.

Passwords are hashed with stdlib PBKDF2-HMAC-SHA256 (no extra dependency);
tokens are opaque random strings persisted in MySQL so they can be revoked
on logout.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from typing import Optional

from src.core.config import get_settings
from src.auth.store import AuthStore, AuthStoreUnavailable, get_auth_store

logger = logging.getLogger(__name__)

__all__ = [
    "AuthError",
    "hash_password",
    "verify_password",
    "generate_token",
    "validate_credentials",
    "AuthService",
    "get_auth_service",
]

_PBKDF2_ITERATIONS = 260_000
_USERNAME_RE = re.compile(r"^[\w\u4e00-\u9fa5-]{2,32}$", re.UNICODE)


class AuthError(Exception):
    """Business failure mapped to an HTTP response by the API layer."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def hash_password(password: str) -> str:
    """Return ``pbkdf2_sha256$iterations$salt_hex$hash_hex``."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against its stored hash."""
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def generate_token() -> str:
    """64-hex-character opaque bearer token (32 random bytes)."""
    return secrets.token_hex(32)


def validate_credentials(username: str, password: str) -> None:
    """Raise :class:`AuthError` when the input violates the configured rules."""
    cfg = get_settings().auth
    if not _USERNAME_RE.match(username) or not (
        cfg.username_min_length <= len(username) <= cfg.username_max_length
    ):
        raise AuthError(
            422,
            "INVALID_USERNAME",
            f"用户名需为 {cfg.username_min_length}-{cfg.username_max_length} 位中文、字母、数字、下划线或连字符",
        )
    if len(password) < cfg.password_min_length:
        raise AuthError(422, "INVALID_PASSWORD", f"密码至少 {cfg.password_min_length} 位")


class AuthService:
    """Registers users, issues tokens and enforces the anonymous quota."""

    def __init__(self, store: Optional[AuthStore] = None) -> None:
        self._store = store or get_auth_store()

    @property
    def settings(self):
        return get_settings().auth

    # ------------------------------------------------------------------
    # Registration / sign-in
    # ------------------------------------------------------------------
    async def register(self, username: str, password: str) -> dict:
        if not self.settings.enabled:
            raise AuthError(503, "AUTH_DISABLED", "认证服务未启用")
        username = username.strip()
        validate_credentials(username, password)
        try:
            user_id = await self._store.create_user(username, hash_password(password))
        except AuthStoreUnavailable as exc:
            raise AuthError(503, "AUTH_UNAVAILABLE", "认证服务暂不可用：MySQL 未启动或连接失败") from exc
        if user_id is None:
            raise AuthError(409, "USERNAME_TAKEN", "该用户名已被注册")
        token = generate_token()
        await self._store.create_token(user_id, token, self.settings.token_ttl_days)
        await self._store.touch_last_login(user_id)
        logger.info("Registered user %s (id=%s)", username, user_id)
        return {"token": token, "user": {"id": user_id, "username": username}}

    async def login(self, username: str, password: str) -> dict:
        if not self.settings.enabled:
            raise AuthError(503, "AUTH_DISABLED", "认证服务未启用")
        try:
            row = await self._store.get_user_by_username(username.strip())
        except AuthStoreUnavailable as exc:
            raise AuthError(503, "AUTH_UNAVAILABLE", "认证服务暂不可用：MySQL 未启动或连接失败") from exc
        if row is None or not verify_password(password, row["password_hash"]):
            raise AuthError(401, "BAD_CREDENTIALS", "用户名或密码错误")
        token = generate_token()
        await self._store.create_token(int(row["id"]), token, self.settings.token_ttl_days)
        await self._store.touch_last_login(int(row["id"]))
        return {"token": token, "user": {"id": int(row["id"]), "username": row["username"]}}

    async def logout(self, token: str) -> None:
        if not token:
            return
        try:
            await self._store.delete_token(token)
        except AuthStoreUnavailable:
            logger.warning("Logout skipped: auth store unavailable")

    # ------------------------------------------------------------------
    # Token resolution
    # ------------------------------------------------------------------
    async def resolve_token(self, token: str) -> Optional[dict]:
        """Return the token's user, or None when invalid/expired/store down."""
        if not token:
            return None
        try:
            return await self._store.get_user_by_token(token)
        except AuthStoreUnavailable:
            return None

    # ------------------------------------------------------------------
    # Anonymous trial quota (only Q&A requests are counted)
    # ------------------------------------------------------------------
    async def consume_question(self, anon_id: str) -> Optional[int]:
        """Consume one free question for an anonymous identity.

        Returns the remaining count after consuming. Raises
        :class:`AuthError` (403) when the quota is exhausted. When MySQL is
        unavailable the check fails open (returns None) so the demo keeps
        running; this is intentional graceful degradation.
        """
        if not self.settings.enabled:
            return None
        limit = self.settings.anonymous_limit
        try:
            allowed, used = await self._store.consume_anon_quota(anon_id, limit)
        except AuthStoreUnavailable as exc:
            logger.warning("Quota check skipped (store unavailable): %s", exc)
            return None
        if not allowed:
            raise AuthError(
                403,
                "QUOTA_EXCEEDED",
                f"未登录免费体验次数已用完（{limit} 次），请注册或登录后继续使用",
            )
        return max(0, limit - used)

    async def peek_quota(self, anon_id: str) -> Optional[dict]:
        """Report the anonymous quota without consuming (for ``/auth/me``)."""
        if not self.settings.enabled:
            return None
        limit = self.settings.anonymous_limit
        try:
            used = await self._store.get_anon_used(anon_id)
        except AuthStoreUnavailable:
            return None
        return {"limit": limit, "used": used, "remaining": max(0, limit - used)}


_auth_service: Optional[AuthService] = None


def get_auth_service() -> AuthService:
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service
