# Deployment Guide

Tài liệu này mô tả các bước triển khai AIHubPlatform cho môi trường dev, staging và production nhỏ gọn.

## 1. Yêu cầu hệ thống
- **Hệ điều hành**: Windows, macOS hoặc Linux.
- **Python**: 3.11 trở lên (đã kiểm thử với 3.11.9). Nên sử dụng virtualenv riêng.
- **Kiến thức nền**: FastAPI, Uvicorn, JWT, PowerShell (để dùng script có sẵn).
- **Thư viện Python** (đã khai báo trong `requirements.txt`):
  - `fastapi`, `uvicorn[standard]`, `python-jose[cryptography]`, `passlib[bcrypt]`, `python-dotenv`, `requests`, `email-validator`, `bcrypt`.
- **Tùy chọn**: reverse proxy (Nginx/Caddy), process manager (systemd, Supervisor, pm2), chứng chỉ TLS.

## 2. Chuẩn bị môi trường
1. Clone hoặc copy source code lên máy chủ.
2. Tạo virtualenv:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/macOS
   .\.venv\Scripts\Activate.ps1  # Windows PowerShell
   ```
3. Cài dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Tạo file `.env` tại project root (ít nhất phải đặt `SECRET_KEY`):
   ```env
   APP_NAME=AIHub Auth API
   SECRET_KEY=<chuoi-bao-mat>
   ACCESS_TOKEN_EXPIRE_MINUTES=15
   REFRESH_TOKEN_EXPIRE_DAYS=7
   LOG_DIR=logs
   LOG_LEVEL=INFO
   ```
   - `LOG_DIR` được tạo tự động nếu chưa tồn tại.
   - Khi deploy nhiều instance, đảm bảo từng instance có secret key riêng hoặc chia sẻ kho revoke chung.

## 3. Khởi chạy dịch vụ

### 3.1 Development (hot reload)
```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_server.ps1
```
- Script sẽ tự bootstrap `.venv` nếu thiếu, load biến từ `.env` và chạy `uvicorn app.main:app --reload`.
- Dashboard có sẵn tại `http://127.0.0.1:8000/dashboard/`.

### 3.2 Production (không reload)
Chạy trực tiếp bằng uvicorn trong virtualenv:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
```
- Sử dụng tham số `--workers` phù hợp CPU/IO.
- Khuyến nghị đặt sau reverse proxy (ví dụ Nginx) để phục vụ TLS, gzip, caching static `/dashboard`.
- Nếu cần systemd unit mẫu:
  ```
  [Unit]
  Description=AIHubPlatform FastAPI
  After=network.target

  [Service]
  WorkingDirectory=/opt/aihub-platform
  Environment="LOG_DIR=/var/log/aihub"
  ExecStart=/opt/aihub-platform/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
  Restart=on-failure
  User=aihub

  [Install]
  WantedBy=multi-user.target
  ```

## 4. Cấu hình bổ sung

### 4.1 Logging
- `logs/app.log` và `logs/auth.log` sử dụng rotating file handler (5MB × 5).
- Khi deploy trong Linux, nên đặt `LOG_DIR=/var/log/aihub` và đảm bảo user chạy service có quyền ghi.
- Có thể chuyển sang stdout (phù hợp container) bằng cách sửa `app/main.py > setup_logging`.

### 4.2 Reverse proxy gợi ý (Nginx)
```
server {
    listen 80;
    server_name dashboard.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```
- Lưu ý giữ nguyên header `Upgrade` để WebSocket hoạt động.

### 4.3 Static dashboard
- `app.main` mount static `web/dashboard`. Khi cần build riêng, chỉ việc cập nhật file HTML (không cần pipeline phức tạp).
- Nếu tách front-end sang CDN, nhớ cập nhật `StaticFiles` hoặc phục vụ bằng web server ngoài.

### 4.4 Seed dữ liệu demo
- Khi ứng dụng start (`@app.on_event("startup")`), hàm `seed_demo_users()` sẽ tạo 2 user (`admin@example.com`, `dev@example.com`). Trong môi trường thật nên thay bằng migration hoặc call tới database.

## 5. Bảo mật & vận hành
- **SECRET_KEY**: bắt buộc thay bằng chuỗi mạnh (>= 32 bytes). Không commit file `.env`.
- **HTTPS**: luôn phục vụ qua HTTPS ở môi trường công khai.
- **Rate limiting**: cân nhắc thêm ở reverse proxy để bảo vệ endpoint `/api/v1/auth/login`.
- **Refresh token store**: hiện tại lưu trong RAM (`_refresh_store`). Triển khai thực tế cần dùng Redis/DB để dùng chung giữa nhiều instance.
- **Monitoring**: tail `logs/auth.log` để theo dõi login bất thường. Có thể ship log tới ELK/CloudWatch bằng filebeat/agent.

## 6. Quy trình nâng cấp
1. Pull source mới (hoặc deploy artifact).
2. Chạy `pip install -r requirements.txt --upgrade` trong virtualenv.
3. Thực hiện smoke test:
   - `curl http://127.0.0.1:8000/api/v1/health`
   - Login bằng demo account, verify `/api/v1/me`.
   - Mở `/dashboard/`, kiểm tra realtime metrics cập nhật.
4. Khởi động lại service (systemd `systemctl restart`, Docker `docker compose up -d`, v.v.).

## 7. Rollback
- Giữ lại bản phát hành trước đó (folder code + `.venv` snapshot hoặc container image).
- Nếu gặp lỗi nghiêm trọng, quay lại bản cũ và restore lại file `.env` + dữ liệu cần thiết.
- Vì refresh token nằm trong RAM, restart service sẽ vô hiệu hoá toàn bộ token cũ → kế hoạch rollback nên thông báo người dùng đăng nhập lại.
