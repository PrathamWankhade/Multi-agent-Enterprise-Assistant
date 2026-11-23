"""Document ingestion: load files, chunk text, and upsert into the vector store.

Supports: .txt, .md, .html, .pdf (optional), .docx (optional)
"""

import logging
import re
import uuid
from pathlib import Path
from typing import List, Optional

from app.rag.store import Chunk, get_vector_store

logger = logging.getLogger(__name__)


def _chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    """Split text into chunks on paragraph boundaries.

    Paragraph-aware splitting avoids cutting words in half and keeps related
    content (e.g. a markdown section) together when it fits.
    """
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        if current and current_len + para_len + 2 > chunk_size:
            chunks.append("\n\n".join(current))
            # Carry trailing paragraphs forward for overlap context.
            carry: List[str] = []
            carry_len = 0
            for prev in reversed(current):
                if carry_len + len(prev) + 2 > overlap:
                    break
                carry.insert(0, prev)
                carry_len += len(prev) + 2
            current = carry
            current_len = carry_len

        current.append(para)
        current_len += para_len + 2

    if current:
        chunks.append("\n\n".join(current))

    return chunks


async def ingest_text(
    text: str,
    source: str,
    doc_id: Optional[str] = None,
    chunk_size: int = 1000,
    overlap: int = 200,
    metadata: Optional[dict] = None,
) -> List[str]:
    doc_id = doc_id or source
    store = get_vector_store()
    await store.initialize()

    chunks = _chunk_text(text, chunk_size=chunk_size, overlap=overlap)

    # Idempotent re-ingestion: remove any previous version of this document.
    try:
        await store.delete_document(doc_id)
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not clear previous chunks for %s: %s", doc_id, exc)

    chunk_objs = [
        Chunk(
            text=c,
            source=source,
            doc_id=doc_id,
            page=None,
            section=None,
            metadata=metadata or {},
            # Deterministic id -> re-ingesting the same content replaces it.
            chunk_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{i}:{c[:64]}")),
        )
        for i, c in enumerate(chunks)
    ]
    return await store.add_chunks(chunk_objs)


async def ingest_file(
    file_path: str,
    doc_id: Optional[str] = None,
    chunk_size: int = 1000,
    overlap: int = 200,
    metadata: Optional[dict] = None,
) -> List[str]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError("File not found: " + file_path)

    source = path.name
    doc_id = doc_id or source

    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".py", ".json", ".csv"}:
        text = path.read_text(encoding="utf-8")
    elif suffix == ".html":
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            text = path.read_text(encoding="utf-8")
        else:
            html = path.read_text(encoding="utf-8")
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(separator="\n")
    elif suffix == ".pdf":
        try:
            import pypdf
        except ImportError as exc:
            raise ImportError("pypdf is required for PDF ingestion: pip install pypdf") from exc
        reader = pypdf.PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    elif suffix == ".docx":
        try:
            import docx
        except ImportError as exc:
            raise ImportError("python-docx is required for DOCX ingestion: pip install python-docx") from exc
        document = docx.Document(str(path))
        text = "\n".join(p.text for p in document.paragraphs)
    else:
        text = path.read_text(encoding="utf-8", errors="ignore")

    return await ingest_text(
        text=text,
        source=source,
        doc_id=doc_id,
        chunk_size=chunk_size,
        overlap=overlap,
        metadata=metadata or {},
    )


async def ingest_directory(
    directory: str,
    glob_pattern: str = "**/*",
    doc_id_prefix: Optional[str] = None,
    chunk_size: int = 1000,
    overlap: int = 200,
) -> List[str]:
    dir_path = Path(directory)
    results: List[str] = []
    for file_path in sorted(dir_path.glob(glob_pattern)):
        if file_path.is_file():
            if doc_id_prefix:
                doc_id = doc_id_prefix + file_path.name
            else:
                doc_id = None
            ids = await ingest_file(
                str(file_path),
                doc_id=doc_id,
                chunk_size=chunk_size,
                overlap=overlap,
            )
            results.extend(ids)
    return results