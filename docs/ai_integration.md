# AI Integration (FastAPI + OpenAI)

## Cấu hình môi trường
- Tạo file `.env` tại project root hoặc thêm biến môi trường:
  
  ```
  OPENAI_API_KEY=sk-xxxx
  AI_MODEL=gpt-4o-mini   # (tùy chọn; mặc định)
  AI_TIMEOUT_MS=1800     # thời gian chờ OpenAI, đảm bảo SLA < 2s
  AI_CACHE_TTL_SECONDS=300  # TTL cache in-memory
  AI_CACHE_MAX=256          # tối đa record cache
  ```

## API mới
- `POST /api/v1/generate_ai_text`
  - Body (JSON):
    ```json
    { "prompt": "Write a welcome message for new users" }
    ```
- Response (JSON):
    ```json
    { "english": "...", "vietnamese": "...", "model": "gpt-4o-mini", "request_id": "..." }
    ```
  - Log truy vấn: ghi tại `logs/ai.log` (xoay vòng 5MB x 5).
  - SLA: API hoàn tất < 2s nhờ timeout + fallback nhanh. Nếu OpenAI quá chậm/không khả dụng, API trả về nội dung demo song ngữ ổn định thay vì lỗi.

## Kiểm thử nhanh
```powershell
curl -X POST http://127.0.0.1:8000/api/v1/generate_ai_text `
  -H "Content-Type: application/json" `
  -d '{"prompt":"Introduce the platform features"}'
```

- Nếu chưa cấu hình `OPENAI_API_KEY`, endpoint trả về nội dung demo song ngữ (offline fallback) để thuận tiện kiểm thử.
