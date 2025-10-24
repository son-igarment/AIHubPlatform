# API Documentation

## Tổng quan
- **Base URL nội bộ**: `http://127.0.0.1:8000`
- **Prefix REST**: `/api/v1`
- **Định dạng**: JSON UTF-8 cho REST, text JSON qua WebSocket.
- **OpenAPI / Swagger UI**: `GET /docs`
- **ReDoc**: `GET /redoc`

```
Auth (JWT) ──> /api/v1/auth/login, /api/v1/auth/refresh
Protected ──> /api/v1/me, /api/v1/protected/*
Dashboard ──> /api/v1/tasks/summary, /api/v1/reports/summary, /api/v1/metrics/history
Realtime  ──> WebSocket /ws/metrics (snapshot + metric stream)
```

## Chuẩn chung
- Sử dụng header `Authorization: Bearer <access_token>` cho tất cả endpoint được bảo vệ.
- Response lỗi tuân theo schema FastAPI:
  ```json
  {
    "detail": "Invalid credentials"
  }
  ```
  hoặc với lỗi validation:
  ```json
  {
    "detail": [
      {
        "loc": ["body", "email"],
        "msg": "value is not a valid email address",
        "type": "value_error.email"
      }
    ]
  }
  ```
- Thời gian hết hạn mặc định: access token 15 phút, refresh token 7 ngày (có thể thay qua env).

## Endpoint chi tiết

### Healthcheck
- `GET /api/v1/health`
- Không yêu cầu auth.
- Response:
  ```json
  {"status": "ok"}
  ```

### Auth

#### `POST /api/v1/auth/login`
- Body:
  ```json
  {
    "email": "admin@example.com",
    "password": "Admin@123"
  }
  ```
- Response `200`:
  ```json
  {
    "access_token": "...",
    "refresh_token": "...",
    "token_type": "bearer",
    "expires_in": 900
  }
  ```
- Lỗi phổ biến:
  - `401 Invalid credentials`

#### `POST /api/v1/auth/refresh`
- Body:
  ```json
  {
    "refresh_token": "..."
  }
  ```
- Response `200`: trả về cặp token mới tương tự login. Refresh token cũ bị thu hồi.
- Lỗi:
  - `401 Refresh token revoked`
  - `401 Invalid token`

### User profile (yêu cầu `Authorization`)

| Endpoint | Role yêu cầu | Mô tả |
|----------|--------------|-------|
| `GET /api/v1/me` | Người dùng hợp lệ | Trả về thông tin công khai của user |
| `GET /api/v1/protected/dev` | `Dev` hoặc `Admin` | Kiểm tra role Dev |
| `GET /api/v1/protected/admin` | `Admin` | Kiểm tra role Admin |

Response mẫu:
```json
{
  "id": "8ad7ef9c-dbdd-48d3-93ac-048b6fe68f2b",
  "email": "admin@example.com",
  "full_name": "Admin User",
  "role": "Admin"
}
```

### Dashboard metrics

#### `GET /api/v1/tasks/summary`
- Response:
  ```json
  {
    "total": 100,
    "completed": 72,
    "pending": 24,
    "failed": 4,
    "updated_at": "2025-10-18T03:21:54.712345+00:00"
  }
  ```

#### `GET /api/v1/reports/summary`
- Cấu trúc tương tự phía trên với các trường `delivered_today`, `pending_review`, `failed`.

#### `GET /api/v1/metrics/history?limit=120`
- Query `limit` (1–300, mặc định 120) xác định số điểm metric gần nhất.
- Response:
  ```json
  {
    "points": [
      {"ts": "2025-10-18T03:21:18.123456+00:00", "value": 48},
      {"ts": "2025-10-18T03:21:19.123456+00:00", "value": 52}
    ]
  }
  ```

### WebSocket realtime

#### `GET /ws/metrics`
- Subprotocol: none. Payload dạng JSON text.
- **Trình tự sự kiện**:
  1. Server chấp nhận kết nối và gửi gói `snapshot`:
     ```json
     {
       "type": "snapshot",
       "points": [{"ts": "...", "value": 50}, ...],
       "tasks": {...},
       "reports": {...}
     }
     ```
  2. Mỗi ~1 giây server gửi gói `metric`:
     ```json
     {
       "type": "metric",
       "point": {"ts": "...", "value": 55},
       "tasks": {...},   // optional drift update
       "reports": {...}  // optional drift update
     }
     ```
  3. Client có thể gửi keep-alive bất kỳ (UI mẫu gửi text `"ok"`).
- Server sẽ loại bỏ client khi lỗi gửi hoặc đóng kết nối.

## Static assets
- `GET /dashboard/` phục vụ file `web/dashboard/index.html`.
- Các asset khác (JS/CSS inline) được bundle trực tiếp trong file HTML nên không cần build step riêng.

## Postman & CLI ngoài
- **Postman**: `postman/AIHubPlatform_Auth.postman_collection.json` giúp kiểm thử nhanh luồng Auth/Role.
- **PowerShell CLI**: `scripts/auth.ps1` hướng tới Auth service triển khai trên Render (`AIHUB_API_BASE_URL`), không trùng với backend FastAPI demo. Sử dụng khi cần test tích hợp với nền tảng gốc.

## Automation Scheduler
- `app/automation.py` chạy nền sử dụng APScheduler. Job mặc định chạy ngay khi khởi động và lặp lại mỗi `AUTOMATION_INTERVAL_HOURS` (6 giờ).
- Hai bước chính trong job:
  1. **Crawl**: gọi `AI_CRAWL_ENDPOINT` (method mặc định `GET`). Có thể khai báo payload JSON qua `AI_CRAWL_PAYLOAD`.
  2. **Update**: gửi dữ liệu vừa crawl tới `AI_UPDATE_ENDPOINT` (method mặc định `POST`). Payload mặc định chứa trường `data` là kết quả crawl; có thể bổ sung qua `AI_UPDATE_PAYLOAD`.
- Header bổ sung cho cả hai bước có thể cấu hình bằng `AI_API_KEY` (Bearer) hoặc JSON `AI_EXTRA_HEADERS`.
- Tổng kết job được log vào `logs/app.log` và nếu cấu hình `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` thì sẽ đẩy thông báo Telegram.
