# ClickUp Webhook → Done ⇒ Next Flow

- Endpoints: `POST /api/task/`, `POST /api/v1/task/`, `POST /api/task/webhook`
- Route `/api/task/webhook` yêu cầu HMAC SHA256 (header `X-ClickUp-Signature` hoặc `X-Signature`) với khóa `CLICKUP_WEBHOOK_SECRET`.
- Purpose: handle ClickUp task webhooks; when a task status becomes “done”, trigger the “Done ⇒ Next” automation with a unique `run_id`.

What happens on status=done
- Generate `run_id` (UUID) and include it in response and fan-out headers (`X-Run-Id`).
- Idempotency: in-memory LRU (size ~1024) dedupes events by key `task_id:status:date_updated` to avoid loops/duplicates.
- Fan-out actions:
  - Update Google Sheet via a webhook URL (if configured).
  - Send a Telegram message (if configured).
  - Attach label `AIHubAuto` via ClickUp API (if `CLICKUP_API_TOKEN` provided).
  - Call `start_next_task` via a configurable HTTP endpoint.
  - Dispatch `automation_scheduler.run_now(origin="clickup_webhook")` to kick off the pipeline `crawl_keywords → update_embeddings → purge_duplicates`.
- Every flow step is logged as JSON (`flow_event | {...}`) into `logs/automation_logs.log`.

Security (optional)
- If you set `TASK_WEBHOOK_TOKEN` (or `WEBHOOK_TOKEN`), the endpoint requires header `X-Webhook-Token` to match.

Environment variables
- `SHEETS_WEBHOOK_URL` (or `GOOGLE_SHEETS_WEBHOOK`): URL to receive JSON updates for Sheets.
- `NEXT_TASK_URL` (or `START_NEXT_TASK_URL`): URL to receive JSON `{ action: "start_next_task", run_id, context: {...} }`.
- `TASK_WEBHOOK_TOKEN` (optional): shared secret token for webhook requests.
- `CLICKUP_API_TOKEN` (optional): ClickUp token to call `POST https://api.clickup.com/api/v2/task/{id}/tag` and attach the `AIHubAuto` label when a task is completed.

Example cURL (`/api/task/` với token chia sẻ)
```bash
curl -X POST http://127.0.0.1:8000/api/task/ \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Token: $TASK_WEBHOOK_TOKEN" \
  -d '{
        "task": {
          "id": "86a-123",
          "name": "Deploy dashboard",
          "status": {"status": "done"},
          "date_updated": "2025-10-27T09:42:00Z",
          "list": {"id": "list_1"},
          "assignees": [{"username": "alice"}]
        }
      }'
```

Response
```json
{ "ok": true, "run_id": "...", "next": { /* optional downstream response */ } }
```

Example HMAC request
```bash
curl -X POST http://127.0.0.1:8000/api/task/webhook \
  -H "Content-Type: application/json" \
  -H "X-ClickUp-Signature: $(python - <<'PY'
import hmac, hashlib, json, os
body = json.dumps({"task": {"id": "86a-123", "status": {"status": "done"}}})
secret = os.environ["CLICKUP_WEBHOOK_SECRET"].encode()
print(hmac.new(secret, body.encode(), hashlib.sha256).hexdigest())
PY
)" \
  -d '{"task": {"id": "86a-123", "status": {"status": "done"}}}'
```
