"""Prometheus metrics for 研析通 v2.0 — requests, latency, tokens, cache, errors."""

import time, logging
from typing import Optional
from contextlib import contextmanager
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response

logger = logging.getLogger(__name__)

# Request metrics
REQUEST_COUNT = Counter(
    "yanxitong_requests_total", "Total requests",
    ["endpoint", "method", "status"],
)
REQUEST_LATENCY = Histogram(
    "yanxitong_request_latency_seconds", "Request latency",
    ["endpoint"], buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)
ACTIVE_REQUESTS = Gauge("yanxitong_active_requests", "Currently active requests")

# Agent metrics
AGENT_CALL_COUNT = Counter(
    "yanxitong_agent_calls_total", "Agent calls",
    ["agent_name", "status"],
)
AGENT_LATENCY = Histogram(
    "yanxitong_agent_latency_seconds", "Agent execution latency",
    ["agent_name"], buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
)

# LLM metrics
LLM_CALL_COUNT = Counter(
    "yanxitong_llm_calls_total", "LLM API calls",
    ["model", "role"],
)
LLM_TOKEN_COUNT = Counter(
    "yanxitong_llm_tokens_total", "Total tokens consumed",
    ["model", "type"],  # type: prompt, completion
)
LLM_COST = Counter(
    "yanxitong_llm_cost_cents_total", "Total LLM cost in cents (USD)",
    ["model"],
)

# Knowledge store metrics
VECTOR_STORE_SIZE = Gauge("yanxitong_vector_store_docs", "Documents in vector store")
KG_ENTITY_COUNT = Gauge("yanxitong_kg_entity_count", "Entities in knowledge graph")
KG_RELATION_COUNT = Gauge("yanxitong_kg_relation_count", "Relations in knowledge graph")

# Cache metrics
CACHE_HITS = Counter("yanxitong_cache_hits_total", "Cache hits", ["cache_type"])
CACHE_MISSES = Counter("yanxitong_cache_misses_total", "Cache misses", ["cache_type"])

# Safety metrics
HALLUCINATION_FLAGS = Counter(
    "yanxitong_hallucination_flags_total", "Hallucination defense flags",
    ["layer", "risk_level"],
)
GUARD_BLOCKS = Counter(
    "yanxitong_guard_blocks_total", "Safety guard blocks",
    ["guard_type", "category"],
)

# Error metrics
ERROR_COUNT = Counter(
    "yanxitong_errors_total", "Error count",
    ["error_type", "component"],
)


@contextmanager
def track_request(endpoint: str, method: str = "POST"):
    """Context manager to track a request lifecycle."""
    start = time.perf_counter()
    ACTIVE_REQUESTS.inc()
    status = "200"
    try:
        yield
    except Exception:
        status = "500"
        raise
    finally:
        ACTIVE_REQUESTS.dec()
        duration = time.perf_counter() - start
        REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)
        REQUEST_COUNT.labels(endpoint=endpoint, method=method, status=status).inc()


def track_agent_call(agent_name: str, status: str = "success", duration_s: float = 0):
    AGENT_CALL_COUNT.labels(agent_name=agent_name, status=status).inc()
    AGENT_LATENCY.labels(agent_name=agent_name).observe(duration_s)


def track_llm_call(model: str, role: str, prompt_tokens: int = 0, completion_tokens: int = 0):
    LLM_CALL_COUNT.labels(model=model, role=role).inc()
    if prompt_tokens:
        LLM_TOKEN_COUNT.labels(model=model, type="prompt").inc(prompt_tokens)
    if completion_tokens:
        LLM_TOKEN_COUNT.labels(model=model, type="completion").inc(completion_tokens)


def track_cache(hit: bool, cache_type: str = "llm"):
    if hit:
        CACHE_HITS.labels(cache_type=cache_type).inc()
    else:
        CACHE_MISSES.labels(cache_type=cache_type).inc()


def track_hallucination_flag(layer: str, risk_level: str):
    HALLUCINATION_FLAGS.labels(layer=layer, risk_level=risk_level).inc()


def track_guard_block(guard_type: str, category: str):
    GUARD_BLOCKS.labels(guard_type=guard_type, category=category).inc()


def track_error(error_type: str, component: str):
    ERROR_COUNT.labels(error_type=error_type, component=component).inc()


def get_metrics_response() -> Response:
    """Generate Prometheus metrics endpoint response."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
