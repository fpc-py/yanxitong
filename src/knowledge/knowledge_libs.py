"""内置分析知识库的种子装载与召回（绘图模板/统计方法/期刊规范/学科教材/实验设计）。

5 个库以 markdown 种子文件（``data/knowledge_libs/*.md``）维护，每个 ``## ``
小节切为一块，写入现有 KB 双区基建的独立命名空间 ``kb:lib:{name}``（见
``kb.KB_LIBRARIES``）：与用户 KB（team/personal）互不可见，不污染 ``list_files``
与 GraphRAG 的文献问答合并检索。召回集由调用方指定：``DEFAULT_LIBRARIES``
（数据分析 4 库）或 ``DESIGN_LIBRARIES``（实验设计 3 库）。

- ``ensure_seeded`` 幂等：``add_chunks`` 内置块级 sha256 去重，重复调用零新增；
- ``recall`` 向量检索失败（BGE 未就绪/索引为空）时自动降级为关键词扫描种子
  文件，返回 ``degraded=True``，数据管道不中断。
"""

import hashlib
import logging
import os
import re
from pathlib import Path

from src.core.config import get_settings
from src.knowledge import kb

logger = logging.getLogger(__name__)

SEED_DIR = os.path.join("data", "knowledge_libs")

#: 库名 → 展示名（顺序即前端与 prompt 中知识引用的分组顺序）
LIB_LABELS: dict[str, str] = {
    "plotting": "绘图模板库",
    "methods": "统计方法库",
    "journal": "期刊规范库",
    "textbook": "学科教材库",
    "design": "实验设计库",
}

LIB_SCOPES: list[str] = [f"kb:lib:{lib}" for lib in LIB_LABELS]

#: 库名 → 种子文件名
LIB_FILES: dict[str, str] = {
    "plotting": "plotting_templates.md",
    "methods": "statistical_methods.md",
    "journal": "journal_specs.md",
    "textbook": "textbook_notes.md",
    "design": "design_priors.md",
}

#: 数据分析（默认召回集）：4 个面向数据分析的库
DEFAULT_LIBRARIES: list[str] = ["plotting", "methods", "journal", "textbook"]

#: 实验设计（设计器召回集）：先验区间 + 统计功效 + 方法论教材
DESIGN_LIBRARIES: list[str] = ["design", "methods", "textbook"]

