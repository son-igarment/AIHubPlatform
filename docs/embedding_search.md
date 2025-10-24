# Embedding Search (Cosine)

## Cấu hình
```
OPENAI_API_KEY=sk-xxxx                 # nếu có sẽ dùng OpenAI Embeddings
EMBEDDING_MODEL=text-embedding-3-small
KNOWLEDGE_DB_PATH=aihub_knowledge.db   # mặc định ở project root
EMBEDDING_DIM=1536                     # phù hợp với model text-embedding-3-small
```

## API
- POST `/api/v1/embeddings/upsert`
  - Body:
    ```json
    { "doc_id": "demo-1", "content": "Apple releases the latest iPhone...", "meta": {"source":"seed"} }
    ```
  - Lưu văn bản + embedding (OpenAI hoặc local fallback) vào SQLite `aihub_knowledge.db`.

- POST `/api/v1/embeddings/search`
  - Body:
    ```json
    { "query": "smartphone by Apple", "top_k": 3 }
    ```
  - Trả về danh sách tài liệu theo độ tương đồng cosine giảm dần.

## Test nhanh
```powershell
python scripts/test_embedding_search.py
```
- Script reset DB, seed 4 document mẫu, chạy 3 truy vấn ví dụ và in ra top kết quả theo điểm tương đồng.
