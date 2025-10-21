import os
from pathlib import Path

from app.config import settings
from app.embeddings import compute_embedding, _upsert_document, _all_documents, cosine_similarity


def reset_db():
    if settings.KNOWLEDGE_DB_PATH.exists():
        settings.KNOWLEDGE_DB_PATH.unlink()
    # re-import to force re-init
    import importlib
    import app.embeddings as emb
    importlib.reload(emb)


def seed():
    docs = [
        ("iphone", "Apple releases the latest iPhone with advanced camera."),
        ("banana", "Bananas are sweet, yellow fruits rich in potassium."),
        ("fastapi", "FastAPI is a modern, fast web framework for building APIs with Python."),
        ("android", "Android phones provide a wide range of hardware options."),
    ]
    for doc_id, content in docs:
        emb = compute_embedding(content)
        _upsert_document(doc_id, content, emb, meta={"source": "seed"})


def run_queries():
    queries = [
        ("smartphone by Apple", 3),
        ("yellow fruit", 3),
        ("python web api", 3),
    ]
    for q, k in queries:
        q_emb = compute_embedding(q)
        scored = []
        for doc_id, content, emb, meta in _all_documents():
            if not emb:
                continue
            score = cosine_similarity(q_emb, emb)
            scored.append((score, doc_id, content))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:k]
        print(f"\nQuery: {q}")
        for s, d, c in top:
            print(f"  - {d:8s}  score={s:.4f}  content={c}")


if __name__ == "__main__":
    print("Resetting DB and seeding sample documents...")
    reset_db()
    seed()
    print("Running similarity queries:")
    run_queries()

