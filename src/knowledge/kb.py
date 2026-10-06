"""知识库：用户上传文档 → 分块 → 嵌入 → 独立 FAISS 索引（data/knowledge_base）。

与论文检索索引（data/faiss）分离；检索逻辑复用 VectorStore.search，
上传文档带 kind="knowledge" 元数据，检索时自动纳入 RAG 知识边界。
"""

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from src.knowledge.vector_store import VectorStore

logger = logging.getLogger(__name__)

KB_DIR = os.path.join("data", "knowledge_base")
KB_CHUNK = 500  # 每块字符上限（中文按字符计，保守分块）
KB_PREVIEW = 80  # 列表预览字符数


def _split_chunks(text: str, size: int = KB_CHUNK) -> list[str]:
    """按段落折叠后定长切块，尽量保持语义就近。"""
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        while len(para) > size:
            chunks.append(para[:size])
            para = para[size:]
        if buf and len(buf) + len(para) + 1 > size:
            chunks.append(buf)
            buf = ""
        buf = (buf + "\n" + para).strip() if buf else para
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c]


_kb_store: VectorStore | None = None


def get_kb_store() -> VectorStore:
    global _kb_store
    if _kb_store is None:
        Path(KB_DIR).mkdir(parents=True, exist_ok=True)
        if os.path.exists(os.path.join(KB_DIR, "index.faiss")):
            _kb_store = VectorStore(index_path=KB_DIR)
        else:
            _kb_store = VectorStore()  # 全新空索引，首次 save 时落盘
    return _kb_store


def add_file(filename: str, content: str, owner: str = "") -> int:
    """分块入库并持久化，返回新增块数；owner 为上传者身份标识（用户间隔离）。"""
    store = get_kb_store()
    now = datetime.now(timezone.utc).isoformat()
    chunks = _split_chunks(content)
    if not chunks:
        return 0
    store.add_documents(
        [{"text": c, "kind": "knowledge", "filename": filename, "owner": owner, "uploaded_at": now} for c in chunks]
    )
    store.save(KB_DIR)
    return len(chunks)


def list_files(owner: str = "") -> list[dict]:
    """按文件名聚合「该 owner」的知识库文档：块数 / 总字符 / 最新时间 / 首块预览。"""
    store = get_kb_store()
    grouped: dict[str, dict] = {}
    for doc in store._documents.values():
        if doc.get("kind") != "knowledge" or doc.get("owner", "") != owner:
            continue
        fn = doc.get("filename", "")
        g = grouped.setdefault(fn, {"filename": fn, "chunks": 0, "chars": 0, "updated_at": "", "preview": ""})
        g["chunks"] += 1
        g["chars"] += len(doc.get("text", "") or "")
        ts = doc.get("uploaded_at", "") or ""
        if ts and (not g["updated_at"] or ts > g["updated_at"]):
            g["updated_at"] = ts
        if not g["preview"]:
            g["preview"] = ((doc.get("text", "") or "")[:KB_PREVIEW]).replace("\n", " ")
    return list(grouped.values())


def remove_file(filename: str, owner: str = "") -> int:
    """删除该 owner 名下文件的全部块（重建索引保留其余块），返回删除块数；无匹配返回 0。"""
    store = get_kb_store()
    remaining = [
        d for d in store._documents.values()
        if d.get("kind") != "knowledge"
        or d.get("owner", "") != owner
        or d.get("filename") != filename
    ]
    removed = len(store._documents) - len(remaining)
    if removed == 0:
        return 0
    store.clear()
    for d in remaining:
        store.add_documents([d])
    store.save(KB_DIR)
    return removed
