"""Shared exception hierarchy for v3.0."""


class YanxitongError(Exception):
    """Base class for all application errors."""
    def __init__(self, message: str = "", detail: dict = None, retryable: bool = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        self._retryable = retryable

    @property
    def retryable(self) -> bool:
        if self._retryable is not None:
            return self._retryable
        return False

    def to_dict(self) -> dict:
        return {"type": type(self).__name__, "message": self.message, "detail": self.detail}


class AgentExecutionError(YanxitongError):
    """Agent execution failed."""
    def __init__(self, message: str = "", agent: str = "", **kwargs):
        super().__init__(message, **kwargs)
        self.agent = agent
    @property
    def retryable(self) -> bool:
        return self._retryable if self._retryable is not None else True


class CircuitBreakerOpenError(YanxitongError):
    """Circuit breaker is OPEN, rejecting calls."""
    def __init__(self, message: str = "", failure_count: int = 0, retry_after_seconds: float = 60.0, **kwargs):
        super().__init__(message, retryable=False, **kwargs)
        self.failure_count = failure_count
        self.retry_after_seconds = retry_after_seconds


class ConfigError(YanxitongError):
    """Invalid or missing configuration."""


class ConfigurationError(ConfigError):
    """Configuration error (alias for cross-module compatibility)."""


class RetrievalError(YanxitongError):
    """Literature-source retrieval failed after retries."""
    @property
    def retryable(self) -> bool:
        return self._retryable if self._retryable is not None else True


class ParseError(YanxitongError):
    """Document parsing (PDF/HTML) failed."""


class SandboxError(YanxitongError):
    """Code sandbox execution failed."""
    def __init__(self, message: str = "", exit_code: int = None, **kwargs):
        super().__init__(message, retryable=True, **kwargs)
        self.exit_code = exit_code


class SandboxTimeoutError(SandboxError):
    """Sandbox execution exceeded its wall-clock budget."""
    def __init__(self, message: str = "", timeout_seconds: int = 30, **kwargs):
        super().__init__(message, retryable=True, **kwargs)
        self.timeout_seconds = timeout_seconds


class SandboxUnavailableError(SandboxError):
    """No code sandbox available: Docker daemon unreachable and in-process
    fallback (which would run LLM-generated code on the host) is not
    explicitly permitted by the environment.

    This is a deliberate production guard: silently degrading to an
    in-process sandbox would execute AI-generated code on the host machine,
    which is a remote-code-execution risk. Callers must surface this as a
    503 / failed-agent-result instead of pretending the run succeeded.
    """
    def __init__(self, message: str = "", **kwargs):
        # 绕过 SandboxError.__init__ 里写死的 retryable=True（它会把 retryable
        # 再塞进 kwargs，导致与本处的 retryable=True 冲突）。
        kwargs.pop("retryable", None)
        YanxitongError.__init__(self, message, retryable=True, **kwargs)


class LLMError(YanxitongError):
    """Upstream LLM call failed."""
    @property
    def retryable(self) -> bool:
        return self._retryable if self._retryable is not None else True


class KnowledgeGraphError(YanxitongError):
    """Neo4j / knowledge-graph storage failed."""


class KnowledgeBaseError(YanxitongError):
    """Vector / graph store operation failed."""
    def __init__(self, message: str = "", store: str = "", **kwargs):
        super().__init__(message, retryable=True, **kwargs)
        self.store = store


class SafetyViolationError(YanxitongError):
    """Safety guard blocked content."""
    def __init__(self, message: str = "", rule: str = "", **kwargs):
        super().__init__(message, retryable=False, **kwargs)
        self.rule = rule


class RateLimitExceededError(YanxitongError):
    """Rate limit exceeded."""
    def __init__(self, message: str = "", retry_after_seconds: float = 60.0, **kwargs):
        super().__init__(message, retryable=True, **kwargs)
        self.retry_after_seconds = retry_after_seconds