"""Integration tests for Phase 5 — with dependency-aware skip guards."""

import pytest, sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import prometheus_client
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False

try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False


class TestMetrics:
    @pytest.mark.skipif(not HAS_PROMETHEUS, reason="prometheus_client not installed")
    def test_metrics_imports(self):
        from src.observability.metrics import REQUEST_COUNT, CACHE_HITS, track_request
        assert REQUEST_COUNT is not None

    @pytest.mark.skipif(not HAS_PROMETHEUS, reason="prometheus_client not installed")
    def test_metrics_counter_increment(self):
        from src.observability.metrics import track_request, track_agent_call, track_cache
        with track_request("test", "GET"):
            pass
        track_agent_call("test_agent", "success", 0.5)
        track_cache(True, "llm")

    @pytest.mark.skipif(not HAS_PROMETHEUS, reason="prometheus_client not installed")
    def test_metrics_response(self):
        from src.observability.metrics import get_metrics_response
        resp = get_metrics_response()
        assert resp.status_code == 200


class TestTracing:
    @pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed")
    def test_tracing_imports(self):
        from src.observability.tracing import setup_tracing, trace_agent_call, trace_llm_call
        assert callable(setup_tracing)

    @pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed")
    def test_tracer_returns_none_when_not_setup(self):
        from src.observability.tracing import get_tracer
        tracer = get_tracer()
        assert tracer is None or hasattr(tracer, 'start_as_current_span')


class TestRAGASEvaluator:
    def test_evaluator_imports(self):
        from tests.evaluation.ragas_eval import RAGASEvaluator, GOLDEN_SET
        assert len(GOLDEN_SET) >= 10  # goldset.json 金标集（A4 扩展到 30 条）

    def test_faithfulness_perfect(self):
        from tests.evaluation.ragas_eval import RAGASEvaluator
        e = RAGASEvaluator()
        score = e.evaluate_faithfulness("BERT is a model.", ["BERT is a model for NLP."])
        assert score > 0.5

    def test_answer_relevancy(self):
        from tests.evaluation.ragas_eval import RAGASEvaluator
        e = RAGASEvaluator()
        score = e.evaluate_answer_relevancy("What is BERT model?", "The BERT model is a bidirectional transformer for NLP.")
        assert score >= 0.0

    def test_context_recall(self):
        from tests.evaluation.ragas_eval import RAGASEvaluator
        e = RAGASEvaluator()
        score = e.evaluate_context_recall("BERT uses bidirectional transformers.", ["BERT bidirectional transformers language model."])
        assert score > 0.0

    def test_context_precision(self):
        from tests.evaluation.ragas_eval import RAGASEvaluator
        e = RAGASEvaluator()
        score = e.evaluate_context_precision(["BERT is a transformer model for NLP tasks."], "BERT transformer model NLP")
        assert score > 0.0

    def test_evaluate_sample(self):
        from tests.evaluation.ragas_eval import RAGASEvaluator, EvalSample
        e = RAGASEvaluator()
        sample = EvalSample(question="What is BERT?", reference_answer="BERT is a bidirectional transformer model.", generated_answer="BERT is a bidirectional transformer.", context=["BERT is a bidirectional transformer model for NLP."])
        result = e.evaluate_sample(sample)
        assert 0.0 <= result.overall <= 1.0

    def test_full_evaluation(self):
        from tests.evaluation.ragas_eval import RAGASEvaluator
        e = RAGASEvaluator()
        report = e.run_evaluation()
        assert report["total_samples"] >= 10  # goldset.json 扩展到 30 条
        assert "ci_pass" in report


class TestMainMetrics:
    def test_main_compiles(self):
        import py_compile
        py_compile.compile(os.path.join(os.path.dirname(__file__), "..", "..", "src", "main.py"), doraise=True)

    def test_metrics_module_file_exists(self):
        assert os.path.exists(os.path.join(os.path.dirname(__file__), "..", "..", "src", "observability", "metrics.py"))

    def test_tracing_module_file_exists(self):
        assert os.path.exists(os.path.join(os.path.dirname(__file__), "..", "..", "src", "observability", "tracing.py"))

    def test_grafana_dashboard_exists(self):
        assert os.path.exists(os.path.join(os.path.dirname(__file__), "..", "..", "docker", "grafana", "dashboard.json"))