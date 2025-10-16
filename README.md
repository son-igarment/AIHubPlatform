# AIHubPlatform

Auth/User Management CLI (PowerShell)

This repo includes a simple PowerShell CLI to work with the Auth APIs from the AIHub Task Tracker backend:

Base URL: `https://aihubtasktracker-bwbz.onrender.com`

Endpoints wired:
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET  /api/v1/users/profile`
- `PUT  /api/v1/users/profile`
- `POST /api/v1/auth/logout`

Quick start
- Open PowerShell in the project folder.
- Use the commands below. A JWT is stored locally at `scripts/.token` after login.

Examples
- Register:
  `./scripts/auth.ps1 register -FullName "John Doe" -Email "john@example.com" -Password "Passw0rd!" -Role Founder -Position Fullstack_Developer`

- Login:
  `./scripts/auth.ps1 login -Email "john@example.com" -Password "Passw0rd!"`

- Get profile:
  `./scripts/auth.ps1 profile-get`

- Update profile (any field optional):
  `./scripts/auth.ps1 profile-update -FullName "Johnny" -Position Backend_Developer`

- Logout:
  `./scripts/auth.ps1 logout`

Notes
- You can override the base URL with env var `AIHUB_API_BASE_URL`.
- Accepted values:
  - Role: `Backend_Developer`, `Lead_Developer`, `Fullstack_Developer`, `Founder`
  - Position: `Backend_Developer`, `API_Integration_Engineer`, `Fullstack_Developer`, `Project_Manager`, `DevOps_Engineer`, `QA_Engineer`

**Python 3 Environment**
- Requirements listed in `requirements.txt`.
- Bootstrap locally (no system Python needed):
  `powershell -ExecutionPolicy Bypass -File scripts/setup_python.ps1`
- Activate: `.venv\Scripts\Activate.ps1`
- Verify: `python -V` and `python -m pip -V`
- Manage deps:
  - Install new: `tools\uv.exe pip install <pkg> -p .venv`
  - Or with pip: `python -m pip install <pkg>`

Auth API Test Cases
- 1) Login hợp lệ: status 200, trả về JWT (access_token, refresh_token). Kiểm tra thêm response time và log có ghi vào `logs/auth.log`.
- 2) Token hết hạn: status 401 khi gọi endpoint bảo vệ (ví dụ `/api/v1/me`). Có log cảnh báo tại `logs/auth.log` nêu rõ HTTPException 401.
- 3) Refresh token: status 200, cấp token mới khi gọi `POST /api/v1/auth/refresh` với refresh_token hợp lệ. Sau khi refresh, gọi lại endpoint bảo vệ phải thành công.

Ghi log KPI (auth.log)
- File: `logs/auth.log` (xoay vòng 5MB x 5)
- Nội dung: email (nếu có), ip, user-agent, request_id, dur_ms, status/detail cho lỗi.
- Dùng để team backend tích hợp Dashboard KPI tuần sau.
