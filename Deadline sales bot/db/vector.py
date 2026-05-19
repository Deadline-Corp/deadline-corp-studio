"""Similarity search over kb_chunks (pgvector backend).

Drop-in semantic equivalent to `vectorstore.similarity_search(query, k=4)` from
the Chroma path, returning langchain `Document` objects with same metadata
shape so main.py change in Day 4 is a one-line swap.

Embedding model: BAAI/bge-m3 with normalize_embeddings=True (matches ingest_pg).
Distance: pgvector cosine `<=>`. Lower = more similar.

Cold start: first call loads bge-m3 from cache (~3-5s). Subsequent calls reuse
the in-process embedder. For Railway, this means the first /chat request after
container spin-up is slower — consider pre-warming in main.py startup hook
when the migration to /message lands.
"""

from __future__ import annotations

import logging
from typing import Optional

from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from sqlalchemy import select

from db.connection import session_scope
from db.models import KBChunk


log = logging.getLogger(__name__)

EMBEDDING_MODEL = "BAAI/bge-m3"

# Lazy singleton — load once, reuse across requests
_EMBEDDER: Optional[HuggingFaceEmbeddings] = None


def _get_embedder() -> HuggingFaceEmbeddings:
    """Return cached HuggingFaceEmbeddings, loading on first call."""
    global _EMBEDDER
    if _EMBEDDER is None:
        log.info(f"Loading {EMBEDDING_MODEL} (first call — ~3-5s)...")
        _EMBEDDER = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
    return _EMBEDDER


def similarity_search(query: str, k: int = 4) -> list[Document]:
    """Top-k nearest kb_chunks to `query` via pgvector cosine distance.

    Args:
        query: User question / lead message
        k: How many chunks to return

    Returns:
        List of langchain Document with page_content + metadata={source, chunk_index, distance}
        Empty list if kb_chunks is empty.
    """
    embedder = _get_embedder()
    query_vec = embedder.embed_query(query)

    with session_scope() as db:
        # pgvector exposes .cosine_distance() on the Vector column type.
        # The HNSW index on kb_chunks.embedding (created in migration 001) is
        # used automatically for this ORDER BY ... LIMIT pattern.
        rows = db.execute(
            select(
                KBChunk,
                KBChunk.embedding.cosine_distance(query_vec).label("distance"),
            )
            .order_by(KBChunk.embedding.cosine_distance(query_vec))
            .limit(k)
        ).all()

        docs = [
            Document(
                page_content=row.KBChunk.content,
                metadata={
                    "source": row.KBChunk.source,
                    "chunk_index": row.KBChunk.chunk_index,
                    "distance": float(row.distance),
                },
            )
            for row in rows
        ]
        return docs


def similarity_search_with_score(query: str, k: int = 4) -> list[tuple[Document, float]]:
    """Same as similarity_search but returns (Document, distance) tuples.

    Matches the Chroma `.similarity_search_with_score()` interface used in
    test_retrieval.py — lower distance = more similar, like Chroma's L2.
    """
    docs = similarity_search(query, k)
    return [(d, d.metadata["distance"]) for d in docs]
