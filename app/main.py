from logging.handlers import RotatingFileHandler
import logging
from pathlib import Path
import time
import uuid
from typing import Dict

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .models import LoginRequest, TokenPair, RefreshRequest, UserPublic
from .security import (
    authenticate,
    create_access_token,
    create_refresh_token,
    get_current_user,
    get_user_public,
    require_role,
    rotate_refresh_token,
    seed_demo_users,
    validate_refresh_token,
)


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    log_file = log_dir / "app.log"
    auth_file = log_dir / "auth.log"
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    # Console handler (guard against duplicates on reload)
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root_logger.addHandler(ch)

    # Root rotating file handler (5MB x 5) with duplicate guard
    if not any(isinstance(h, RotatingFileHandler) and getattr(h, 'baseFilename', '') == str(log_file) for h in root_logger.handlers):
        fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root_logger.addHandler(fh)

    # Dedicated auth logger to logs/auth.log (duplicate guard)
    auth_logger = logging.getLogger("auth")
    if not any(isinstance(h, RotatingFileHandler) and getattr(h, 'baseFilename', '') == str(auth_file) for h in auth_logger.handlers):
        ah = RotatingFileHandler(auth_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        ah.setFormatter(fmt)
        auth_logger.addHandler(ah)
    # Keep propagate True so auth logs also go to app.log and console


app = FastAPI(title=settings.APP_NAME, version="1.0.0")
setup_logging(settings.LOG_DIR, settings.LOG_LEVEL)
logger = logging.getLogger("auth")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")
    ua = request.headers.get("user-agent", "?")
    try:
        response = await call_next(request)
        return response
    except HTTPException as he:
        dur_ms = int((time.perf_counter() - start) * 1000)
        logger.warning(
            "HTTPException | id=%s | %s %s | status=%s | detail=%s | ip=%s | ua=%s | dur_ms=%s",
            request_id,
            request.method,
            request.url.path,
            he.status_code,
            he.detail,
            ip,
            ua,
            dur_ms,
        )
        raise
    except RequestValidationError as ve:
        dur_ms = int((time.perf_counter() - start) * 1000)
        logger.warning(
            "ValidationError | id=%s | %s %s | status=422 | errors=%s | ip=%s | ua=%s | dur_ms=%s",
            request_id,
            request.method,
            request.url.path,
            ve.errors(),
            ip,
            ua,
            dur_ms,
        )
        raise
    except Exception:
        dur_ms = int((time.perf_counter() - start) * 1000)
        logger.exception(
            "Unhandled | id=%s | %s %s | ip=%s | ua=%s | dur_ms=%s",
            request_id,
            request.method,
            request.url.path,
            ip,
            ua,
            dur_ms,
        )
        raise


@app.on_event("startup")
async def startup_event():
    seed_demo_users()
    logger.info("Application started. Demo users seeded.")


@app.get("/api/v1/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/auth/login", response_model=TokenPair)
async def login(payload: LoginRequest, request: Request):
    ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")
    ua = request.headers.get("user-agent", "?")
    req_id = getattr(request.state, "request_id", "-")
    start = time.perf_counter()
    try:
        user = authenticate(payload.email, payload.password)
        if not user:
            # Do not leak whether email exists
            logger.warning(
                "Login failed | id=%s | email=%s | ip=%s | ua=%s | reason=%s",
                req_id,
                payload.email,
                ip,
                ua,
                "invalid_credentials",
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)
        logger.info(
            "Login success | id=%s | email=%s | ip=%s | ua=%s | dur_ms=%s",
            req_id,
            user.email,
            ip,
            ua,
            int((time.perf_counter() - start) * 1000),
        )
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=int(settings.access_expires.total_seconds()),
        )
    except HTTPException:
        # Already logged above; let middleware preserve status
        raise
    except Exception:
        # Add context for unexpected errors in login flow
        logger.exception(
            "Login error | id=%s | email=%s | ip=%s | ua=%s",
            req_id,
            payload.email,
            ip,
            ua,
        )
        raise


@app.post("/api/v1/auth/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest):
    subject = validate_refresh_token(payload.refresh_token)
    access_token = create_access_token(subject)
    new_refresh = rotate_refresh_token(payload.refresh_token, subject)
    logger.info("Refresh token rotated for subject=%s", subject)
    return TokenPair(
        access_token=access_token,
        refresh_token=new_refresh,
        expires_in=int(settings.access_expires.total_seconds()),
    )


@app.get("/api/v1/me", response_model=UserPublic)
async def me(user=Depends(get_current_user)):
    return get_user_public(user)


@app.get("/api/v1/protected/dev", response_model=UserPublic)
async def dev_area(user=Depends(require_role("Dev"))):
    return get_user_public(user)


@app.get("/api/v1/protected/admin", response_model=UserPublic)
async def admin_area(user=Depends(require_role("Admin"))):
    return get_user_public(user)

