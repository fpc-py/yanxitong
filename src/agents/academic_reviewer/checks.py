"""Deterministic review checks for the Academic Reviewer.

Everything here is rule-based (no LLM): quote location mapping, citation-marker
hygiene, and a local n-gram similarity self-check against the session's paper
abstracts. The LLM only supplies raw paragraph findings; this module verifies
they really exist in the draft and enriches them with locations/stats.
"""

from __future__ import annotations

import re

# Canonical issue types (frontend maps these to Chinese labels).
TYPE_CITATION = "citation_format"
TYPE_EXPRESSION = "expression"
TYPE_DATA = "data_integrity"
TYPE_LOGIC = "logic"
TYPE_AI = "ai_disclosure"
TYPE_PLAGIARISM = "similarity"
TYPE_OTHER = "other"

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# Status: fixable(可自动修复) / open(待修) / suggest(建议)
STATUS_FIXABLE = "fixable"
STATUS_OPEN = "open"
STATUS_SUGGEST = "suggest"

SIMILARITY_THRESHOLD = 15.0  # 安全阈值（%），与原型一致
SIMILARITY_MATCH_MIN = 35.0  # 单句重合率超过该值才单列问题
SHINGLE = 8  # 字符级 n-gram 窗口

_TYPE_ALIASES = {
    "引用格式": TYPE_CITATION,
    "citation": TYPE_CITATION,
    "citation_format": TYPE_CITATION,
    "references": TYPE_CITATION,
    "学术表达": TYPE_EXPRESSION,
    "表达": TYPE_EXPRESSION,
    "润色": TYPE_EXPRESSION,
    "语言": TYPE_EXPRESSION,
    "expression": TYPE_EXPRESSION,
    "language": TYPE_EXPRESSION,
    "数据完整性": TYPE_DATA,
    "数据": TYPE_DATA,
    "data_integrity": TYPE_DATA,
    "data": TYPE_DATA,
    "逻辑一致性": TYPE_LOGIC,
    "逻辑": TYPE_LOGIC,
    "logic": TYPE_LOGIC,
    "ai标注": TYPE_AI,
    "ai标注缺失": TYPE_AI,
    "学术不端": TYPE_AI,
    "ai_disclosure": TYPE_AI,
    "ai": TYPE_AI,
    "similarity": TYPE_PLAGIARISM,
    "查重": TYPE_PLAGIARISM,
}

_HEADING_HASH = re.compile(r"^#{1,6}\s+(.*)$")
_HEADING_NUMBERED = re.compile(r"^\d+(?:\.\d+){1,4}[\.、．\s]|^\d+[\.、．]\s*\S")
_HEADING_CN = re.compile(r"^[一二三四五六七八九十]+[、.．]\s*\S")
_HEADING_WORDS = re.compile(
    r"^(摘要|关键词|引言|绪论|方法|材料与方法|结果|结果与讨论|讨论|结论|参考文献|致谢|"
    r"Abstract|Introduction|Methods|Results|Discussion|Conclusion|References).*$"
)

_REF_LINE = re.compile(r"^\s*\[\d+\]")
_SENT_SPLIT = re.compile(r"[。！？!?；;\n]+")
_CJK_KEEP = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")

DETERMINISTIC_SOURCE = "rule"
REVIEWER_SOURCE = "reviewer"


def _norm_ws(text: str) -> str:
    """Collapse all whitespace so quotes survive line-wrap differences."""
    return re.sub(r"\s+", "", text or "")


def _norm_type(value: object) -> str:
    key = str(value or "").strip().lower().replace(" ", "")
    return _TYPE_ALIASES.get(key, _TYPE_ALIASES.get(str(value or "").strip(), TYPE_OTHER))


def _norm_severity(value: object) -> str:
    key = str(value or "").strip().lower()
    if key in ("high", "高", "严重"):
        return "high"
    if key in ("medium", "中", "中等", "middle"):
        return "medium"
    return "low"


def _is_heading(line: str) -> tuple[bool, str]:
    line = line.strip()
    if not line:
        return False, ""
    m = _HEADING_HASH.match(line)
    if m:
        return True, m.group(1).strip()
    if len(line) <= 40 and _HEADING_NUMBERED.match(line):
        return True, line
    if len(line) <= 40 and _HEADING_CN.match(line):
        return True, line
    if len(line) <= 20 and _HEADING_WORDS.match(line) and not re.search(r"[。！？!?]", line):
        return True, line
    return False, ""


