import asyncio
import hashlib
import os
import re
from typing import Iterable, List
from pypdf import PdfReader

from ..config import Settings
from .embedding_service import EmbeddingService
from .search_service import SearchService


def _chunk_text(text: str, target_chars: int = 1800, overlap: int = 200) -> List[str]:
    """Split on paragraph boundaries, accumulating ~target_chars per chunk with overlap.

    1800 chars ≈ 450 tokens, which is a good RAG chunk size.
    """
    text = re.sub(r"\s+\n", "\n", text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: List[str] = []
    buf = ""
    for para in paragraphs:
        if len(buf) + len(para) + 2 <= target_chars:
            buf = (buf + "\n\n" + para).strip()
        else:
            if buf:
                chunks.append(buf)
            # Start a new chunk with a small overlap from the previous one
            if chunks and overlap > 0:
                tail = chunks[-1][-overlap:]
                buf = (tail + "\n\n" + para).strip()
            else:
                buf = para
    if buf:
        chunks.append(buf)
    return chunks


def _extract_pdf(path: str) -> str:
    reader = PdfReader(path)
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _doc_id(path: str, idx: int) -> str:
    base = os.path.basename(path)
    digest = hashlib.md5(path.encode("utf-8")).hexdigest()[:8]
    return f"doc-{digest}-{idx}"


class DocumentIngestion:
    """Indexes documents from a local folder into Azure AI Search."""

    def __init__(self, settings: Settings, docs_dir: str):
        self._settings = settings
        self._docs_dir = docs_dir
        self._search = SearchService(settings)
        self._embeddings = EmbeddingService(settings)

    def _iter_pdfs(self) -> Iterable[str]:
        if not os.path.isdir(self._docs_dir):
            return []
        return [
            os.path.join(self._docs_dir, f)
            for f in sorted(os.listdir(self._docs_dir))
            if f.lower().endswith(".pdf")
        ]

    async def ingest_all(self) -> int:
        files = list(self._iter_pdfs())
        if not files:
            return 0

        docs: list = []
        for path in files:
            try:
                text = _extract_pdf(path)
            except Exception as exc:
                print(f"[DOC] failed to read {path}: {exc}")
                continue
            if not text.strip():
                continue
            title = os.path.splitext(os.path.basename(path))[0]
            for idx, chunk in enumerate(_chunk_text(text)):
                docs.append({
                    "id": _doc_id(path, idx),
                    "source_table": "docs",
                    "record_id": os.path.basename(path),
                    "title": title,
                    "content": chunk,
                    "portfolio_team": "",
                    "stage": "",
                    "owner": "",
                })

        if not docs:
            return 0

        embeddings = await self._embeddings.embed_batch([d["content"] for d in docs])
        for doc, emb in zip(docs, embeddings):
            doc["content_vector"] = emb

        async with self._search._search_client() as client:
            for i in range(0, len(docs), 100):
                await client.upload_documents(docs[i : i + 100])

        print(f"[DOC] indexed {len(docs)} chunks from {len(files)} PDF(s)")
        return len(docs)
