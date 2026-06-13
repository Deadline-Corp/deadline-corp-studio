"""Рантайм-ингест Knowledge Base (P4): добавить/заменить ОДИН источник в
kb_chunks без переингеста всей базы и без перезапуска.

В отличие от ingest_pg.py (TRUNCATE + перезалив всех kb/*.md при деплое), здесь
аддитивно: заменяем только чанки с данным `source`. Переиспользуем УЖЕ загруженную
модель эмбеддингов из main (не грузим bge-m3 заново).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def ingest_text(source: str, content: str, *, replace: bool = True) -> int:
    """Чанкует текст, эмбеддит и пишет в kb_chunks под именем `source`.

    replace=True (дефолт) — сначала удаляет прежние чанки этого source (идемпотентно
    обновляет один документ). Возвращает число записанных чанков (0 — если пусто)."""
    content = (content or "").strip()
    if not content:
        return 0

    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from db.connection import session_scope
    from db.models import KBChunk
    import main as _m  # переиспользуем загруженную модель _m.embeddings

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = [c for c in splitter.split_text(content) if c.strip()]
    if not chunks:
        return 0

    vectors = _m.embeddings.embed_documents(chunks)

    with session_scope() as db:
        if replace:
            db.query(KBChunk).filter(KBChunk.source == source).delete()
        for i, (c, v) in enumerate(zip(chunks, vectors)):
            db.add(KBChunk(source=source, chunk_index=i, content=c, embedding=v))

    logger.info("[kb_ingest] source=%r → %d чанков (replace=%s)", source, len(chunks), replace)
    return len(chunks)