def split_paragraphs(draft: str) -> list[dict]:
    """Split the draft into paragraphs carrying section + in-section index."""
    paragraphs: list[dict] = []
    section = ""
    para_in_section = 0
    in_references = False

    for raw_block in re.split(r"\n\s*\n", draft or ""):
        block = raw_block.strip()
        if not block:
            continue
        heading, title = _is_heading(block.splitlines()[0])
        if heading and len(block.splitlines()) == 1:
            section = title
            para_in_section = 0
            in_references = bool(re.search(r"参考文献|references", title, re.IGNORECASE))
            continue
        if in_references:
            continue
        para_in_section += 1
        paragraphs.append(
            {
                "index": len(paragraphs) + 1,  # 1-based global
                "section": section,
                "para": para_in_section,
                "text": block,
            }
        )
    return paragraphs


def locate_quote(draft: str, quote: str) -> dict | None:
    """Map a verbatim quote back to its paragraph (whitespace-tolerant)."""
    q = (quote or "").strip()
    if not q or not draft:
        return None
    nq = _norm_ws(q)
    for para in split_paragraphs(draft):
        if q in para["text"] or (nq and nq in _norm_ws(para["text"])):
            return {"section": para["section"], "para": para["para"], "global_para": para["index"]}
    return None


def normalize_llm_issues(raw_issues: object, draft: str, limit: int = 30) -> list[dict]:
    """Turn reviewer-supplied findings into verified issues with locations."""
    if not isinstance(raw_issues, list):
        return []
    out: list[dict] = []
    for raw in raw_issues[:limit]:
        if not isinstance(raw, dict):
            continue
        quote = str(raw.get("quote") or "").strip()
        description = str(raw.get("description") or raw.get("issue") or "").strip()
        suggestion = str(raw.get("suggestion") or "").strip()
        if not (description or suggestion):
            continue
        located = locate_quote(draft, quote) if quote else None
        out.append(
            {
                "type": _norm_type(raw.get("type")),
                "severity": _norm_severity(raw.get("severity")),
                "quote": quote[:80],
                "description": description[:500],
                "suggestion": suggestion[:500],
                "fixable": False,
                "located": bool(located),
                "section": (located or {}).get("section", ""),
                "para": (located or {}).get("para"),
                "global_para": (located or {}).get("global_para"),
                "source": REVIEWER_SOURCE,
            }
        )
    return out


def detect_citation_issues(draft: str, papers_count: int) -> list[dict]:
    """Rule-based citation hygiene: unmerged neighbours + out-of-range markers."""
    issues: list[dict] = []
    if not draft:
        return issues

    seen_adjacent: set[tuple[int, int]] = set()
    for m in re.finditer(r"\[(\d+)\]\s*\[(\d+)\]", draft):
        a, b = int(m.group(1)), int(m.group(2))
        if (a, b) in seen_adjacent:
            continue
        seen_adjacent.add((a, b))
        merged = f"[{a}]" if a == b else f"[{a},{b}]"
        issues.append(
            {
                "type": TYPE_CITATION,
                "severity": "low",
                "quote": m.group(0),
                "description": f"相邻引用 {m.group(0)} 未合并为 {merged}",
                "suggestion": f"按引用规范合并为 {merged}。",
                "fixable": True,
                "located": True,
                "source": DETERMINISTIC_SOURCE,
            }
        )

    if papers_count > 0:
        seen_marker: set[int] = set()
        for m in re.finditer(r"\[(\d+)\]", draft):
            n = int(m.group(1))
            if n <= papers_count or n in seen_marker:
                continue
            seen_marker.add(n)
            issues.append(
                {
                    "type": TYPE_CITATION,
                    "severity": "medium",
                    "quote": m.group(0),
                    "description": f"引用 [{n}] 超出当前会话文献库范围（共 {papers_count} 篇），无法核验",
                    "suggestion": "核对编号，或补充对应文献后再引用。",
                    "fixable": False,
                    "located": True,
                    "source": DETERMINISTIC_SOURCE,
                }
            )

    for issue in issues:
        issue.update(locate_quote(draft, issue["quote"]) or {"section": "", "para": None, "global_para": None})
    return issues


def _shingles(text: str, k: int = SHINGLE) -> set[str]:
    chars = _CJK_KEEP.sub("", text or "")
    if len(chars) < k:
        return set()
    return {chars[i : i + k] for i in range(len(chars) - k + 1)}


