"""Unit tests for deterministic academic-review checks (no LLM involved)."""

from src.agents.academic_reviewer import checks


DRAFT = """# 3.2 实验结果与讨论

本实验通过差热分析法测定 Pb-Sn 二元体系的冷却曲线，在不同 Sn 质量分数下获得了 7 组步冷曲线。

依据文献 Jones, 2023，该区间的相界线斜率与本实验测定值吻合良好[7][8]。

需要指出的是，本实验所用样品纯度为 99.9%，可能会引入一点点误差[12]。

## 参考文献

[1] Edge et al. From Local to Global. 2024.
"""


class TestSplitParagraphs:
    def test_sections_and_indexes(self):
        paragraphs = checks.split_paragraphs(DRAFT)
        assert len(paragraphs) == 3
        assert paragraphs[0]["section"] == "3.2 实验结果与讨论"
        assert paragraphs[0]["para"] == 1
        assert paragraphs[2]["para"] == 3

    def test_reference_section_excluded(self):
        paragraphs = checks.split_paragraphs(DRAFT)
        assert all("Edge et al" not in p["text"] for p in paragraphs)


class TestLocateQuote:
    def test_locate_ok(self):
        loc = checks.locate_quote(DRAFT, "可能会引入一点点误差")
        assert loc is not None
        assert loc["section"] == "3.2 实验结果与讨论"
        assert loc["para"] == 3

    def test_locate_whitespace_tolerant(self):
        loc = checks.locate_quote(DRAFT, "在不同 Sn 质量分数下\n获得了")
        assert loc is not None
        assert loc["para"] == 1

    def test_locate_missing(self):
        assert checks.locate_quote(DRAFT, "这句话不在草稿中") is None


class TestCitationIssues:
    def test_adjacent_markers_fixable(self):
        issues = checks.detect_citation_issues(DRAFT, papers_count=8)
        merged = [i for i in issues if "未合并" in i["description"]]
        assert len(merged) == 1
        assert merged[0]["fixable"] is True
        assert "[7,8]" in merged[0]["description"]
        assert merged[0]["located"] is True

    def test_out_of_range_marker(self):
        issues = checks.detect_citation_issues(DRAFT, papers_count=8)
        out_of_range = [i for i in issues if "超出" in i["description"]]
        assert len(out_of_range) == 1
        assert out_of_range[0]["severity"] == "medium"
        assert out_of_range[0]["fixable"] is False

    def test_no_papers_skips_range_check(self):
        issues = checks.detect_citation_issues(DRAFT, papers_count=0)
        assert all("超出" not in i["description"] for i in issues)


class TestNormalizeLlmIssues:
    def test_normalizes_and_locates(self):
        raw = [
            {
                "type": "学术表达",
                "severity": "中",
                "quote": "可能会引入一点点误差",
                "description": "口语化表达",
                "suggestion": "改为量化表述",
            },
            {"type": "引用格式", "severity": "low", "quote": "", "description": "", "suggestion": ""},
            "not-a-dict",
        ]
        issues = checks.normalize_llm_issues(raw, DRAFT)
        assert len(issues) == 1
        issue = issues[0]
        assert issue["type"] == checks.TYPE_EXPRESSION
        assert issue["severity"] == "medium"
        assert issue["located"] is True
        assert issue["para"] == 3

    def test_unlocatable_quote_kept_without_location(self):
        raw = [{"type": "逻辑", "severity": "high", "quote": "不存在的句子", "description": "x", "suggestion": "y"}]
        issues = checks.normalize_llm_issues(raw, DRAFT)
        assert issues[0]["located"] is False


class TestSimilarity:
    def test_identical_text_flagged(self):
        papers = [{"title": "Pb-Sn 相图研究", "abstract": "本实验通过差热分析法测定 Pb-Sn 二元体系的冷却曲线，获得了 7 组步冷曲线。"}]
        result = checks.similarity_self_check(DRAFT, papers)
        assert result["pct"] is not None
        assert result["pct"] > 10
        assert result["safe"] is False
        assert len(result["matches"]) >= 1

    def test_unrelated_draft_is_safe(self):
        papers = [{"title": "Deep Learning", "abstract": "We propose a transformer architecture for image recognition tasks."}]
        result = checks.similarity_self_check(DRAFT, papers)
        assert result["pct"] == 0.0
        assert result["safe"] is True
        assert result["matches"] == []

    def test_no_papers_returns_empty(self):
        result = checks.similarity_self_check(DRAFT, [])
        assert result["pct"] is None
        assert result["safe"] is None

    def test_similarity_issues_raise_severity(self):
        similarity = {
            "matches": [
                {"quote": "a" * 40, "ratio": 80.0, "title": "t", "located": True},
                {"quote": "b" * 40, "ratio": 45.0, "title": "t", "located": True},
            ]
        }
        issues = checks.similarity_issues(similarity)
        assert [i["severity"] for i in issues] == ["high", "medium"]
        assert all(i["type"] == checks.TYPE_PLAGIARISM for i in issues)


class TestMergeAndStats:
    def test_dedupe_and_status(self):
        deterministic = [
            {
                "type": checks.TYPE_CITATION,
                "severity": "low",
                "quote": "[7][8]",
                "description": "相邻引用 [7][8] 未合并",
                "suggestion": "合并",
                "fixable": True,
                "located": True,
                "source": "rule",
            }
        ]
        reviewer = [
            {
                "type": checks.TYPE_CITATION,
                "severity": "high",
                "quote": "[7][8]",
                "description": "相邻引用 [7][8] 未合并",
                "suggestion": "合并",
                "fixable": False,
                "located": True,
                "source": "reviewer",
            },
            {
                "type": checks.TYPE_AI,
                "severity": "high",
                "quote": "可能会引入一点点误差",
                "description": "AI 生成内容未标注",
                "suggestion": "补充标注",
                "fixable": False,
                "located": True,
                "source": "reviewer",
            },
        ]
        merged = checks.merge_issues(deterministic, reviewer, DRAFT)
        assert len(merged) == 2
        by_type = {i["type"]: i for i in merged}
        # 规则项去重后保留 fixable 状态
        assert by_type[checks.TYPE_CITATION]["status"] == checks.STATUS_FIXABLE
        assert by_type[checks.TYPE_AI]["status"] == checks.STATUS_OPEN
        assert merged[0]["severity"] == "high"
        assert all(i["id"].startswith("i") for i in merged)

    def test_build_stats(self):
        issues = [
            {"type": checks.TYPE_CITATION, "severity": "low", "fixable": True},
            {"type": checks.TYPE_CITATION, "severity": "medium", "fixable": False},
            {"type": checks.TYPE_EXPRESSION, "severity": "low", "fixable": False},
            {"type": checks.TYPE_PLAGIARISM, "severity": "high", "fixable": False},
        ]
        stats = checks.build_stats(issues, {"pct": 8.2, "safe": True, "threshold": 15.0})
        assert stats["citation_format"] == 2
        assert stats["citation_fixable"] == 1
        assert stats["citation_manual"] == 1
        assert stats["polish"] == 1
        assert stats["misconduct"] == 1
        assert stats["similarity_pct"] == 8.2
        assert stats["similarity_safe"] is True
