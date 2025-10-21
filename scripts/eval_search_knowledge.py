from app.embeddings import compute_embedding, _upsert_document, _all_documents, cosine_similarity
from app.embeddings import _simple_tokenize  # reuse tokenizer
from app.config import settings
import importlib


def reset_db():
    if settings.KNOWLEDGE_DB_PATH.exists():
        settings.KNOWLEDGE_DB_PATH.unlink()
    import app.embeddings as emb
    importlib.reload(emb)


def seed():
    corpus = [
        ("iphone", "Apple releases the latest iPhone with advanced camera and A-series chip."),
        ("banana", "Bananas are sweet, yellow fruits rich in potassium and fiber."),
        ("fastapi", "FastAPI is a modern, high-performance web framework for building APIs with Python."),
        ("android", "Android phones provide a wide range of hardware options across manufacturers."),
        ("grapes", "Grapes grow in clusters and can be eaten raw or used to make wine."),
        ("python", "Python is a versatile programming language popular for data science and automation."),
    ]
    for doc_id, content in corpus:
        emb = compute_embedding(content)
        _upsert_document(doc_id, content, emb, meta={"source": "eval"})


def evaluate():
    tests = [
        ("smartphone by Apple", "iphone"),
        ("yellow fruit", "banana"),
        ("python web api", "fastapi"),
        ("android devices", "android"),
        ("wine fruit", "grapes"),
        ("data science language", "python"),
    ]
    # Import the endpoint's scoring implementation via function call
    from app.embeddings import _bm25_keyword_scores

    docs = _all_documents()
    docs_for_kw = [(d, c) for d, c, _e, _m in docs]
    correct = 0
    total = len(tests)
    for query, expected_id in tests:
        # Hybrid score with alpha=0.7
        q_emb = compute_embedding(query)
        kw_scores = _bm25_keyword_scores(query, docs_for_kw)
        scored = []
        for doc_id, content, emb, _m in docs:
            if not emb:
                continue
            cos = cosine_similarity(q_emb, emb)
            kw = kw_scores.get(doc_id, 0.0)
            score = 0.7 * cos + 0.3 * kw
            scored.append((score, doc_id))
        top1 = sorted(scored, key=lambda x: x[0], reverse=True)[:1]
        if top1 and top1[0][1] == expected_id:
            correct += 1
    acc = correct / max(total, 1)
    print(f"Accuracy@1: {acc*100:.1f}%  (correct={correct}/{total})")


if __name__ == "__main__":
    reset_db()
    seed()
    evaluate()

