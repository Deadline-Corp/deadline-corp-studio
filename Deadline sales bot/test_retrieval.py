"""
Quick sanity check for RAG retrieval.

Run after `python ingest.py` to verify that meaningful chunks come back
for typical lead queries. Add/remove queries as you tune the knowledge base.

Usage:
    python test_retrieval.py
"""

from pathlib import Path
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

CHROMA_DIR = Path(__file__).parent / "chroma_db"

QUERIES = [
    # RU
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

def main():
    if not CHROMA_DIR.exists():
        print(f"ERROR: {CHROMA_DIR} not found. Run `python ingest.py` first.")
        return

    embeddings = HuggingFaceEmbeddings(
        model_name="BAAI/bge-m3",
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    vs = Chroma(persist_directory=str(CHROMA_DIR), embedding_function=embeddings)

    for q in QUERIES:
        print(f"\n{'=' * 70}")
        print(f"Q: {q}")
        results = vs.similarity_search_with_score(q, k=3)
        for i, (doc, score) in enumerate(results):
            src = doc.metadata.get("source", "?")
            preview = doc.page_content[:120].replace("\n", " ")
            # Lower score = closer in Chroma's L2 distance
            print(f"  [{i}] score={score:.3f}  {src}")
            print(f"      {preview}...")

if __name__ == "__main__":
    main()
