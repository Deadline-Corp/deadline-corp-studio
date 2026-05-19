"""Ingest knowledge base files into pgvector (`kb_chunks` table).

Parallel to ingest.py (which targets Chroma). Both can coexist — Week 1 runs
both retrievers side-by-side, main.py keeps using Chroma until Day 4. After
Day 4 cutover, ingest.py can be deleted.

Run:
    python ingest_pg.py            # uses DATABASE_URL from .env

The script is idempotent: TRUNCATE kb_chunks first, then re-inserts everything.
Safe to re-run after any kb/*.md edit.
"""

import logging
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from sqlalchemy import text

from db.connection import session_scope, engine
from db.models import KBChunk


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent
KB_DIR = ROOT / "kb"
EMBEDDING_MODEL = "BAAI/bge-m3"

# Same settings as ingest.py — keep retrieval comparable between Chroma and pgvector
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def load_documents() -> list[Document]:
    """Load all .md files from kb/ as LangChain documents."""
    if not KB_DIR.exists():
        raise FileNotFoundError(f"Knowledge base not found at {KB_DIR}.")

    md_files = sorted(KB_DIR.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"No .md files in {KB_DIR}.")

    docs = []
    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        docs.append(Document(page_content=content, metadata={"source": md_file.name}))
        log.info(f"  loaded {md_file.name} ({len(content)} chars)")

    log.info(f"Loaded {len(docs)} documents from {KB_DIR}")
    return docs


def split_documents(docs: list[Document]) -> list[Document]:
    """Chunk documents identically to ingest.py for fair Chroma vs pgvector comparison."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(docs)
    log.info(f"Split into {len(chunks)} chunks (size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP})")
    return chunks


def embed_chunks(chunks: list[Document]) -> list[list[float]]:
    """Embed all chunks with bge-m3. Same normalization as the query side
    so cosine distance via pgvector `<=>` matches the search semantics."""
    log.info(f"Loading embedding model {EMBEDDING_MODEL} (cached after first run)...")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    texts = [c.page_content for c in chunks]
    log.info(f"Embedding {len(texts)} chunks on CPU — typically 30-60 s for ~80 chunks...")
    vectors = embeddings.embed_documents(texts)
    log.info(f"Embeddings done. Shape: {len(vectors)} x {len(vectors[0])}")
    return vectors


def write_to_pgvector(chunks: list[Document], vectors: list[list[float]]) -> int:
    """TRUNCATE kb_chunks then bulk INSERT. Per-file chunk_index for human debugging."""
    per_file_counter: dict[str, int] = {}
    inserted = 0

    with session_scope() as db:
        # Idempotency: wipe everything before re-inserting. Cheap on ~80 rows.
        db.execute(text("TRUNCATE TABLE kb_chunks RESTART IDENTITY;"))

        for chunk, vec in zip(chunks, vectors):
            source = chunk.metadata.get("source", "?")
            idx = per_file_counter.setdefault(source, 0)
            per_file_counter[source] += 1

            db.add(KBChunk(
                source=source,
                chunk_index=idx,
                content=chunk.page_content,
                embedding=vec,
            ))
            inserted += 1

        # session_scope() commits on context exit

    return inserted


def smoke_test() -> None:
    """Quick similarity_search after ingest — confirms HNSW index works."""
    log.info("--- Smoke test retrieval ---")
    from db.vector import similarity_search

    queries = [
        "Сколько стоит сделать сайт?",
        "Делаете ли вы AI-агентов?",
        "Telegram MiniApp",
        "Do you do mobile apps?",
    ]
    for q in queries:
        results = similarity_search(q, k=2)
        log.info(f"\nQ: {q}")
        for i, doc in enumerate(results):
            preview = doc.page_content[:80].replace("\n", " ")
            log.info(f"  [{i}] {doc.metadata.get('source', '?')}: {preview}...")


def main() -> None:
    log.info(f"Target: {engine.url.render_as_string(hide_password=True)}")
    docs = load_documents()
    chunks = split_documents(docs)
    vectors = embed_chunks(chunks)
    inserted = write_to_pgvector(chunks, vectors)
    log.info(f"\nInserted {inserted} rows into kb_chunks.")

    try:
        smoke_test()
    except ImportError:
        log.warning("db/vector.py not yet written — skipping smoke test.")

    log.info("\nDone. kb_chunks is now ready for retrieval via db.vector.similarity_search.")


if __name__ == "__main__":
    main()