def _strip_reference_lines(draft: str) -> str:
    kept: list[str] = []
    in_references = False
    for line in (draft or "").splitlines():
        heading, title = _is_heading(line)
        if heading:
            in_references = bool(re.search(r"参考文献|references", title, re.IGNORECASE))
            continue
        if in_references or _REF_LINE.match(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def similarity_self_check(draft: str, papers: list[dict], threshold: float = SIMILARITY_THRESHOLD) -> dict:
    """Local n-gram overlap vs session paper abstracts (非正式查重，仅作自检)。"""
    abstracts: list[tuple[str, set[str]]] = []
    for p in (papers or [])[:15]:
        if not isinstance(p, dict):
            continue
        corpus = f"{p.get('title', '')}。{p.get('abstract', '')}"
        shingles = _shingles(corpus)
        if shingles:
            abstracts.append((str(p.get("title", ""))[:120], shingles))

    result = {
        "pct": None,
        "safe": None,
        "threshold": threshold,
        "matches": [],
        "papers_compared": len(abstracts),
        "method": "char-ngram",
    }
    if not (draft or "").strip() or not abstracts:
        return result

    body = _strip_reference_lines(draft)
    draft_shingles = _shingles(body)
    if not draft_shingles:
        return result

    union: set[str] = set()
    for _, sh in abstracts:
        union |= sh
    overlap = draft_shingles & union
    pct = round(100.0 * len(overlap) / len(draft_shingles), 1)

    matches: list[dict] = []
    for sentence in _SENT_SPLIT.split(body):
        s = sentence.strip()
        if len(s) < max(4 * SHINGLE, 24):
            continue
        ss = _shingles(s)
        if not ss:
            continue
        best_ratio, best_title = 0.0, ""
        for title, paper_sh in abstracts:
            ratio = len(ss & paper_sh) / len(ss)
            if ratio > best_ratio:
                best_ratio, best_title = ratio, title
        if best_ratio * 100 >= SIMILARITY_MATCH_MIN:
            matches.append(
                {
                    "quote": s[:60],
                    "ratio": round(best_ratio * 100, 1),
                    "title": best_title,
                    "located": bool(locate_quote(draft, s[:60])),
                }
            )

    matches.sort(key=lambda m: -m["ratio"])
    result["pct"] = pct
    result["matches"] = matches[:10]
    result["safe"] = pct < threshold and all(m["ratio"] < 50.0 for m in matches)
    return result


def similarity_issues(similarity: dict) -> list[dict]:
    """High-overlap sentences become academic-integrity findings."""
    issues: list[dict] = []
    for m in similarity.get("matches") or []:
        severity = "high" if m.get("ratio", 0) >= 60 else "medium"
        issues.append(
            {
                "type": TYPE_PLAGIARISM,
                "severity": severity,
                "quote": m.get("quote", ""),
                "description": f"与文献《{m.get('title', '')[:60]}》摘要重合度 {m.get('ratio', 0)}%，注意改写与规范引用",
                "suggestion": "确认引用标注完整；如为改写不全，建议重述该句表述。",
                "fixable": False,
                "located": bool(m.get("located")),
                "source": DETERMINISTIC_SOURCE,
            }
        )
    return issues


def merge_issues(deterministic: list[dict], reviewer: list[dict], draft: str, limit: int = 40) -> list[dict]:
    """Rule findings win over LLM duplicates; sort by severity, then attach ids."""
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _key(issue: dict) -> tuple[str, str]:
        quote = _norm_ws(issue.get("quote", ""))[:30]
        if quote:
            return issue["type"], quote
        return issue["type"], _norm_ws(issue.get("description", ""))[:40]

    for issue in [*deterministic, *reviewer]:
        k = _key(issue)
        if k in seen:
            continue
        seen.add(k)
        merged.append(issue)

    merged.sort(key=lambda i: (SEVERITY_ORDER.get(i["severity"], 3), i["type"]))

    for idx, issue in enumerate(merged, start=1):
        if not issue.get("located") and issue.get("quote"):
            located = locate_quote(draft, issue["quote"])
            if located:
                issue.update(located)
                issue["located"] = True
        issue["id"] = f"i{idx}"
        issue["status"] = (
            STATUS_FIXABLE
            if issue.get("fixable")
            else (STATUS_SUGGEST if issue["severity"] == "low" else STATUS_OPEN)
        )
    return merged[:limit]


def build_stats(issues: list[dict], similarity: dict) -> dict:
    citation = [i for i in issues if i["type"] == TYPE_CITATION]
    polish = [i for i in issues if i["type"] == TYPE_EXPRESSION]
    misconduct = [i for i in issues if i["type"] in (TYPE_AI, TYPE_PLAGIARISM)]
    return {
        "total": len(issues),
        "citation_format": len(citation),
        "citation_fixable": len([i for i in citation if i.get("fixable")]),
        "citation_manual": len([i for i in citation if not i.get("fixable")]),
        "polish": len(polish),
        "misconduct": len(misconduct),
        "high": len([i for i in issues if i["severity"] == "high"]),
        "similarity_pct": similarity.get("pct"),
        "similarity_safe": similarity.get("safe"),
        "similarity_threshold": similarity.get("threshold", SIMILARITY_THRESHOLD),
    }
