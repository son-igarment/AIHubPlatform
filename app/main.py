from logging.handlers import RotatingFileHandler
import logging
from pathlib import Path
import time
import uuid
from typing import Dict

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from .config import settings
from . import dashboard
from . import modules as modules_core
from .automation import automation_scheduler
from .storage import init_persistence
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
from .security import decode_token


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    log_file = log_dir / "app.log"
    auth_file = log_dir / "auth.log"
    ai_file = log_dir / "ai.log"
    modules_file = log_dir / "modules_toggles.log"
    automation_file = log_dir / "automation_logs.log"
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

    # Dedicated AI logger to logs/ai.log (duplicate guard)
    ai_logger = logging.getLogger("ai")
    if not any(isinstance(h, RotatingFileHandler) and getattr(h, 'baseFilename', '') == str(ai_file) for h in ai_logger.handlers):
        aih = RotatingFileHandler(ai_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        aih.setFormatter(fmt)
        ai_logger.addHandler(aih)

    # Dedicated modules logger to logs/modules_toggles.log (duplicate guard)
    modules_logger = logging.getLogger("modules")
    if not any(isinstance(h, RotatingFileHandler) and getattr(h, 'baseFilename', '') == str(modules_file) for h in modules_logger.handlers):
        mh = RotatingFileHandler(modules_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        mh.setFormatter(fmt)
        modules_logger.addHandler(mh)

    automation_logger = logging.getLogger("automation.flow")
    if not any(isinstance(h, RotatingFileHandler) and getattr(h, 'baseFilename', '') == str(automation_file) for h in automation_logger.handlers):
        autoh = RotatingFileHandler(automation_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        autoh.setFormatter(fmt)
        automation_logger.addHandler(autoh)


app = FastAPI(title=settings.APP_NAME, version="1.0.0")
setup_logging(settings.LOG_DIR, settings.LOG_LEVEL)
init_persistence()
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
    status_code = 500
    response = None
    try:
        response = await call_next(request)
        status_code = getattr(response, "status_code", 200)
        return response
    except HTTPException as he:
        dur_ms = int((time.perf_counter() - start) * 1000)
        status_code = he.status_code
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
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
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
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
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
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        path = request.url.path
        try:
            await dashboard.record_request_metric(path, status_code, duration_ms)
        except Exception:
            logging.getLogger(__name__).exception("Failed to record dashboard metric")


@app.on_event("startup")
async def startup_event():
    seed_demo_users()
    logger.info("Application started. Demo users seeded.")
    # Ensure modules.json exists with defaults
    try:
        modules_core.ensure_modules_file()
    except Exception:
        logging.getLogger(__name__).exception("Failed to initialize modules.json")
    # kick off dashboard metrics loop
    try:
        await dashboard.ensure_loop_started()
    except Exception:
        logging.getLogger(__name__).exception("Failed to start dashboard metrics loop")
    try:
        await automation_scheduler.start()
    except Exception:
        logging.getLogger(__name__).exception("Failed to start automation scheduler")


@app.on_event("shutdown")
async def shutdown_event():
    try:
        await automation_scheduler.shutdown()
    except Exception:
        logging.getLogger(__name__).exception("Failed to shut down automation scheduler")


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



# Include dashboard API routes
app.include_router(dashboard.router)

# Include AI routes
try:
    from . import ai
    app.include_router(ai.router, prefix="/api/v1")
except Exception:
    logging.getLogger(__name__).exception("Failed to include AI routes")

# Include Modules API routes
try:
    from .modules_api import router as modules_router
    # Provide both versioned and legacy paths
    app.include_router(modules_router, prefix="/api/v1")
    app.include_router(modules_router, prefix="/api")
except Exception:
    logging.getLogger(__name__).exception("Failed to include Modules routes")

# Include Task/Webhook routes (ClickUp → Done ⇒ Next)
try:
    from .tasks import router as tasks_router
    # Provide both versioned and legacy paths
    app.include_router(tasks_router, prefix="/api/v1")
    app.include_router(tasks_router, prefix="/api")
except Exception:
    logging.getLogger(__name__).exception("Failed to include Task/Webhook routes")


# Lightweight auth middleware for module routes
@app.middleware("http")
async def auth_middleware_for_modules(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/module") or path.startswith("/api/v1/module"):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.lower().startswith("bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
        token = auth_header.split(" ", 1)[1]
        try:
            payload = decode_token(token)
            request.state.user_sub = payload.get("sub")
        except HTTPException:
            raise
        except Exception:
            logging.getLogger(__name__).exception("Auth middleware error")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return await call_next(request)

# Include Embedding routes
try:
    from . import embeddings
    app.include_router(embeddings.router, prefix="/api/v1")
except Exception:
    logging.getLogger(__name__).exception("Failed to include Embedding routes")

# Alias without /embeddings prefix for knowledge search
try:
    from .embeddings import SearchKnowledgeRequest, SearchKnowledgeResponse, search_knowledge as _search_knowledge

    @app.post("/api/v1/search_knowledge", response_model=SearchKnowledgeResponse)
    def search_knowledge_alias(payload: SearchKnowledgeRequest) -> SearchKnowledgeResponse:
        return _search_knowledge(payload)
except Exception:
    logging.getLogger(__name__).exception("Failed to expose /api/v1/search_knowledge alias")

# Serve static dashboard UI at /dashboard
try:
    app.mount("/dashboard", StaticFiles(directory="web/dashboard", html=True), name="dashboard")
except Exception:
    logging.getLogger(__name__).warning("Static dashboard directory not found; skipping mount")

# Serve AI generator UI at /ai (React/Tailwind single-page via CDN)
try:
    app.mount("/ai", StaticFiles(directory="web/ai-generator", html=True), name="ai_generator")
except Exception:
    logging.getLogger(__name__).warning("Static ai-generator directory not found; skipping mount")


@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/dashboard/")
