"""Integration tests for Phase 6: Polish & Defense Preparation."""

import pytest, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAblation:
    """Test ablation experiment runner."""

    def test_ablation_imports(self):
        from tests.evaluation.ablation import AblationRunner, AblationResult
        assert AblationRunner is not None

    def test_all_experiments(self):
        from tests.evaluation.ablation import AblationRunner
        results = AblationRunner.run_all()
        assert len(results) == 5
        for r in results:
            assert r.experiment in ("exp1", "exp2", "exp3", "exp4", "exp5")
            assert 0.0 <= r.faithfulness <= 1.0
            assert r.cost_estimate > 0

    def test_report_generation(self):
        from tests.evaluation.ablation import AblationRunner
        report = AblationRunner.generate_report()
        assert "消融实验报告" in report
        assert "exp1" in report
        assert "exp5" in report
        assert "结论" in report

    def test_baseline_values(self):
        from tests.evaluation.ablation import AblationRunner
        base = AblationRunner.BASELINE_RESULTS
        assert base["faithfulness"] > 0.7
        assert base["citation_accuracy"] > 0.7
        assert base["cost_estimate"] < 1.0


class TestDemoScenarios:
    """Test demo scenario definitions."""

    def test_demo_imports(self):
        from tests.evaluation.demo_scenarios import DemoScenarios
        assert DemoScenarios is not None

    def test_three_scenarios(self):
        from tests.evaluation.demo_scenarios import DemoScenarios
        s1 = DemoScenarios.SCENARIO_1
        s2 = DemoScenarios.SCENARIO_2
        s3 = DemoScenarios.SCENARIO_3
        assert s1["name"] == "文献调研全链路"
        assert s2["name"] == "数据分析闭环"
        assert s3["name"] == "论文写作与审稿"
        assert len(s1["steps"]) == 4
        assert len(s2["steps"]) == 4
        assert len(s3["steps"]) == 4

    def test_demo_script_generation(self):
        from tests.evaluation.demo_scenarios import DemoScenarios
        script = DemoScenarios.generate_demo_script()
        assert "演示场景 1" in script
        assert "演示场景 2" in script
        assert "演示场景 3" in script
        assert "演示检查清单" in script


class TestReadme:
    """Test README exists and has key sections."""

    def test_readme_exists(self):
        readme_path = os.path.join(os.path.dirname(__file__), "..", "..", "README.md")
        assert os.path.exists(readme_path), "README.md not found"

    def test_readme_has_sections(self):
        readme_path = os.path.join(os.path.dirname(__file__), "..", "..", "README.md")
        if os.path.exists(readme_path):
            content = open(readme_path, encoding="utf-8").read()
            # Should at minimum have these sections
            assert len(content) > 500, "README seems too short"


class TestPhase6Integration:
    """Integration checks for Phase 6 completeness."""

    def test_all_eval_files_exist(self):
        eval_dir = os.path.join(os.path.dirname(__file__), "..", "evaluation")
        files = os.listdir(eval_dir)
        assert "ragas_eval.py" in files
        assert "ablation.py" in files
        assert "demo_scenarios.py" in files

    def test_project_completeness(self):
        """Verify all major components are in place."""
        base = os.path.join(os.path.dirname(__file__), "..", "..")
        checks = [
            ("src/main.py", "Application entry point"),
            ("src/agents/supervisor/agent.py", "Supervisor agent"),
            ("src/agents/retriever/agent.py", "Retriever agent"),
            ("src/agents/kg_builder/agent.py", "KG Builder agent"),
            ("src/agents/data_analyst/agent.py", "Data Analyst agent"),
            ("src/agents/experiment_designer/agent.py", "Experiment Designer"),
            ("src/agents/writing_assistant/agent.py", "Writing Assistant"),
            ("src/agents/academic_reviewer/agent.py", "Academic Reviewer"),
            ("src/knowledge/vector_store.py", "Vector store"),
            ("src/knowledge/graph_store.py", "Graph store"),
            ("src/knowledge/graphrag.py", "GraphRAG"),
            ("src/safety/hallucination.py", "Hallucination defense"),
            ("src/safety/guard.py", "LLM Guard"),
            ("src/safety/citation.py", "Citation tracker"),
            ("src/workflows/supervisor_graph.py", "Supervisor graph"),
            ("src/api/routes.py", "API routes"),
            ("src/observability/tracing.py", "Tracing"),
            ("src/observability/metrics.py", "Metrics"),
            ("config.yaml", "Configuration"),
            ("docker-compose.yml", "Docker compose"),
            ("pyproject.toml", "Project config"),
            ("README.md", "Documentation"),
        ]
        missing = []
        for path, desc in checks:
            full = os.path.join(base, path)
            if not os.path.exists(full):
                missing.append(f"{desc} ({path})")
        if missing:
            pytest.fail(f"Missing files: {', '.join(missing)}")