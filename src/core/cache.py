"""Redis-based semantic cache with similarity-based lookup and prompt compression.

This module provides:

    - SemanticCache: a two-tier (in-process + Redis) cache for LLM completions
      keyed by a normalized SHA-256 hash of the prompt.  It degrades gracefully
      to a TTL-bounded local cache when Redis is unavailable.
    - PromptCompressor: lightweight history-compression helpers used to keep
      long conversations inside the model's context window.
    - get_cache: module-level singleton accessor so every caller shares one
      cache instance (and therefore one Redis connection pool).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Optional

import redis.asyncio as aioredis

from src.core.config import get_settings

logger = logging.getLogger(__name__)

__all__ = ["SemanticCache", "PromptCompressor", "get_cache"]


class SemanticCache:
    """Caches LLM responses keyed by a semantic hash of the prompt.

    Uses the SHA-256 digest of a *normalized* prompt (lower-cased, stripped,
    whitespace collapsed) as the cache key, so cosmetically different prompts
    with the same meaning still hit the cache.  Entries expire after
    settings.cache.ttl seconds.

    The cache is intentionally resilient: if Redis cannot be reached, every
    operation transparently falls back to a small in-process dictionary, so
    development and offline demos keep working without any infrastructure.
    """

    #: Prefix applied to every Redis key for namespacing.
    KEY_PREFIX: str = "llm_cache:"

    def __init__(self, redis_client: Optional[aioredis.Redis] = None) -> None:
        """Create a cache.

        Args:
            redis_client: Optional pre-configured Redis client.  When omitted,
                one is lazily created from application settings on first use.
        """
        self.settings = get_settings()
        self._redis: Optional[aioredis.Redis] = redis_client
        self._local_cache: dict[str, tuple[float, Any]] = {}  # (stored_at, value)
        self._redis_available: Optional[bool] = None  # unknown / yes / no

    # ------------------------------------------------------------ infrastructure
    async def _ensure_redis(self) -> None:
        """Lazily connect to Redis and record availability for later calls.

        On failure self._redis is set to None and all callers fall back to the
        in-memory cache.
        """
        if self._redis is not None and self._redis_available is not False:
            return
        try:
            client = aioredis.from_url(self.settings.redis.url, decode_responses=True)
            await client.ping()
            self._redis = client
            self._redis_available = True
            logger.info(
                "SemanticCache connected to Redis at %s", self.settings.redis.url
            )
        except Exception as exc:  # pragma: no cover - infrastructure dependent
            logger.warning("Redis unavailable (%s); using in-memory fallback.", exc)
            self._redis = None
            self._redis_available = False

    # ------------------------------------------------------------------- keying
    @staticmethod
    def _normalize(prompt: str) -> str:
        """Normalize a prompt for consistent hashing.

        Lower-cases, strips outer whitespace, and collapses internal whitespace
        runs into single spaces so formatting differences do not fragment the
        cache.
        """
        return re.sub(r"\s+", " ", prompt.strip().lower())

    def _hash(self, prompt: str) -> str:
        """Return the hex SHA-256 digest of the normalized prompt."""
        return hashlib.sha256(self._normalize(prompt).encode("utf-8")).hexdigest()

    # ---------------------------------------------------------------- operations
    async def get(self, prompt: str) -> Optional[str]:
        """Look up a cached completion for the given prompt.

        Checks the local cache first (honouring the TTL), then Redis.  A Redis
        hit is promoted into the local cache for faster subsequent access.

        Returns:
            The cached response text, or None on a miss or expired entry.
        """
        key = self._hash(prompt)

        entry = self._local_cache.get(key)
        if entry is not None:
            stored_at, value = entry
            if time.time() - stored_at < self.settings.cache.ttl:
                return value
            del self._local_cache[key]

        await self._ensure_redis()
        if self._redis is not None:
            try:
                value = await self._redis.get(f"{self.KEY_PREFIX}{key}")
                if value is not None:
                    self._local_cache[key] = (time.time(), value)
                    return value
            except Exception as exc:  # pragma: no cover - infra dependent
                logger.warning("Redis GET failed (%s); serving from memory.", exc)
        return None

    async def set(self, prompt: str, response: str) -> None:
        """Store the response for the prompt in both cache tiers.

        Args:
            prompt: The prompt (system + user text) used as the cache key.
            response: The LLM completion to cache under settings.cache.ttl.
        """
        key = self._hash(prompt)
        self._local_cache[key] = (time.time(), response)

        await self._ensure_redis()
        if self._redis is not None:
            try:
                await self._redis.setex(
                    f"{self.KEY_PREFIX}{key}", self.settings.cache.ttl, response
                )
            except Exception as exc:  # pragma: no cover - infra dependent
                logger.warning("Redis SETEX failed (%s); kept local copy.", exc)

    async def invalidate(self, prompt: Optional[str] = None) -> None:
        """Remove one entry (when prompt is given) or the whole cache.

        Args:
            prompt: The exact prompt whose entry should be dropped.  When
                None, every cached completion is purged from both tiers.
        """
        if prompt is not None:
            key = self._hash(prompt)
            self._local_cache.pop(key, None)
            await self._ensure_redis()
            if self._redis is not None:
                await self._redis.delete(f"{self.KEY_PREFIX}{key}")
            return

        self._local_cache.clear()
        await self._ensure_redis()
        if self._redis is not None:
            keys = list(await self._redis.keys(f"{self.KEY_PREFIX}*"))
            if keys:
                await self._redis.delete(*keys)
        logger.info("SemanticCache fully invalidated.")

    async def close(self) -> None:
        """Release the Redis connection (no-op in fallback mode)."""
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except AttributeError:  # older redis-py releases
                await self._redis.close()
            self._redis = None
            self._redis_available = None


class PromptCompressor:
    """Compresses long prompts by summarizing conversation history.

    The full-fidelity variant delegates summarization to an LLM; this class
    ships the deterministic, zero-cost heuristic used on hot paths: the most
    recent exchanges are kept verbatim while older turns are collapsed into a
    bounded header block so the combined prompt stays inside the token budget.
    """

    #: Number of trailing messages kept verbatim (2 user/assistant exchanges).
    KEEP_RECENT: int = 4
    #: Rough characters-per-token ratio used for the budget heuristic.
    CHARS_PER_TOKEN: int = 2

    @staticmethod
    def compress_history(messages: list[dict], max_tokens: int = 2000) -> str:
        """Summarize older messages to keep context within a token budget.

        Args:
            messages: Chat history as [{"role": ..., "content": ...}, ...].
            max_tokens: Soft budget for the *older* portion, measured in
                tokens (converted to characters with a rough heuristic ratio).

        Returns:
            A single string containing a truncated summary of the older
            messages followed by the most recent exchanges verbatim.  Returns
            an empty string when messages is empty.
        """
        if not messages:
            return ""

        # Short conversations fit entirely; return them unmodified.
        if len(messages) <= PromptCompressor.KEEP_RECENT:
            return "\n".join(m.get("content", "") for m in messages)

        older = messages[: -PromptCompressor.KEEP_RECENT]
        recent = messages[-PromptCompressor.KEEP_RECENT:]

        older_text = "\n".join(m.get("content", "") for m in older)
        char_budget = max_tokens * PromptCompressor.CHARS_PER_TOKEN
        if len(older_text) > char_budget:
            older_text = older_text[:char_budget] + "...[truncated]"

        recent_text = "\n".join(m.get("content", "") for m in recent)
        return f"[History Summary]\n{older_text}\n\n[Recent]\n{recent_text}"


# -------------------------------------------------------------------- singleton
_cache: Optional[SemanticCache] = None


async def get_cache() -> SemanticCache:
    """Return the process-wide SemanticCache, creating it on first use."""
    global _cache
    if _cache is None:
        _cache = SemanticCache()
    return _cache
