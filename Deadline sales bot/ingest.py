"""
Ingest knowledge base files into Chroma vector DB.

Run:
    python ingest.py

Re-run whenever you edit files in kb/. The script wipes and rebuilds chroma_db/.
"""

import os
import shutil
import logging
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent
KB_DIR = ROOT / "kb"
CHROMA_DIR = ROOT / "chroma_db"
EMBEDDING_MODEL = "BAAI/bge-m3"  # multilingual RU/EN, free, runs on CPU

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def load_documents() -> list:
    """Load all .md files from kb/ as LangChain documents."""
    if not KB_DIR.exists():
        raise FileNotFoundError(f"Knowledge base not found at {KB_DIR}. Create kb/ folder with .md files.")

    docs = []
    md_files = sorted(KB_DIR.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"No .md files in {KB_DIR}.")

    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        docs.append(Document(page_content=content, metadata={"source": md_file.name}))
        log.info(f"  · loaded {md_file.name} ({len(content)} chars)")

    log.info(f"Loaded {len(docs)} documents from {KB_DIR}")
    return docs


def split_documents(docs: list) -> list:
    """Chunk documents for embedding."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(docs)
    log.info(f"Split into {len(chunks)} chunks (size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP})")
    return chunks


def build_vectorstore(chunks: list) -> Chroma:
    """Embed chunks with bge-m3 and persist to chroma_db/."""
    log.info(f"Loading embedding model {EMBEDDING_MODEL} (first run downloads ~2GB, cached afterwards)...")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    # Wipe previous index to keep things deterministic
    if CHROMA_DIR.exists():
        shutil.rmtree(CHROMA_DIR)
        log.info(f"Removed existing {CHROMA_DIR}")

    log.info("Embedding chunks and writing to Chroma (this can take 1-3 min on first run)...")
    vs = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=str(CHROMA_DIR),
    )
    log.info(f"✓ Vector DB saved to {CHROMA_DIR}")
    return vs


def smoke_test(vs: Chroma) -> None:
    """Quick retrieval sanity check on a few queries."""
    test_queries = [
        "Сколько стоит сделать сайт?",
        "Делаете ли вы AI-агентов?",
        "У меня горит дедлайн",
        "Telegram MiniApp",
        "Do you do mobile apps?",
    ]
    log.info("\n--- Smoke test retrieval ---")
    for q in test_queries:
        results = vs.similarity_search(q, k=2)
        log.info(f"\nQ: {q}")
        for i, doc in enumerate(results):
            preview = doc.page_content[:80].replace("\n", " ")
            log.info(f"  [{i}] {doc.metadata.get('source', '?')}: {preview}...")


def main():
    docs = load_documents()
    chunks = split_documents(docs)
    vs = build_vectorstore(chunks)
    smoke_test(vs)
    log.info("\n✓ Done. Run `uvicorn main:app --reload` to start the bot.")


if __name__ == "__main__":
    main()
