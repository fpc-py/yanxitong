"""知识库：上传文档 → 三级解析 → 分块嵌入 → 独立 FAISS 索引（data/knowledge_base）。

双区模型：
- team 库（scope="kb:team"）：课题组共享，登录用户可上传，全员可检索，仅上传者可删；
- personal 库（scope="kb:{owner}"）：仅本人可见、可删（匿名身份同样适用）。

与论文检索索引（data/faiss）分离；每个块带页码/段号/章节/字符区间元数据，
供 GraphRAG 合并检索（[KB:filename p{page}] 标记）与前端 span 溯源使用。
"""

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from src.knowledge.vector_store import VectorStore

logger = logging.getLogger(__name__)

KB_DIR = os.path.join("data", "knowledge_base")
KB_PREVIEW = 80
TEAM_SCOPE = "kb:team"

#: 内置分析知识库（绘图模板/统计方法/期刊规范/学科教材/实验设计）：全局共享，
#: 独立命名空间 kb:lib:{name}——不进入用户 KB 文件列表（list_files 只认
#: team/personal），也不参与 GraphRAG 的文献问答合并检索。
KB_LIBRARIES = ("plotting", "methods", "journal", "textbook", "design")

_kb_store: VectorStore | None = None


def team_scope() -> str:
    return TEAM_SCOPE


def personal_scope(owner: str) -> str:
    return f"kb:{owner}"


def scope_of(library: str, owner: str) -> str:
    if library == "team":
        return TEAM_SCOPE
    if library in KB_LIBRARIES:
        return f"kb:lib:{library}"
    return personal_scope(owner)


def get_kb_store() -> VectorStore:
    global _kb_store
    if _kb_store is None:
        Path(KB_DIR).mkdir(parents=True, exist_ok=True)
        if os.path.exists(os.path.join(KB_DIR, "index.faiss")):
            _kb_store = VectorStore(index_path=KB_DIR)
        else:
            _kb_store = VectorStore()  # 全新空索引，首次 save 时落盘
    return _kb_store


def add_chunks(filename: str, chunks: list[dict], library: str, owner: str, content_hash: str = "") -> int:
    """把解析出的块写入对应库并落盘，返回新增块数；块级 sha256 去重。

    每个 chunk 来自 ``pdf_ingest.chunk_parsed_doc``：含 text/page/para/
    section_title/char_start/char_end/chunk_index。
    """
    if not chunks:
        return 0
    store = get_kb_store()
    scope = scope_of(library, owner)
    now = datetime.now(timezone.utc).isoformat()
    seen = {d.get("chunk_hash") for d in store._documents.values() if d.get("scope") == scope}
    docs = []
    for ch in chunks:
        text = (ch.get("text") or "").strip()
        if not text:
            continue
        chunk_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if chunk_hash in seen:
            continue
        seen.add(chunk_hash)
        docs.append({
            "text": text,
            "kind": "knowledge",
            "library": library,
            "scope": scope,
            "owner": owner,
            "content_hash": content_hash,
            "filename": filename,
            "page": ch.get("page", 0),
            "para": ch.get("para", 0),
            "section_title": ch.get("section_title", ""),
            "chunk_index": ch.get("chunk_index", 0),
            "chunk_hash": chunk_hash,
            "char_start": ch.get("char_start", 0),
            "char_end": ch.get("char_end", 0),
            "uploaded_at": now,
        })
    if not docs:
        return 0
    store.add_documents(docs)
    store.save(KB_DIR)
    return len(docs)


def find_by_hash(library: str, owner: str, content_hash: str) -> dict | None:
    """同一库中是否已存在该内容哈希的文档（重复上传秒答 deduped）。"""
    if not content_hash:
        return None
    scope = scope_of(library, owner)
    for doc in get_kb_store()._documents.values():
        if doc.get("scope") == scope and doc.get("content_hash") == content_hash:
            return {
                "filename": doc.get("filename", ""),
                "owner": doc.get("owner", ""),
                "uploaded_at": doc.get("uploaded_at", ""),
            }
    return None


def query_chunks(query: str, scopes: list[str], top_k: int = 5) -> list[dict]:
    """按 scope 列表做向量检索（GraphRAG 合并 team + 个人双区）。"""
    if not scopes:
        return []
    return get_kb_store().search(query, top_k=top_k, scopes=scopes)


def list_files(owner: str) -> list[dict]:
    """列出「team 共享库 + 本人 personal 库」的文档，按 库+文件名+上传者 聚合。"""
    allowed = {TEAM_SCOPE, personal_scope(owner)}
    grouped: dict[tuple, dict] = {}
    for doc in get_kb_store()._documents.values():
        if doc.get("kind") != "knowledge" or doc.get("scope") not in allowed:
            continue
        fn = doc.get("filename", "")
        library = doc.get("library", "personal")
        key = (library, fn, doc.get("owner", ""))
        g = grouped.setdefault(key, {
            "filename": fn,
            "library": library,
            "uploader": doc.get("owner", ""),
            "content_hash": doc.get("content_hash", ""),
            "chunks": 0,
            "chars": 0,
            "updated_at": "",
            "preview": "",
        })
        g["chunks"] += 1
        g["chars"] += len(doc.get("text", "") or "")
        ts = doc.get("uploaded_at", "") or ""
        if ts and (not g["updated_at"] or ts > g["updated_at"]):
            g["updated_at"] = ts
        if not g["preview"]:
            g["preview"] = ((doc.get("text", "") or "")[:KB_PREVIEW]).replace("\n", " ")
    return list(grouped.values())


def remove_file(filename: str, library: str, owner: str) -> int:
    """删除指定库中「本人上传」的文件全部块（重建索引保留其余块），返回删除块数。"""
    store = get_kb_store()
    scope = scope_of(library, owner)

    def keep(d: dict) -> bool:
        return not (
            d.get("kind") == "knowledge"
            and d.get("scope") == scope
            and d.get("filename") == filename
            and d.get("owner", "") == owner
        )

    remaining = [d for d in store._documents.values() if keep(d)]
    removed = len(store._documents) - len(remaining)
    if removed == 0:
        return 0
    store.clear()
    for d in remaining:
        store.add_documents([d])
    store.save(KB_DIR)
    return removed


def find_chunk(owner: str, filename: str, chunk_hash: str) -> dict | None:
    """按块哈希取块正文及前后各一块（原文预览），仅限有权访问的库。"""
    allowed = {TEAM_SCOPE, personal_scope(owner)}
    docs = [d for d in get_kb_store()._documents.values()
            if d.get("kind") == "knowledge" and d.get("scope") in allowed]
    target = next((d for d in docs if d.get("filename") == filename and d.get("chunk_hash") == chunk_hash), None)
    if target is None:
        return None
    siblings = sorted(
        (d for d in docs
         if d.get("filename") == filename
         and d.get("scope") == target.get("scope")
         and d.get("owner") == target.get("owner")),
        key=lambda d: d.get("chunk_index", 0),
    )
    index = next((i for i, d in enumerate(siblings) if d.get("chunk_hash") == chunk_hash), 0)
    before = siblings[index - 1] if index > 0 else None
    after = siblings[index + 1] if index + 1 < len(siblings) else None
    return {
        "filename": filename,
        "library": target.get("library", "personal"),
        "uploader": target.get("owner", ""),
        "page": target.get("page", 0),
        "section_title": target.get("section_title", ""),
        "chunk_index": target.get("chunk_index", 0),
        "chunk_hash": chunk_hash,
        "text": target.get("text", ""),
        "before": before.get("text", "") if before else "",
        "after": after.get("text", "") if after else "",
    }
