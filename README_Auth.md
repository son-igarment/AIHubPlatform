Auth API (FastAPI)

This adds a local FastAPI service providing login, refresh-token and role-based authorization (Admin/Dev), detailed error logging, and a ready-to-use Postman collection.

- Stack: FastAPI + JWT (python-jose) + passlib[bcrypt]
- Roles: `Admin`, `Dev`
- Demo users:
  - admin@example.com / Admin@123 (role: Admin)
  - dev@example.com / Dev@123 (role: Dev)

Setup
- Create/refresh Python environment
  - powershell -ExecutionPolicy Bypass -File scripts/setup_python.ps1
  - .venv\Scripts\Activate.ps1
- Start server
  - powershell -ExecutionPolicy Bypass -File scripts/run_server.ps1
  - Base URL: http://127.0.0.1:8000
  - Docs: http://127.0.0.1:8000/docs
- Optional .env in repo root:
  - SECRET_KEY=change-this
  - ACCESS_TOKEN_EXPIRE_MINUTES=15
  - REFRESH_TOKEN_EXPIRE_DAYS=7
  - LOG_DIR=logs
  - LOG_LEVEL=INFO

Endpoints
- POST /api/v1/auth/login
  - Body: { "email": "...", "password": "..." }
  - Returns: { access_token, refresh_token, token_type, expires_in }
- POST /api/v1/auth/refresh
  - Body: { "refresh_token": "..." }
  - Returns: new access/refresh token pair and expires_in
- GET /api/v1/me
  - Auth: Bearer access_token
  - Returns current user info
- GET /api/v1/protected/dev
  - Auth: Bearer access_token
  - Requires role Dev or Admin
- GET /api/v1/protected/admin
  - Auth: Bearer access_token
  - Requires role Admin

Authorization model
- Access token (short TTL) for API access
- Refresh token (longer TTL) for renewing access
- Refresh tokens are rotated and stored in-memory for demo purposes (revokes older token on refresh)

Logging
- Logs are written to `logs/app.log` with rotation (5MB x 5)
- HTTP exceptions log at warning level; unhandled errors log stack traces at error level

Postman
- Collection: `postman/AIHubPlatform_Auth.postman_collection.json`
- Quick flow:
  - Login with demo accounts → save `access_token` and `refresh_token` as collection variables
  - Call `/api/v1/me` with `Authorization: Bearer {{access_token}}`
  - Call role-protected endpoints
  - Refresh tokens by calling `/api/v1/auth/refresh` and update variables

Notes
- This service is self-contained for local development and demos. For production, replace in-memory stores with a database and proper token revocation strategy.
- Keep `SECRET_KEY` private and strong.
