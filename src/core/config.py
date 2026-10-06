"""Global configuration loader with YAML + environment-variable overrides.

Layered configuration, from lowest to highest priority:

1. Hard-coded defaults declared on the ``*Config`` classes below.
2. Values loaded from ``config.yaml`` at the repository root.
3. Environment variables (``LLM_*``, ``RETRIEVER_*``, ``KG_*``, ``SANDBOX_*``,
   ...), which ``pydantic-settings`` applies on top of the YAML values.

Usage::

    from src.core.config import get_settings

    settings = get_settings()
    print(settings.llm.supervisor)
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseSettings):
    """Model selection and generation parameters for the agent roles.

    Environment variables use the ``LLM_`` prefix (e.g. ``LLM_TEMPERATURE=0.2``).
    The DashScope API key may also be supplied via the unprefixed
    ``DASHSCOPE_API_KEY`` variable, which is the canonical name used across
    the rest of the stack.
    """

    model_config = SettingsConfigDict(env_prefix="LLM_", populate_by_name=True, extra="ignore")

    supervisor: str = "qwen-max"
    """Model used by the supervisor / orchestrator agent."""

    lightweight: str = "qwen3.8-flash"
    """Fast, low-cost model for high-volume steps (parsing, extraction)."""

    deep_reasoning: str = "deepseek-v4-pro"
    """Strong reasoning model for planning, review and final synthesis."""

    temperature: float = 0.1
    """Default sampling temperature for deterministic, factual outputs."""

    max_tokens: int = 4096
    """Default completion budget per LLM call."""

    dashscope_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "DASHSCOPE_API_KEY",
            "LLM_DASHSCOPE_API_KEY",
            "dashscope_api_key",
        ),
    )
    """API key for DashScope-compatible endpoints (inject via env, never commit)."""


class RetrieverConfig(BaseSettings):
    """Parameters for literature retrieval and vector indexing."""

    model_config = SettingsConfigDict(env_prefix="RETRIEVER_", populate_by_name=True, extra="ignore")

    arxiv_max_results: int = 20
    """Maximum number of records fetched per arXiv query."""

    semantic_scholar_max_results: int = 20
    """Maximum number of records fetched per Semantic Scholar query."""

    embedding_model: str = "BAAI/bge-m3"
    """Sentence-transformers model used to embed papers and passages."""

    vector_dim: int = 1024
    """Embedding dimensionality; must match ``embedding_model`` output."""

    top_k: int = 10
    """Number of documents returned by vector similarity search."""

    index_path: str = "data/faiss"
    """On-disk directory for the paper FAISS index (index.faiss + docs.json)."""


class KGBuilderConfig(BaseSettings):
    """Ontology and extraction limits for the knowledge-graph builder."""

    model_config = SettingsConfigDict(env_prefix="KG_", populate_by_name=True, extra="ignore")

    entity_types: list[str] = Field(
        default_factory=lambda: [
            "Method",
            "Dataset",
            "Metric",
            "Model",
            "Theory",
            "Author",
            "Publication",
            "Field",
            "Tool",
            "Finding",
        ]
    )
    """Closed set of entity labels the extractor is allowed to emit."""

    relation_types: list[str] = Field(
        default_factory=lambda: [
            "PROPOSES",
            "EVALUATES",
            "OUTPERFORMS",
            "CITES",
            "USES_DATASET",
            "IMPROVES",
            "COMPARES_WITH",
            "BELONGS_TO",
        ]
    )
    """Closed set of relation types the extractor is allowed to emit."""

    max_entities_per_doc: int = 50
    """Hard cap on entities extracted from a single document."""


class SandboxConfig(BaseSettings):
    """Resource limits for the containerised code-execution sandbox."""

    model_config = SettingsConfigDict(env_prefix="SANDBOX_", populate_by_name=True, extra="ignore")

    image: str = "yanxitong-sandbox:latest"
    """Docker image used to run untrusted generated code."""

    memory_limit: str = "512m"
    """Per-container memory cap passed to Docker."""

    cpu_limit: float = 1.0
    """Number of CPUs granted to each sandbox container."""

    timeout: int = 30
    """Wall-clock seconds before a running snippet is killed."""

    network_disabled: bool = True
    """Whether the sandbox container runs without network access."""


class SafetyConfig(BaseSettings):
    """Thresholds for hallucination guards and self-consistency checks."""

    model_config = SettingsConfigDict(env_prefix="SAFETY_", populate_by_name=True, extra="ignore")

    confidence_threshold: float = 0.6
    """Answers below this confidence trigger escalation or refusal."""

    self_consistency_samples: int = 3
    """Number of independent samples used in self-consistency voting."""


class CacheConfig(BaseSettings):
    """Answer-cache semantics for the retrieval/Q&A pipeline."""

    model_config = SettingsConfigDict(env_prefix="CACHE_", populate_by_name=True, extra="ignore")

    ttl: int = 3600
    """Time-to-live of a cached answer, in seconds."""

    similarity_threshold: float = 0.92
    """Embedding similarity above which a query hits the cache."""


class RateLimitConfig(BaseSettings):
    """Inbound request throttling for the API layer."""

    model_config = SettingsConfigDict(env_prefix="RATE_", populate_by_name=True, extra="ignore")

    requests_per_minute: int = 60
    """Maximum requests accepted per client per minute."""


class MySQLConfig(BaseSettings):
    """Connection settings for the MySQL account/quota store."""

    model_config = SettingsConfigDict(env_prefix="MYSQL_", populate_by_name=True, extra="ignore")

    host: str = "127.0.0.1"
    """MySQL host; docker-compose app service overrides to ``mysql``."""

    port: int = 3309
    """Host port; compose maps container 3306 to 3309 to avoid clashes with other local MySQLs."""

    user: str = "yanxitong"
    """Application login user."""

    password: str = "yanxitong"
    """Application login password (override via ``MYSQL_PASSWORD``)."""

    database: str = "yanxitong"
    """Schema name holding users / tokens / anonymous quota tables."""

    pool_min: int = 1
    """Minimum pooled connections kept by aiomysql."""

    pool_max: int = 5
    """Upper bound of pooled connections."""


class AuthConfig(BaseSettings):
    """Registration, token lifetime and anonymous trial quota policy."""

    model_config = SettingsConfigDict(env_prefix="AUTH_", populate_by_name=True, extra="ignore")

    enabled: bool = True
    """Master switch; disabling turns every auth endpoint into 503."""

    anonymous_limit: int = 5
    """Free Q&A requests granted to visitors who have not signed in."""

    token_ttl_days: int = 7
    """Lifetime of an issued bearer token, in days."""

    username_min_length: int = 2
    """Minimum accepted username length."""

    username_max_length: int = 32
    """Maximum accepted username length."""

    password_min_length: int = 6
    """Minimum accepted password length."""


class Neo4jConfig(BaseSettings):
    """Connection settings for the Neo4j knowledge-graph store."""

    model_config = SettingsConfigDict(env_prefix="NEO4J_", populate_by_name=True, extra="ignore")

    uri: str = "bolt://localhost:7687"
    """Bolt endpoint of the Neo4j server."""

    user: str = "neo4j"
    """Neo4j login user."""

    password: str = "password"
    """Neo4j login password (override via ``NEO4J_PASSWORD``)."""


class RedisConfig(BaseSettings):
    """Connection settings for the Redis cache / message broker."""

    model_config = SettingsConfigDict(env_prefix="REDIS_", populate_by_name=True, extra="ignore")

    url: str = "redis://localhost:6379/0"
    """Redis connection URL (``redis://``, ``rediss://`` or unix socket)."""


class AppConfig(BaseSettings):
    """Application-server runtime settings."""

    model_config = SettingsConfigDict(env_prefix="APP_", populate_by_name=True, extra="ignore")

    host: str = "0.0.0.0"
    """Bind address for the FastAPI server."""

    port: int = 8000
    """Listen port for the FastAPI server."""

    debug: bool = False
    """Enable debug-only behaviour (verbose errors, hot reload)."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    """Root logging level."""

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value):
        """Accept case-insensitive log levels from YAML/env (e.g. ``info``)."""
        return value.upper() if isinstance(value, str) else value


class Settings(BaseSettings):
    """Root settings aggregating every subsystem config.

    Each nested block keeps its own environment prefix, so ``LLM_MODEL``-style
    variables continue to override the corresponding leaf fields regardless of
    how the YAML file is structured.
    """

    model_config = SettingsConfigDict(env_prefix="YANXITONG_", populate_by_name=True, extra="ignore")

    llm: LLMConfig = Field(default_factory=LLMConfig)
    retriever: RetrieverConfig = Field(default_factory=RetrieverConfig)
    kg: KGBuilderConfig = Field(default_factory=KGBuilderConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    neo4j: Neo4jConfig = Field(default_factory=Neo4jConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    mysql: MySQLConfig = Field(default_factory=MySQLConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    app: AppConfig = Field(default_factory=AppConfig)

    @classmethod
    def from_yaml(cls, yaml_path: Optional[Path] = None) -> "Settings":
        """Load settings from a YAML file, with env-var overrides applied.

        Args:
            yaml_path: Path to the YAML configuration file. Defaults to
                ``<repo_root>/config.yaml`` (three levels above this module).

        Returns:
            A fully validated :class:`Settings` instance. Missing or empty
            YAML files simply fall back to defaults plus env overrides.
        """
        if yaml_path is None:
            yaml_path = Path(__file__).parent.parent.parent / "config.yaml"

        yaml_data: dict = {}
        if yaml_path.exists():
            with open(yaml_path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f) or {}

        return cls(**yaml_data)


# --- module-level singleton -------------------------------------------------

_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` singleton, building it lazily.

    Returns:
        The cached settings instance. The first call parses ``config.yaml``
        and applies environment overrides; later calls are O(1).
    """
    global _settings
    if _settings is None:
        _settings = Settings.from_yaml()
    return _settings


def reload_settings(yaml_path: Optional[Path] = None) -> Settings:
    """Force a rebuild of the settings singleton.

    Useful in tests or after mutating the environment at runtime.

    Args:
        yaml_path: Optional explicit YAML path for the rebuilt settings.

    Returns:
        The freshly parsed settings instance, now cached globally.
    """
    global _settings
    _settings = Settings.from_yaml(yaml_path)
    return _settings
