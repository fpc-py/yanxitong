"""Base agent class with circuit breaker, audit logging, and confidence tracking.

Every agent in the YanXiTong multi-agent system derives from BaseAgent, which
provides:

    - Circuit breaking: repeated downstream failures trip a CircuitBreaker
      that short-circuits execution (optionally serving a registered fallback)
      until the dependency has a chance to recover.
    - Audit logging: every meaningful action is appended to an in-memory
      AuditEntry trail and mirrored to the standard logging channel, tagged
      with a short per-execution trace id.
    - Confidence tracking: results are returned as AgentResult objects
      carrying a 0..1 confidence score and the citations backing the output.
"""

from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

from src.core.exceptions import AgentExecutionError, CircuitBreakerOpenError

logger = logging.getLogger(__name__)

__all__ = [
    "CircuitState",
    "CircuitBreaker",
    "AuditEntry",
    "AgentResult",
    "BaseAgent",
]


class CircuitState(Enum):
    """Lifecycle states of a CircuitBreaker."""

    CLOSED = "closed"          # Normal operation; failures are counted.
    OPEN = "open"              # Tripped; calls short-circuit to fallback/error.
    HALF_OPEN = "half_open"    # Exactly one probe call is allowed through.


@dataclass
class CircuitBreaker:
    """Protects a flaky downstream dependency.

    State machine:

        - CLOSED: calls pass through.  After failure_threshold *consecutive*
          failures the breaker trips to OPEN.
        - OPEN: calls are short-circuited immediately.  If fallback is
          registered it is invoked; otherwise CircuitBreakerOpenError is
          raised.  Once recovery_timeout seconds have elapsed since the last
          failure the breaker moves to HALF_OPEN.
        - HALF_OPEN: the next call is allowed through as a probe: success
          resets the breaker to CLOSED, failure returns it to OPEN.
    """

    failure_threshold: int = 3
    recovery_timeout: float = 60.0
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    fallback: Optional[Callable[..., Awaitable[Any]]] = None

    def _should_attempt_recovery(self) -> bool:
        """Return True once the recovery timeout has elapsed."""
        return time.monotonic() - self.last_failure_time >= self.recovery_timeout

    async def call(
        self,
        func: Callable[..., Awaitable[Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute func under breaker supervision.

        Args:
            func: The coroutine function guarding the remote call.
            *args / **kwargs: Forwarded verbatim to func (and to fallback).

        Raises:
            CircuitBreakerOpenError: Breaker is OPEN and no fallback exists.
            Exception: Whatever func raised, after the failure is recorded.
        """
        if self.state == CircuitState.OPEN:
            if self._should_attempt_recovery():
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker: OPEN -> HALF_OPEN (probing)")
            else:
                if self.fallback is not None:
                    logger.warning("Circuit breaker OPEN, using fallback")
                    return await self.fallback(*args, **kwargs)
                remaining = self.recovery_timeout - (
                    time.monotonic() - self.last_failure_time
                )
                raise CircuitBreakerOpenError(
                    f"Circuit breaker is OPEN. {self.failure_count} failures, "
                    f"retry after {max(remaining, 0.0):.0f}s"
                )

        try:
            result = await func(*args, **kwargs)
        except Exception as error:
            self._on_failure(error)
            raise
        self._on_success()
        return result

    def _on_success(self) -> None:
        """Record a successful call: reset failures and close the breaker."""
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self.last_success_time = time.monotonic()

    def _on_failure(self, error: Exception) -> None:
        """Record a failed call; trip the breaker once past the threshold."""
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.error(
                "Circuit breaker tripped OPEN after %d failures: %s",
                self.failure_count,
                error,
            )


@dataclass
class AuditEntry:
    """One immutable record in an agent's audit trail."""

    timestamp: float
    agent_name: str
    action: str
    detail: dict[str, Any]
    trace_id: str


@dataclass
class AgentResult:
    """Standard result container for all agent executions."""

    success: bool
    data: Any = None
    error: Optional[str] = None
    confidence: float = 1.0
    citations: list[dict[str, Any]] = field(default_factory=list)
    audit_trail: list[AuditEntry] = field(default_factory=list)
    execution_time_ms: float = 0.0


class BaseAgent(ABC):
    """Abstract base for all agents with circuit breaker, audit, and confidence.

    Subclasses set name / description / model_role and implement
    _execute_impl(state) -> AgentResult.  Prompt-driven subclasses typically
    also provide _build_prompt(state) -> str and
    _parse_response(response: str, state) -> dict helpers, calling _call_llm
    from within _execute_impl.
    """

    name: str = "base"
    description: str = "Base agent"
    model_role: str = "lightweight"

    def __init__(self) -> None:
        self.circuit_breaker = CircuitBreaker()
        self.audit_log: list[AuditEntry] = []
        self._llm: Any = None

    @property
    def llm(self) -> Any:
        """Lazily resolved chat model for this agent's model_role."""
        if self._llm is None:
            from src.core.llm_factory import get_llm

            self._llm = get_llm(self.model_role)
        return self._llm

    async def execute(self, state: dict[str, Any]) -> AgentResult:
        """Main execution entry point, wrapped with the circuit breaker.

        Unexpected internal exceptions are converted into a failed
        AgentResult; only infrastructure-level errors (AgentExecutionError,
        CircuitBreakerOpenError) are re-raised so the orchestrator can react
        to them.
        """
        start = time.perf_counter()
        trace_id = str(uuid.uuid4())[:8]
        self._audit(
            "execute_start",
            {"state_keys": list(state.keys()) if state else []},
            trace_id,
        )
        try:
            result = await self.circuit_breaker.call(self._execute_impl, state)
            result.audit_trail = list(self.audit_log[-10:])
            self._audit("execute_complete", {"success": result.success}, trace_id)
        except (AgentExecutionError, CircuitBreakerOpenError):
            raise
        except Exception as exc:
            self._audit("execute_error", {"error": str(exc)}, trace_id)
            logger.exception("Agent %s failed: %s", self.name, exc)
            result = AgentResult(success=False, error=str(exc), confidence=0.0)
        result.execution_time_ms = (time.perf_counter() - start) * 1000.0
        return result

    @abstractmethod
    async def _execute_impl(self, state: dict[str, Any]) -> AgentResult:
        """Core execution logic -- subclasses override this."""
        raise NotImplementedError

    def _audit(
        self, action: str, detail: dict[str, Any], trace_id: str = ""
    ) -> None:
        """Append an AuditEntry to the trail and mirror it to the logger."""
        entry = AuditEntry(
            timestamp=time.time(),
            agent_name=self.name,
            action=action,
            detail=detail,
            trace_id=trace_id,
        )
        self.audit_log.append(entry)
        logger.debug("[%s] %s: %s", self.name, action, detail)

    async def _call_llm(
        self,
        prompt: str,
        system_prompt: str = "",
        json_mode: bool = False,
        enable_thinking: bool = True,
    ) -> str:
        """Safe LLM call with semantic-cache read-through.

        Checks the shared SemanticCache first; on a miss, invokes the model
        and stores the completion for future reuse.

        Args:
            prompt: The user-level prompt text.
            system_prompt: Optional system instructions prepended to the call
                and folded into the cache key.
            json_mode: When True, asks the provider for a strict JSON object
                (``response_format={"type": "json_object"}``). Note that some
                providers require the word "json" to appear in the messages.
            enable_thinking: Set False for DashScope reasoning models to skip the
                hidden chain-of-thought. Otherwise the reasoning can consume the
                whole token budget and leave the visible answer empty.

        Returns:
            The model's completion text.
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        from src.core.cache import get_cache

        messages: list[Any] = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))

        cache = await get_cache()
        cache_key = ("json::" if json_mode else "") + ("nothink::" if not enable_thinking else "") + system_prompt + prompt
        cached = await cache.get(cache_key)
        if cached is not None:
            self._audit("llm_cache_hit", {"prompt_len": len(prompt)})
            return cached

        llm = self.llm
        bind_kwargs: dict[str, Any] = {}
        if json_mode:
            bind_kwargs["response_format"] = {"type": "json_object"}
        if json_mode or not enable_thinking:
            bind_kwargs["extra_body"] = {"enable_thinking": False}
        if bind_kwargs:
            llm = llm.bind(**bind_kwargs)

        response = await llm.ainvoke(messages)
        completion: str = (
            response.content
            if isinstance(response.content, str)
            else str(response.content)
        )

        await cache.set(cache_key, completion)
        self._audit(
            "llm_call",
            {"prompt_len": len(prompt), "response_len": len(completion)},
        )
        return completion

    def set_fallback(self, fallback: Callable[..., Awaitable[Any]]) -> None:
        """Register the coroutine invoked when the breaker is OPEN."""
        self.circuit_breaker.fallback = fallback