_MAX_CHUNK_CHARS = 900
_HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_\-]{1,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_seeded = False
_seed_failed = False


def lib_scope(library: str) -> str:
    return f"kb:lib:{library}"


def seed_path(library: str) -> str:
    return os.path.join(SEED_DIR, LIB_FILES.get(library, f"{library}.md"))


def _split_sections(text: str) -> list[dict]:
    """把种子 markdown 按 ``## `` 标题切块；``` 代码栅栏内的伪标题不会误切。"""
    matches = [m for m in _HEADING_RE.finditer(text) if _outside_code_fence(text, m.start())]
    spans: list[tuple[str, int, int]] = []
    if matches and matches[0].start() > 0:
        spans.append(("总览", 0, matches[0].start()))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        spans.append((m.group(1).strip(), m.start(), end))
    sections = []
    for title, start, end in spans:
        body = text[start:end].strip()
        if not body:
            continue
        sections.append({
            "text": body,
            "section_title": title,
            "chunk_index": len(sections),
            "page": 0,
            "para": 0,
            "char_start": start,
            "char_end": end,
        })
    return sections


def _outside_code_fence(text: str, pos: int) -> bool:
    return text[:pos].count("```") % 2 == 0


def ensure_seeded() -> int:
    """把 4 个种子文件写入 kb:lib:* 命名空间，返回本次新增块数（幂等）。"""
    global _seeded, _seed_failed
    if _seeded or _seed_failed:
        return 0
    added = 0
    try:
        for library in LIB_LABELS:
            path = Path(seed_path(library))
            if not path.is_file():
                logger.warning("知识库种子缺失: %s", path)
                continue
            raw = path.read_text(encoding="utf-8")
            chunks = _split_sections(raw)
            if not chunks:
                continue
            content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            added += kb.add_chunks(
                f"{library}.md", chunks, library=library, owner="system", content_hash=content_hash
            )
        _seeded = True
    except Exception as exc:  # 编码器不可用等：标记失败，后续走关键词回退
        _seed_failed = True
        logger.warning("知识库种子装载失败，召回将退化为关键词模式: %s", exc)
    return added


def _terms(text: str) -> set[str]:
    text = (text or "").lower()
    words = set(_WORD_RE.findall(text))
    cjk = _CJK_RE.findall(text)
    words.update(a + b for a, b in zip(cjk, cjk[1:]))
    return words


def _augment_query(intent: str, profile: dict | None) -> str:
    """意图 + 画像（格式/列名/类型）拼接成召回查询，提升方法库命中率。"""
    parts = [intent or ""]
    if isinstance(profile, dict):
        if profile.get("format"):
            parts.append(str(profile["format"]))
        cols = [c for c in (profile.get("columns") or []) if isinstance(c, dict)]
        parts.append(" ".join(str(c.get("name", "")) for c in cols[:20]))
        parts.append(" ".join(sorted({str(c.get("dtype", "")) for c in cols if c.get("dtype")})))
    return " ".join(p for p in parts if p).strip() or "数据分析"


def _format_chunk(library: str, hit: dict, similarity: float) -> dict:
    return {
        "section": hit.get("section_title") or "",
        "text": (hit.get("text") or "")[:_MAX_CHUNK_CHARS],
        "source": f"{LIB_LABELS[library]}/{hit.get('filename') or library + '.md'}",
        "similarity": round(float(similarity), 4),
    }


def _empty_libraries(libs: list[str]) -> dict[str, dict]:
    return {lib: {"label": LIB_LABELS[lib], "chunks": []} for lib in libs}


def _keyword_libraries(intent: str, top_k: int, libs: list[str]) -> dict[str, dict]:
    """关键词回退：按字符二元组/词重叠给种子小节打分，零命中取前 2 节。"""
    query_terms = _terms(intent)
    libraries = _empty_libraries(libs)
    for library in libs:
        path = Path(seed_path(library))
        if not path.is_file():
            continue
        sections = _split_sections(path.read_text(encoding="utf-8"))
        scored = []
        for sec in sections:
            overlap = len(query_terms & _terms(sec["text"]))
            scored.append((overlap, sec))
        scored.sort(key=lambda item: -item[0])
        picked = [item for item in scored if item[0] > 0][:top_k]
        if not picked:
            picked = scored[:2]
        for score, sec in picked:
            libraries[library]["chunks"].append(
                _format_chunk(library, sec, min(1.0, score / 8.0))
            )
    return libraries


def recall(
    intent: str,
    profile: dict | None = None,
    top_k: int | None = None,
    libraries: list[str] | None = None,
) -> dict:
    """召回内置知识库的相关块（``libraries=None`` 时用数据分析默认 4 库）。

    Returns:
        ``{query, libraries: {lib: {label, chunks: [{section, text, source,
        similarity}]}}, degraded, sources}``；向量通道不可用时 degraded=True
        且 chunks 来自种子文件关键词扫描。
    """
    libs = list(libraries) if libraries else list(DEFAULT_LIBRARIES)
    k = top_k or get_settings().analysis.recall_top_k
    query = _augment_query(intent, profile)
    degraded = False
    out = _empty_libraries(libs)
    try:
        ensure_seeded()
        hits = 0
        for library in libs:
            for hit in kb.query_chunks(query, scopes=[lib_scope(library)], top_k=k):
                out[library]["chunks"].append(
                    _format_chunk(library, hit, hit.get("similarity", 0.0))
                )
                hits += 1
        if hits == 0:
            raise RuntimeError("知识库向量召回为空（索引未就绪或编码器不可用）")
    except Exception as exc:
        logger.warning("知识库向量召回失败，退化为关键词扫描: %s", exc)
        degraded = True
        out = _keyword_libraries(query if query else intent, k, libs)
    return {
        "query": intent,
        "libraries": out,
        "degraded": degraded,
        "sources": sum(len(v["chunks"]) for v in out.values()),
    }
