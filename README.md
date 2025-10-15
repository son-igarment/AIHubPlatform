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
