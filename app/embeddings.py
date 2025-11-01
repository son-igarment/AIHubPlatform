import json
import math
import sqlite3
import string
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import settings
from . import dashboard as dashboard_mod
from .resilience import get_breaker

try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


router = APIRouter(prefix="/embeddings", tags=["embeddings"])


# -----------------------------
# Database
# -----------------------------
DB_PATH: Path = settings.KNOWLEDGE_DB_PATH


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    return con


def _init_db() -> None:
    con = _connect()
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT UNIQUE,
                content TEXT NOT NULL,
                embedding TEXT NOT NULL,
                norm REAL NOT NULL,
                meta TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        con.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_documents_updated
            AFTER UPDATE ON documents
            FOR EACH ROW BEGIN
                UPDATE documents SET updated_at=CURRENT_TIMESTAMP WHERE id=OLD.id;
            END;
            """
        )
        con.commit()
    finally:
        con.close()


_init_db()


# -----------------------------
# Embedding utilities
# -----------------------------
_client: Optional["OpenAI"] = None


def _get_client() -> Optional["OpenAI"]:
    global _client
    if OpenAI is None:
        return None
    if not settings.OPENAI_API_KEY:
        return None
    if _client is None:
        try:
            _client = OpenAI(api_key=settings.OPENAI_API_KEY)
        except Exception:
            return None
    return _client


def _simple_tokenize(text: str) -> List[str]:
    tbl = str.maketrans({c: " " for c in string.punctuation})
    return [t for t in text.lower().translate(tbl).split() if t]


def _local_embedding(text: str, dim: int = 384) -> List[float]:
    vec = [0.0] * dim
    tokens = _simple_tokenize(text)
    if not tokens:
        return vec
    for tok in tokens:
        idx = (hash(tok) % dim + dim) % dim
        vec[idx] += 1.0
    # sqrt normalization
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def compute_embedding(text: str) -> List[float]:
    client = _get_client()
    if client is None:
        dim = min(384, settings.EMBEDDING_DIM or 384)
        return _local_embedding(text, dim)
    try:
        breaker = get_breaker(
            key=f"openai:embeddings:{settings.EMBEDDING_MODEL}",
            fail_threshold=settings.AI_CIRCUIT_FAIL_THRESHOLD,
            reset_timeout=float(settings.AI_CIRCUIT_RESET_SEC),
        )
        if not breaker.allow_request():
            raise RuntimeError("embeddings_circuit_open")
        res = client.embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=text,
            timeout=max(0.5, settings.AI_TIMEOUT_MS / 1000 - 0.2),
        )
        breaker.record_success()
        return list(res.data[0].embedding)
    except Exception:
        # Fallback local on any API error
        try:
            breaker.record_failure()  # type: ignore[name-defined]
        except Exception:
            pass
        dim = min(384, settings.EMBEDDING_DIM or 384)
        return _local_embedding(text, dim)


def _norm(vec: List[float]) -> float:
    return math.sqrt(sum(v * v for v in vec)) or 1.0


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = _norm(a[:n])
    nb = _norm(b[:n])
    return dot / (na * nb)


# -----------------------------
# Schemas
# -----------------------------
class UpsertEmbeddingRequest(BaseModel):
    content: str = Field(..., min_length=1)
    doc_id: Optional[str] = Field(None, description="Stable external id")
    meta: Optional[Dict[str, Any]] = None


class UpsertEmbeddingResponse(BaseModel):
    doc_id: str
    content: str
    meta: Optional[Dict[str, Any]] = None
    dim: int


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = 5
    min_score: float = 0.0


class SearchHit(BaseModel):
    doc_id: str
    content: str
    score: float
    meta: Optional[Dict[str, Any]] = None


class SearchResponse(BaseModel):
    total: int
    top_k: int
    results: List[SearchHit]


class SearchKnowledgeRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = 5
    min_score: float = 0.0
    # Hybrid scoring: alpha * cosine + (1-alpha) * keyword
    alpha: float = Field(0.7, ge=0.0, le=1.0)
    use_hybrid: bool = True


class SearchKnowledgeResponse(BaseModel):
    total: int
    top_k: int
    alpha: float
    use_hybrid: bool
    results: List[SearchHit]


# -----------------------------
# CRUD helpers
# -----------------------------
def _upsert_document(doc_id: str, content: str, embedding: List[float], meta: Optional[Dict[str, Any]]) -> None:
    con = _connect()
    try:
        emb_json = json.dumps(embedding)
        norm = _norm(embedding)
        meta_json = json.dumps(meta) if meta is not None else None
        con.execute(
            """
            INSERT INTO documents(doc_id, content, embedding, norm, meta)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                content=excluded.content,
                embedding=excluded.embedding,
                norm=excluded.norm,
                meta=excluded.meta,
                updated_at=CURRENT_TIMESTAMP
            """,
            (doc_id, content, emb_json, norm, meta_json),
        )
        con.commit()
    finally:
        con.close()


def _all_documents() -> List[Tuple[str, str, List[float], Optional[Dict[str, Any]]]]:
    con = _connect()
    try:
        cur = con.execute("SELECT doc_id, content, embedding, meta FROM documents")
        rows = cur.fetchall()
        out: List[Tuple[str, str, List[float], Optional[Dict[str, Any]]]] = []
        for doc_id, content, emb_json, meta_json in rows:
            try:
                emb = json.loads(emb_json)
            except Exception:
                emb = []
            meta = None
            if meta_json:
                try:
                    meta = json.loads(meta_json)
                except Exception:
                    meta = None
            out.append((doc_id, content, emb, meta))
        return out
    finally:
        con.close()


def _bm25_keyword_scores(query: str, docs: List[Tuple[str, str]]) -> Dict[str, float]:
    # docs: List[(doc_id, content)]
    tokens_q = _simple_tokenize(query)
    if not tokens_q or not docs:
        return {}
    # Build DF and per-doc TF
    N = len(docs)
    df: Dict[str, int] = {}
    doc_tfs: Dict[str, Dict[str, int]] = {}
    doc_lens: Dict[str, int] = {}
    for doc_id, content in docs:
        toks = _simple_tokenize(content)
        doc_lens[doc_id] = len(toks) or 1
        tf: Dict[str, int] = {}
        seen: set[str] = set()
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
            if t not in seen:
                df[t] = df.get(t, 0) + 1
                seen.add(t)
        doc_tfs[doc_id] = tf
    avg_len = sum(doc_lens.values()) / max(N, 1)
    # BM25 parameters
    k = 1.2
    b = 0.75
    scores: Dict[str, float] = {doc_id: 0.0 for doc_id, _ in docs}
    for q in tokens_q:
        df_q = df.get(q, 0)
        idf = math.log(1 + (N - df_q + 0.5) / (df_q + 0.5)) if N > 0 else 0.0
        for doc_id, _ in docs:
            tf = doc_tfs[doc_id].get(q, 0)
            denom = tf + k * (1 - b + b * (doc_lens[doc_id] / (avg_len or 1)))
            bm25 = idf * ((tf * (k + 1)) / denom) if denom > 0 else 0.0
            scores[doc_id] += bm25
    # Normalize to [0,1]
    max_s = max(scores.values()) if scores else 0.0
    if max_s > 0:
        for d in scores:
            scores[d] = scores[d] / max_s
    return scores


# -----------------------------
# Routes
# -----------------------------
@router.post("/upsert", response_model=UpsertEmbeddingResponse)
def upsert_embedding(payload: UpsertEmbeddingRequest) -> UpsertEmbeddingResponse:
    doc_id = payload.doc_id or str(uuid.uuid4())
    emb = compute_embedding(payload.content)
    _upsert_document(doc_id, payload.content, emb, payload.meta)
    return UpsertEmbeddingResponse(doc_id=doc_id, content=payload.content, meta=payload.meta, dim=len(emb))


@router.post("/search", response_model=SearchResponse)
async def search_embeddings(payload: SearchRequest) -> SearchResponse:
    query_emb = compute_embedding(payload.query)
    docs = _all_documents()
    scored: List[Tuple[float, str, str, Optional[Dict[str, Any]]]] = []
    for doc_id, content, emb, meta in docs:
        if not emb:
            continue
        score = cosine_similarity(query_emb, emb)
        if score >= payload.min_score:
            scored.append((score, doc_id, content, meta))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[: max(0, payload.top_k)]
    resp = SearchResponse(
        total=len(scored),
        top_k=payload.top_k,
        results=[
            SearchHit(doc_id=d, content=c, score=round(s, 6), meta=m) for s, d, c, m in top
        ],
    )
    try:
        if top:
            await dashboard_mod.record_similarity(float(top[0][0]))
            await dashboard_mod.record_similarity_query(payload.query, top)
    except Exception:
        pass
    return resp


@router.post("/search_knowledge", response_model=SearchKnowledgeResponse)
async def search_knowledge(payload: SearchKnowledgeRequest) -> SearchKnowledgeResponse:
    query_emb = compute_embedding(payload.query)
    all_docs = _all_documents()
    # Prepare BM25 docs view
    docs_for_kw = [(doc_id, content) for doc_id, content, _emb, _meta in all_docs]
    kw_scores = _bm25_keyword_scores(payload.query, docs_for_kw) if payload.use_hybrid else {}
    scored: List[Tuple[float, str, str, Optional[Dict[str, Any]]]] = []
    for doc_id, content, emb, meta in all_docs:
        if not emb:
            continue
        cos = cosine_similarity(query_emb, emb)
        if payload.use_hybrid:
            kw = kw_scores.get(doc_id, 0.0)
            score = payload.alpha * cos + (1.0 - payload.alpha) * kw
        else:
            score = cos
        if score >= payload.min_score:
            scored.append((score, doc_id, content, meta))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[: max(0, payload.top_k)]
    resp = SearchKnowledgeResponse(
        total=len(scored),
        top_k=payload.top_k,
        alpha=payload.alpha,
        use_hybrid=payload.use_hybrid,
        results=[SearchHit(doc_id=d, content=c, score=round(s, 6), meta=m) for s, d, c, m in top],
    )
    try:
        if top:
            await dashboard_mod.record_similarity(float(top[0][0]))
            await dashboard_mod.record_similarity_query(payload.query, top)
    except Exception:
        pass
    return resp
