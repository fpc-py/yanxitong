"""Multi-model routing factory with cost-aware dispatch.

The router maps a semantic *role* (``supervisor`` / ``lightweight`` /
``deep_reasoning``) onto a concrete model configured in
:class:`src.core.config.LLMConfig`, instantiating a lazily cached,
OpenAI-compatible :class:`~langchain_openai.ChatOpenAI` client behind it:

* ``qwen*`` models are routed through the DashScope OpenAI-compatible
  endpoint.
* ``deepseek*`` models are routed through the official DeepSeek endpoint.
* Anything else falls back to the stock OpenAI client.

In addition to dispatch, :class:`LLMRouter` keeps a lightweight cost ledger
so callers can attribute token usage and estimated spend per model after
each call (see :meth:`LLMRouter.track_usage` and
:meth:`LLMRouter.cost_summary`).

Usage::

    from src.core.llm_factory import get_llm

    llm = get_llm("lightweight")
    result = llm.invoke("Summarise this abstract: ...")
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from langchain_openai import ChatOpenAI

from src.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

ModelRole = Literal["supervisor", "lightweight", "deep_reasoning"]

#: Approximate list prices in USD per **one million** tokens, as
#: ``(prompt_price, completion_price)``.  Entries are keyed by model-name
#: prefix; unknown models are recorded with zero cost.  Keep this table in
#: sync with the providers' official pricing pages.
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "qwen-max": (1.6, 6.4),
    "qwen3.8-flash": (0.05, 0.4),
    "deepseek-v4-pro": (0.27, 1.10),
}

#: Sentinel used when no API key is configured (calls will fail upstream,
#: but local wiring and tests keep working).
_PLACEHOLDER_API_KEY = "sk-placeholder"


@dataclass
class ModelUsage:
    """Accumulated token usage and estimated cost for a single model."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        """Total prompt + completion tokens recorded for this model."""
        return self.prompt_tokens + self.completion_tokens


@dataclass
class CostLedger:
    """Thread-safe, per-model token/cost ledger.

    Attributes:
        usage: Mapping of model name to accumulated :class:`ModelUsage`.
        pricing: Mapping of model name to ``(prompt, completion)`` price
            per million tokens.
    """

    usage: dict[str, ModelUsage] = field(default_factory=dict)
    pricing: dict[str, tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_PRICING)
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _price_for(self, model: str) -> tuple[float, float]:
        """Return the price pair for ``model`` using longest-prefix matching.

        Args:
            model: Concrete model identifier, e.g. ``"qwen-max-latest"``.

        Returns:
            ``(prompt_price_per_mtok, completion_price_per_mtok)``;
            ``(0.0, 0.0)`` when the model is unknown.
        """
        best_prefix = ""
        for prefix in self.pricing:
            if model.startswith(prefix) and len(prefix) > len(best_prefix):
                best_prefix = prefix
        return self.pricing.get(best_prefix, (0.0, 0.0))

    def record(
        self, model: str, prompt_tokens: int, completion_tokens: int
    ) -> float:
        """Add one call's token usage to the ledger.

        Args:
            model: Model that produced the usage.
            prompt_tokens: Number of prompt (input) tokens.
            completion_tokens: Number of completion (output) tokens.

        Returns:
            Estimated cost, in USD, of *this call only*.
        """
        with self._lock:
            entry = self.usage.setdefault(model, ModelUsage())
            entry.prompt_tokens += prompt_tokens
            entry.completion_tokens += completion_tokens
            entry.calls += 1
            p_price, c_price = self._price_for(model)
        return (
            prompt_tokens * p_price + completion_tokens * c_price
        ) / 1_000_000

    def estimated_cost(self, model: str) -> float:
        """Estimated total USD spend recorded for one model."""
        entry = self.usage.get(model)
        if entry is None:
            return 0.0
        p_price, c_price = self._price_for(model)
        return (
            entry.prompt_tokens * p_price + entry.completion_tokens * c_price
        ) / 1_000_000

    @property
    def total_cost(self) -> float:
        """Estimated total USD spend across all models."""
        return sum(self.estimated_cost(m) for m in self.usage)

    def summary(self) -> dict[str, dict[str, Any]]:
        """Return a plain-dict snapshot of usage and cost per model.

        The mapping is suitable for logging, metrics export, or JSON APIs.
        """
        return {
            model: {
                "calls": entry.calls,
                "prompt_tokens": entry.prompt_tokens,
                "completion_tokens": entry.completion_tokens,
                "total_tokens": entry.total_tokens,
                "estimated_cost_usd": round(self.estimated_cost(model), 6),
            }
            for model, entry in self.usage.items()
        }

    def reset(self) -> None:
        """Clear all accumulated usage."""
        with self._lock:
            self.usage.clear()


class LLMRouter:
    """Routes requests to an appropriate LLM based on task role and cost.

    Instances cache one :class:`~langchain_openai.ChatOpenAI` client per
    ``(role, temperature)`` combination, so repeated calls inside a workflow
    are cheap. The router is intentionally provider-agnostic: it only knows
    which OpenAI-compatible base URL to use for each model family.

    Attributes:
        settings: Resolved application settings used for model selection.
        ledger: :class:`CostLedger` tracking token usage and estimated spend.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        """Create a router.

        Args:
            settings: Optional explicit settings; defaults to the global
                :func:`get_settings()` singleton.
        """
        self.settings: Settings = settings or get_settings()
        self._models: dict[str, ChatOpenAI] = {}
        self.ledger: CostLedger = CostLedger()

    # ------------------------------------------------------------------
    # Client construction
    # ------------------------------------------------------------------
    def _api_key(self) -> str:
        """DashScope/DeepSeek key from settings, with a placeholder fallback."""
        key = self.settings.llm.dashscope_api_key
        if not key:
            logger.warning(
                "DASHSCOPE_API_KEY is not configured; using a placeholder. "
                "Live model calls will fail until the key is set."
            )
            return _PLACEHOLDER_API_KEY
        return key

    def get_llm(self, role: ModelRole = "lightweight", **kwargs: Any) -> ChatOpenAI:
        """Get or create the cached LLM instance bound to ``role``.

        Args:
            role: Semantic task role; resolved to a concrete model name via
                :class:`~src.core.config.LLMConfig`.
            **kwargs: Extra keyword arguments forwarded to ``ChatOpenAI``.
                ``temperature`` and ``max_tokens`` are honoured here and
                otherwise default to the configured values.

        Returns:
            A ready-to-invoke ``ChatOpenAI`` client.

        Raises:
            ValueError: If ``role`` is not one of ``supervisor``,
                ``lightweight`` or ``deep_reasoning``.
        """
        cfg = self.settings.llm
        if not hasattr(cfg, role):
            raise ValueError(
                f"Unknown model role {role!r}; expected one of "
                f"'supervisor', 'lightweight', 'deep_reasoning'."
            )

        cache_key = f"{role}_{kwargs.get('temperature', '')}"
        if cache_key in self._models:
            return self._models[cache_key]

        model_name: str = getattr(cfg, role)
        temperature: float = kwargs.pop("temperature", cfg.temperature)
        max_tokens: int = kwargs.pop("max_tokens", cfg.max_tokens)

        if "qwen" in model_name:
            llm = ChatOpenAI(
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=self._api_key(),
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                **kwargs,
            )
        elif "deepseek" in model_name:
            # 本项目统一使用 DashScope 的 OpenAI 兼容端点：dashscope 上也提供了
            # deepseek 系模型，且只有 DASHSCOPE_API_KEY 可用。若改用官方 DeepSeek
            # 端点（api.deepseek.com）会因 key 不匹配而 401。
            llm = ChatOpenAI(
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=self._api_key(),
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                **kwargs,
            )
        else:
            llm = ChatOpenAI(
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

        self._models[cache_key] = llm
        return llm

    # ------------------------------------------------------------------
    # Role shortcuts
    # ------------------------------------------------------------------
    @property
    def supervisor(self) -> ChatOpenAI:
        """Client for orchestration / final-answer synthesis (``qwen-max`` by default)."""
        return self.get_llm("supervisor")

    @property
    def lightweight(self) -> ChatOpenAI:
        """Client for high-volume, low-cost steps (``qwen3.8-flash`` by default)."""
        return self.get_llm("lightweight")

    @property
    def deep_reasoning(self) -> ChatOpenAI:
        """Client for planning/verification (``deepseek-v4-pro`` at temp 0.3 by default)."""
        return self.get_llm("deep_reasoning", temperature=0.3)

    # ------------------------------------------------------------------
    # Cost tracking
    # ------------------------------------------------------------------
    def track_usage(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Record token usage for one completed call.

        Accepts raw token counts so it can be fed from any response object
        (LangChain ``usage_metadata``, provider JSON, or tests).

        Args:
            model: Concrete model name that served the call.
            prompt_tokens: Input token count.
            completion_tokens: Output token count.

        Returns:
            Estimated USD cost of this single call.
        """
        return self.ledger.record(model, prompt_tokens, completion_tokens)

    def track_response(self, response: Any, model: Optional[str] = None) -> float:
        """Extract usage from a LangChain response and record it.

        Reads ``response.usage_metadata`` (or ``response.response_metadata``
        fallbacks) when present. Silently records nothing for responses that
        carry no usage information (e.g. streaming aggregates not yet closed).

        Args:
            response: A LangChain ``AIMessage`` (batch) or a single generated
                message.
            model: Optional model-name override; when omitted it is read from
                response metadata.

        Returns:
            Estimated USD cost of the tracked call (``0.0`` when no usage
                could be found).
        """
        usage = getattr(response, "usage_metadata", None) or {}
        prompt_tokens = int(usage.get("input_tokens", 0) or 0)
        completion_tokens = int(usage.get("output_tokens", 0) or 0)
        resolved_model = model or getattr(response, "response_metadata", {}).get(
            "model_name", ""
        )
        if not resolved_model:
            logger.debug("track_response: could not resolve model name for usage")
            return 0.0
        return self.track_usage(resolved_model, prompt_tokens, completion_tokens)

    def cost_summary(self) -> dict[str, dict[str, Any]]:
        """Per-model token usage and estimated spend, as a plain dict."""
        return self.ledger.summary()

    @property
    def total_cost_usd(self) -> float:
        """Estimated total USD spend across every tracked call."""
        return self.ledger.total_cost

    def reset_cost(self) -> None:
        """Clear the cost ledger (e.g. between evaluation runs)."""
        self.ledger.reset()


# --- module-level convenience singleton -------------------------------------

_router: Optional[LLMRouter] = None


def get_llm(role: ModelRole = "lightweight", **kwargs: Any) -> ChatOpenAI:
    """Module-level shortcut returning a client from the global router.

    Args:
        role: Semantic task role to route to.
        **kwargs: Forwarded to :meth:`LLMRouter.get_llm`.

    Returns:
        A cached ``ChatOpenAI`` instance for the requested role.
    """
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router.get_llm(role, **kwargs)


def get_router() -> LLMRouter:
    """Return the global :class:`LLMRouter` singleton, creating it lazily."""
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router


def reset_router() -> None:
    """Drop the global router singleton (used by tests / hot config reload)."""
    global _router
    _router = None
