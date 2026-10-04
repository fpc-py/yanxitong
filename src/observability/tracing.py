"""OpenTelemetry tracing v2.0 — full agent/LLM/tool call tracing with custom spans."""

import logging, time, functools
from contextlib import contextmanager
from typing import Optional, Callable
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger(__name__)

_tracer: Optional[trace.Tracer] = None


def setup_tracing(service_name: str = "yanxitong", otlp_endpoint: str = "http://localhost:4317"):
    global _tracer
    try:
        resource = Resource(attributes={SERVICE_NAME: service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)
        logger.info("OpenTelemetry tracing initialized, exporting to %s", otlp_endpoint)
    except Exception as e:
        logger.warning("OpenTelemetry setup failed (tracing disabled): %s", e)


def instrument_fastapi(app):
    try:
        FastAPIInstrumentor.instrument_app(app)
        logger.info("FastAPI instrumented with OpenTelemetry")
    except Exception as e:
        logger.warning("FastAPI instrumentation failed: %s", e)


def get_tracer() -> Optional[trace.Tracer]:
    global _tracer
    if _tracer is None:
        try:
            _tracer = trace.get_tracer("yanxitong")
        except Exception:
            return None
    return _tracer


@contextmanager
def trace_agent_call(agent_name: str, action: str, attributes: dict = None):
    """Context manager to trace an agent execution with custom attributes."""
    tracer = get_tracer()
    if tracer is None:
        yield
        return
    start = time.perf_counter()
    span_name = f"agent.{agent_name}.{action}"
    with tracer.start_as_current_span(span_name) as span:
        span.set_attribute("agent.name", agent_name)
        span.set_attribute("agent.action", action)
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(f"agent.{k}", str(v)[:256])
        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise
        finally:
            duration = (time.perf_counter() - start) * 1000
            span.set_attribute("duration_ms", duration)


@contextmanager
def trace_llm_call(model: str, prompt_length: int = 0):
    """Trace an LLM API call with model and token info."""
    tracer = get_tracer()
    if tracer is None:
        yield
        return
    start = time.perf_counter()
    with tracer.start_as_current_span(f"llm.{model}") as span:
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.prompt_length", prompt_length)
        result_attrs = {}
        try:
            yield result_attrs
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
        finally:
            duration = (time.perf_counter() - start) * 1000
            span.set_attribute("duration_ms", duration)
            for k, v in result_attrs.items():
                span.set_attribute(f"llm.{k}", str(v)[:256])


@contextmanager
def trace_tool_call(tool_name: str, tool_action: str):
    """Trace a tool call (API, sandbox, DB, etc)."""
    tracer = get_tracer()
    if tracer is None:
        yield
        return
    start = time.perf_counter()
    with tracer.start_as_current_span(f"tool.{tool_name}.{tool_action}") as span:
        span.set_attribute("tool.name", tool_name)
        span.set_attribute("tool.action", tool_action)
        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
        finally:
            span.set_attribute("duration_ms", (time.perf_counter() - start) * 1000)


def trace_async(func):
    """Decorator for async functions to auto-trace with function name."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        tracer = get_tracer()
        if tracer is None:
            return await func(*args, **kwargs)
        start = time.perf_counter()
        with tracer.start_as_current_span(func.__name__) as span:
            try:
                result = await func(*args, **kwargs)
                span.set_status(Status(StatusCode.OK))
                return result
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise
            finally:
                span.set_attribute("duration_ms", (time.perf_counter() - start) * 1000)
    return wrapper
