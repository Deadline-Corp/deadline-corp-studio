"""Compare retrieval quality: Chroma vs pgvector on 14 baseline queries.

Both backends share:
- Same embedding model (BAAI/bge-m3, normalize_embeddings=True)
- Same chunker (RecursiveCharacterTextSplitter chunk_size=500, overlap=50)
- Same KB files (kb/*.md, 12 files, ~82 chunks)

What differs:
- Index: Chroma DiskANN vs pgvector HNSW (m=16, ef_construction=64, cosine_ops)
- Distance metric exposed in score: Chroma L2 vs pgvector cosine distance
  (we don't compare scores directly, only the order of returned chunks)

Pass criteria:
- top-1 source matches on >= 80% of queries (12 of 14)
- top-3 sources have >= 67% overlap on average (i.e., 2 of 3 chunks overlap)

If under threshold, investigate: ef_search at query time, or chunk_size drift.

Run:
    python scripts/compare_retrieval.py
"""

import sys
from pathlib import Path

# Allow `from db.vector import ...` from scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from db.vector import similarity_search


CHROMA_DIR = Path(__file__).parent.parent / "chroma_db"
EMBEDDING_MODEL = "BAAI/bge-m3"
K = 3  # top-k chunks per query

QUERIES = [
    # RU — close to actual lead patterns from prompts.py few-shots
    "Сколько стоит сделать сайт?",
    "А приблизительно сколько?",
    "Делаете ли вы AI чат-ботов?",
    "У меня горит дедлайн, нужно за неделю",
    "Делаете мобильное приложение?",
    "А с 1С работаете?",
    "Telegram MiniApp нужен с оплатой",
    "Какие у вас гарантии?",
    "Как с вами связаться?",
    "А скидку дадите если возьму два проекта?",
    # EN
    "Do you do mobile apps?",
    "How much does an AI chatbot cost?",
    "Show me a case study for booking platforms",
    "Can you build a Telegram MiniApp e-commerce?",
]


def load_chroma() -> Chroma:
    if not CHROMA_DIR.exists():
        raise FileNotFoundError(f"Chroma DB not found at {CHROMA_DIR}. Run `python ingest.py` first.")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    return Chroma(persist_directory=str(CHROMA_DIR), embedding_function=embeddings)


def main() -> None:
    print(f"=== Loading Chroma from {CHROMA_DIR} ===")
    chroma_vs = load_chroma()

    print(f"=== Querying both backends with k={K} ===\n")

    top1_matches = 0
    top3_overlaps = []  # ratios per query

    for i, q in enumerate(QUERIES, 1):
        # Chroma
        chroma_results = chroma_vs.similarity_search_with_score(q, k=K)
        chroma_sources = [doc.metadata.get("source", "?") for doc, _ in chroma_results]

        # pgvector
        pg_results = similarity_search(q, k=K)
        pg_sources = [doc.metadata.get("source", "?") for doc in pg_results]

        # top-1 match
        top1_ok = chroma_sources[0] == pg_sources[0] if chroma_sources and pg_sources else False
        top1_matches += int(top1_ok)

        # top-K source overlap (multiset intersection / K).
        # set intersection wrongly deduplicates: [A,A,B] vs [A,A,B] gives {A,B}∩{A,B}=2,
        # i.e. 0.67 — even though the two lists are byte-identical. Counter & is correct.
        from collections import Counter
        overlap = sum((Counter(chroma_sources) & Counter(pg_sources)).values()) / K
        top3_overlaps.append(overlap)

        # Output side-by-side
        marker = "OK " if top1_ok else "MISS"
        print(f"[{i:>2}] {marker}  Q: {q}")
        print(f"     Chroma top-{K}: {chroma_sources}")
        print(f"     pgvec  top-{K}: {pg_sources}")
        print(f"     overlap@{K}: {overlap:.2f}")
        print()

    # Summary
    n = len(QUERIES)
    top1_rate = top1_matches / n
    avg_overlap = sum(top3_overlaps) / n

    print(f"=== Summary ===")
    print(f"Top-1 match rate:         {top1_matches}/{n}  ({top1_rate:.0%})")
    print(f"Avg top-{K} source overlap: {avg_overlap:.2f}")
    print()

    if top1_rate >= 0.80 and avg_overlap >= 0.67:
        print("PASS — retrieval parity acceptable. Safe to cut over to pgvector in Day 4.")
        sys.exit(0)
    else:
        print("INVESTIGATE — retrieval drift detected.")
        print("  Likely causes:")
        print("  - HNSW ef_search too low (default 40) — try setting ef_search=100 at query time")
        print("  - chunk_size or overlap not consistent between ingest.py and ingest_pg.py")
        print("  - Chroma chunks were ingested with a different bge-m3 version")
        sys.exit(1)


if __name__ == "__main__":
    main()
