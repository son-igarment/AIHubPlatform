# AIHubPlatform

AIHubPlatform là bộ khung demo phục vụ phát triển nhanh dịch vụ Auth/Role-based, dashboard realtime và các công cụ hỗ trợ QA. Repo bao gồm:

- **FastAPI backend**: cung cấp login, refresh token, tuyến bảo vệ theo role và WebSocket stream cho dashboard.
- **Realtime dashboard**: trang React/Chart.js dựng sẵn, lấy số liệu tổng hợp qua REST/WebSocket.
- **Công cụ CLI & Postman**: script PowerShell `scripts/auth.ps1` tương tác với hạ tầng Auth bên ngoài; Postman collection đi kèm để kiểm thử nhanh.
- **Logging chuẩn hóa**: `logs/app.log` và `logs/auth.log` xoay vòng 5×5MB, bắt đủ request metadata phục vụ KPI.

## Kiến trúc & Luồng chính
- **Auth service**: FastAPI (`app/main.py`, `app/security.py`) dùng JWT (python-jose) + passlib[bcrypt]. Demo seed sẵn 2 user (Admin/Dev) khi khởi động.
- **Dashboard metrics**: `app/dashboard.py` chạy background loop tạo dữ liệu tổng hợp (tasks/reports) và đẩy WebSocket `/ws/metrics`; REST hỗ trợ `/api/v1/tasks/summary`, `/api/v1/reports/summary`, `/api/v1/metrics/history`.
- **Static UI**: `web/dashboard/index.html` mount tại `/dashboard`, kết nối tới API/WebSocket cùng origin nên không cần cấu hình CORS bổ sung.
- **Script chạy server**: `scripts/run_server.ps1` kiểm tra/khởi tạo virtualenv, load `.env`, rồi start `uvicorn app.main:app --reload`.
- **Automation scheduler**: `app/automation.py` sử dụng APScheduler để tự động crawl & update dữ liệu AI mỗi 6 giờ (mặc định) và gửi kết quả về Telegram.

```
[Client] -> /api/v1/auth/login -> JWT access+refresh
        -> /api/v1/auth/refresh -> refresh rotation (in-memory)
        -> /api/v1/me, /api/v1/protected/* (Bearer access token)
Dashboard UI ----REST----> /api/v1/*summary, /metrics/history
                   └----WebSocket----> /ws/metrics (realtime stream)
```

## Thiết lập nhanh
1. Cài Python 3.11+ (khuyến nghị dùng script bootstrap kèm repo).
2. Chạy `powershell -ExecutionPolicy Bypass -File scripts/setup_python.ps1` để tạo `.venv` và cài requirements.
3. Khởi động backend: `powershell -ExecutionPolicy Bypass -File scripts/run_server.ps1`.
   - Server mặc định: `http://127.0.0.1:8000`
   - OpenAPI/Swagger: `http://127.0.0.1:8000/docs`
   - Dashboard: `http://127.0.0.1:8000/dashboard/`
4. (Tuỳ chọn) cấu hình `.env` tại project root:
   ```
   SECRET_KEY=change-this
   ACCESS_TOKEN_EXPIRE_MINUTES=15
   REFRESH_TOKEN_EXPIRE_DAYS=7
   LOG_DIR=logs
   LOG_LEVEL=INFO
   ```

## Demo accounts & bảo mật
- `admin@example.com / Admin@123` (role: `Admin`)
- `dev@example.com / Dev@123` (role: `Dev`)
- Access token mặc định hết hạn 15 phút; refresh token 7 ngày và được rotate mỗi lần sử dụng.
- Token, user storage chỉ là in-memory phục vụ demo. Khi triển khai thật cần thay bằng database + cơ chế revoke bền vững.

## Công cụ liên quan
- **scripts/auth.ps1**: CLI gọn nhẹ để gọi Auth API từ backend AIHub Task Tracker (Render). Lưu JWT tại `scripts/.token`. Có thể override `AIHUB_API_BASE_URL`.
- **scripts/setup_python.ps1**: cài đặt `uv` và tạo virtualenv tự động (không cần quyền admin).
- **Postman collection**: `postman/AIHubPlatform_Auth.postman_collection.json` với flow đăng nhập, refresh, gọi endpoint bảo vệ.

## Logging & Giám sát
- `logs/app.log`: mọi request + exception (console + file), xoay vòng 5MB × 5.
- `logs/auth.log`: logger chuyên dụng ghi login, refresh, lỗi xác thực (thông tin gồm request_id, email, ip, user-agent, duration).
- Dashboard realtime đọc dữ liệu synthetic, thuận tiện cho việc benchmark UI hoặc thay thế bằng dữ liệu thật từ backend.

## Automation Scheduler
- Mặc định bật, chạy ngay sau khi server khởi động và lặp lại theo chu kỳ `AUTOMATION_INTERVAL_HOURS` (default 6 giờ).
- Cấu hình qua biến môi trường:
  - `AUTOMATION_ENABLED` (`true`/`false`)
  - `AUTOMATION_INTERVAL_HOURS` (số giờ, ví dụ `6`)
  - `AUTOMATION_RUN_AT_STARTUP` (`true` để chạy ngay khi start)
  - `AI_CRAWL_ENDPOINT`, `AI_CRAWL_METHOD`, `AI_CRAWL_PAYLOAD`
  - `AI_UPDATE_ENDPOINT`, `AI_UPDATE_METHOD`, `AI_UPDATE_PAYLOAD`
  - `AI_API_KEY`, `AI_EXTRA_HEADERS` (JSON object string) để chèn header bổ sung.
  - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_THREAD_ID` (tùy chọn), `TELEGRAM_DISABLE_NOTIFICATIONS`
- Nếu thiếu endpoint, bước tương ứng sẽ bị bỏ qua nhưng job vẫn ghi log. Kết quả mỗi lần chạy được gửi tới Telegram (nếu cấu hình) và ghi vào `logs/app.log`.

## Thư mục chính
- `app/`: mã nguồn FastAPI (config, bảo mật, dashboard router).
- `web/dashboard/`: static React + Chart.js bundle.
- `scripts/`: PowerShell helper (setup, run server, auth CLI).
- `postman/`: collection phục vụ QA.
- `tools/`: nhị phân `uv.exe` và script cài đặt.

## Tài liệu chi tiết
- [API Docs](docs/api.md)
- [Deployment Guide](docs/deployment.md)
- Phụ lục Auth CLI (vẫn giữ tại `README_Auth.md`).

> Gợi ý: tạo `python -m venv .venv` và thay đổi `SECRET_KEY` ngay khi đưa vào môi trường thực tế.
