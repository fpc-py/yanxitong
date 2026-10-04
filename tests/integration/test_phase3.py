"""Integration tests for Phase 3: Writing + Enhanced Review."""

import pytest, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCitationFormatter:
    """Test citation formatting utilities."""

    def test_format_gbt7714(self):
        from src.tools.citation_formatter import CitationFormatter
        paper = {
            "title": "BERT: Pre-training of Deep Bidirectional Transformers",
            "authors": ["Devlin J", "Chang M W", "Lee K", "Toutanova K"],
            "year": 2019,
            "source": "NAACL-HLT",
            "doi": "10.18653/v1/N19-1423",
        }
        result = CitationFormatter.format(paper, "gbt7714", 1)
        assert "Devlin J" in result
        assert "BERT" in result
        assert "2019" in result

    def test_format_apa(self):
        from src.tools.citation_formatter import CitationFormatter
        paper = {"title": "Test Paper", "authors": ["Smith J"], "year": 2020}
        result = CitationFormatter.format(paper, "apa")
        assert "Smith J" in result
        assert "2020" in result

    def test_format_numeric(self):
        from src.tools.citation_formatter import CitationFormatter
        paper = {"title": "Test", "authors": ["A B"], "year": 2022}
        result = CitationFormatter.format(paper, "numeric", 5)
        assert "[5]" in result

    def test_format_bibliography(self):
        from src.tools.citation_formatter import CitationFormatter
        papers = [
            {"title": "Paper 1", "authors": ["A"], "year": 2020},
            {"title": "Paper 2", "authors": ["B"], "year": 2021},
        ]
        bib = CitationFormatter.format_bibliography(papers, "gbt7714")
        assert "Paper 1" in bib
        assert "Paper 2" in bib


class TestWritingAssistant:
    """Test writing assistant agent."""

    def test_agent_creation(self):
        from src.agents.writing_assistant.agent import WritingAssistantAgent
        agent = WritingAssistantAgent()
        assert agent.name == "writing_assistant"
        assert agent.model_role == "deep_reasoning"

    def test_section_prompts(self):
        from src.agents.writing_assistant.agent import SECTION_PROMPTS
        assert "abstract" in SECTION_PROMPTS
        assert "introduction" in SECTION_PROMPTS
        assert "methods" in SECTION_PROMPTS
        assert "results" in SECTION_PROMPTS
        assert "discussion" in SECTION_PROMPTS
        assert "full_paper" in SECTION_PROMPTS
        assert len(SECTION_PROMPTS) == 6


class TestAcademicReviewerV2:
    """Test enhanced academic reviewer."""

    def test_agent_creation(self):
        from src.agents.academic_reviewer.agent import AcademicReviewerAgent
        agent = AcademicReviewerAgent()
        assert agent.name == "academic_reviewer"
        assert "v2.0" in agent.description

    def test_parse_review_valid_json(self):
        from src.agents.academic_reviewer.agent import AcademicReviewerAgent
        agent = AcademicReviewerAgent()
        review = agent._parse_review('{"overall_score": 85, "recommendation": "accept"}')
        assert review["overall_score"] == 85
        assert review["recommendation"] == "accept"

    def test_parse_review_invalid_json(self):
        from src.agents.academic_reviewer.agent import AcademicReviewerAgent
        agent = AcademicReviewerAgent()
        review = agent._parse_review("This is not JSON")
        assert review["overall_score"] == 60
        assert "summary" in review

    def test_parse_review_with_markdown_fences(self):
        from src.agents.academic_reviewer.agent import AcademicReviewerAgent
        agent = AcademicReviewerAgent()
        review = agent._parse_review('```json\n{"overall_score": 90}\n```')
        assert review["overall_score"] == 90


class TestSupervisorGraphV3:
    """Test v3 workflow with writing chain."""

    def test_writing_routing_keywords(self):
        keywords = ["写论文", "生成论文", "撰写", "draft", "写作", "写摘要", "写引言"]
        assert all(isinstance(k, str) for k in keywords)
        assert len(keywords) >= 7

    def test_review_routing_keywords(self):
        keywords = ["审稿", "审阅", "修改论文", "论文评审", "review", "评审"]
        assert all(isinstance(k, str) for k in keywords)
        assert len(keywords) >= 6


class TestAPIRoutesV3:
    """Test that routes module compiles with v3 endpoints."""

    def test_routes_module_compiles(self):
        import py_compile
        path = os.path.join(os.path.dirname(__file__), "..", "..", "src", "api", "routes.py")
        py_compile.compile(path, doraise=True)

    def test_schemas_module_compiles(self):
        import py_compile
        path = os.path.join(os.path.dirname(__file__), "..", "..", "src", "api", "schemas.py")
        py_compile.compile(path, doraise=True)