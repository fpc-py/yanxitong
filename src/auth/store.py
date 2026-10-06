"""MySQL persistence for accounts, bearer tokens and the anonymous trial quota.

The pool is created lazily on first use with a short retry cooldown, so the
application still boots (and the demo still runs) when MySQL has not been
started yet: callers get :class:`AuthStoreUnavailable` and the API layer
degrades gracefully.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.core.config import get_settings

logger = logging.getLogger(__name__)

__all__ = ["AuthStoreUnavailable", "AuthStore", "get_auth_store"]

_RETRY_COOLDOWN_SECONDS = 30.0

_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(64) NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_login_at DATETIME NULL,
        UNIQUE KEY uq_users_username (username)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_tokens (
        token CHAR(64) PRIMARY KEY,
        user_id BIGINT UNSIGNED NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME NOT NULL,
        KEY idx_tokens_user (user_id),
        CONSTRAINT fk_tokens_user FOREIGN KEY (user_id)
            REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS anon_quota (
        anon_id VARCHAR(96) PRIMARY KEY,
        used INT NOT NULL DEFAULT 0,
        first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


class AuthStoreUnavailable(RuntimeError):
    """Raised when MySQL cannot be reached (pool creation failed recently)."""


def _utcnow() -> datetime:
    """Naive UTC timestamp matching the DATETIME columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuthStore:
    """Thin async data-access layer around aiomysql."""

    def __init__(self) -> None:
        cfg = get_settings().mysql
        self._cfg = cfg
        self._pool = None
        self._lock = asyncio.Lock()
        self._unavailable_until = 0.0

    # ------------------------------------------------------------------
    # Pool / schema lifecycle
    # ------------------------------------------------------------------
    async def _get_pool(self):
        """Return a live pool, or raise :class:`AuthStoreUnavailable`.

        A failed attempt parks the store for a cooldown window so a down
        MySQL does not add a connection timeout to every request.
        """
        if self._pool is not None:
            return self._pool
        if time.monotonic() < self._unavailable_until:
            raise AuthStoreUnavailable("MySQL unavailable (retry cooldown)")
        async with self._lock:
            if self._pool is not None:
                return self._pool
            try:
                import aiomysql

                self._pool = await asyncio.wait_for(
                    aiomysql.create_pool(
                        host=self._cfg.host,
                        port=self._cfg.port,
                        user=self._cfg.user,
                        password=self._cfg.password,
                        db=self._cfg.database,
                        minsize=self._cfg.pool_min,
                        maxsize=self._cfg.pool_max,
                        autocommit=True,
                        charset="utf8mb4",
                        pool_recycle=3600,
                    ),
                    timeout=5.0,
                )
            except Exception as exc:
                self._unavailable_until = time.monotonic() + _RETRY_COOLDOWN_SECONDS
                raise AuthStoreUnavailable(
                    f"cannot connect to MySQL at {self._cfg.host}:{self._cfg.port} ({exc})"
                ) from exc
            await self._init_schema()
            logger.info(
                "Auth store connected to MySQL at %s:%s/%s",
                self._cfg.host, self._cfg.port, self._cfg.database,
            )
            return self._pool

    async def _init_schema(self) -> None:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                for stmt in _SCHEMA_STATEMENTS:
                    await cur.execute(stmt)

    async def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    async def _fetchone(self, query: str, params: tuple = ()) -> Optional[dict]:
        pool = await self._get_pool()
        import aiomysql

        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(query, params)
                return await cur.fetchone()

    async def _execute(self, query: str, params: tuple = ()) -> int:
        """Run a write statement; returns affected row count."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                return cur.rowcount

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    async def create_user(self, username: str, password_hash: str) -> Optional[int]:
        """Insert a user; returns the new id, or None when the name is taken."""
        import pymysql

        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                        (username, password_hash),
                    )
                    return int(cur.lastrowid)
        except pymysql.err.IntegrityError:
            return None

    async def get_user_by_username(self, username: str) -> Optional[dict]:
        return await self._fetchone(
            "SELECT id, username, password_hash, created_at FROM users WHERE username = %s",
            (username,),
        )

    async def get_user_by_id(self, user_id: int) -> Optional[dict]:
        return await self._fetchone(
            "SELECT id, username, created_at FROM users WHERE id = %s",
            (user_id,),
        )

    async def touch_last_login(self, user_id: int) -> None:
        await self._execute(
            "UPDATE users SET last_login_at = %s WHERE id = %s",
            (_utcnow(), user_id),
        )

    # ------------------------------------------------------------------
    # Bearer tokens
    # ------------------------------------------------------------------
    async def create_token(self, user_id: int, token: str, ttl_days: int) -> None:
        expires_at = _utcnow() + timedelta(days=ttl_days)
        await self._execute(
            "INSERT INTO auth_tokens (token, user_id, expires_at) VALUES (%s, %s, %s)",
            (token, user_id, expires_at),
        )

    async def get_user_by_token(self, token: str) -> Optional[dict]:
        """Resolve a token to its user; expired tokens are deleted and ignored."""
        row = await self._fetchone(
            """
            SELECT t.token, t.expires_at, u.id, u.username, u.created_at
            FROM auth_tokens t JOIN users u ON u.id = t.user_id
            WHERE t.token = %s
            """,
            (token,),
        )
        if row is None:
            return None
        if row["expires_at"] <= _utcnow():
            await self.delete_token(token)
            return None
        created_at = row["created_at"]
        return {
            "id": row["id"],
            "username": row["username"],
            "created_at": created_at.isoformat() if created_at else None,
        }

    async def delete_token(self, token: str) -> int:
        return await self._execute("DELETE FROM auth_tokens WHERE token = %s", (token,))

    # ------------------------------------------------------------------
    # Anonymous trial quota
    # ------------------------------------------------------------------
    async def get_anon_used(self, anon_id: str) -> int:
        row = await self._fetchone(
            "SELECT used FROM anon_quota WHERE anon_id = %s", (anon_id,)
        )
        return int(row["used"]) if row else 0

    async def consume_anon_quota(self, anon_id: str, limit: int) -> tuple[bool, int]:
        """Atomically consume one free question.

        Returns ``(allowed, used)`` where ``used`` is the value after the
        attempt: when the limit is already reached ``allowed`` is False and
        ``used`` stays at its previous value.
        """
        pool = await self._get_pool()
        import aiomysql

        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "INSERT IGNORE INTO anon_quota (anon_id, used) VALUES (%s, 0)",
                    (anon_id,),
                )
                await cur.execute(
                    """
                    UPDATE anon_quota
                    SET used = used + 1, last_seen_at = %s
                    WHERE anon_id = %s AND used < %s
                    """,
                    (_utcnow(), anon_id, limit),
                )
                allowed = cur.rowcount == 1
                await cur.execute(
                    "SELECT used FROM anon_quota WHERE anon_id = %s", (anon_id,)
                )
                row = await cur.fetchone()
        used = int(row["used"]) if row else 0
        return allowed, used


_auth_store: Optional[AuthStore] = None


def get_auth_store() -> AuthStore:
    """Return the process-wide :class:`AuthStore` singleton."""
    global _auth_store
    if _auth_store is None:
        _auth_store = AuthStore()
    return _auth_store


async def close_auth_store() -> None:
    """Close the shared pool on shutdown; no-op when never initialised."""
    if _auth_store is not None:
        await _auth_store.close()
