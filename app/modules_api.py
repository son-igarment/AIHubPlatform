import logging
import time
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from .modules import list_modules, toggle_module, set_module
from .notifications import send_telegram_message
from .security import get_current_user
from .storage import record_module_toggle


router = APIRouter()
logger = logging.getLogger("modules")


class ModuleToggleRequest(BaseModel):
    module: str
    enabled: Optional[bool] = None


class ModuleToggleResponse(BaseModel):
    ok: bool
    module: str
    enabled: bool
    ts: int
    modules: Dict[str, bool]


@router.post("/module/toggle", response_model=ModuleToggleResponse)
async def module_toggle(payload: ModuleToggleRequest, request: Request, user=Depends(get_current_user)):
    name = (payload.module or "").strip().lower()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Module name required")

    if payload.enabled is None:
        enabled = toggle_module(name)
    else:
        enabled = set_module(name, bool(payload.enabled))

    all_mods = list_modules()
    on_off = "ON" if enabled else "OFF"

    # Log to modules logger
    sub = getattr(request.state, "user_sub", None)
    actor = sub or user.id
    logger.info("Module %s toggled to %s by=%s", name, on_off, actor)

    epoch_ts = int(time.time())
    try:
        record_module_toggle(name, enabled, actor, epoch_ts)
    except Exception:  # pragma: no cover - persistence error
        logging.getLogger(__name__).exception("Failed to record module toggle event")

    # Telegram notification
    try:
        await send_telegram_message(f"⚙️ Module `{name}` {on_off} by `{actor}`")
    except Exception:
        logging.getLogger(__name__).exception("Telegram send failed for module toggle")

    return ModuleToggleResponse(ok=True, module=name, enabled=enabled, ts=epoch_ts, modules=all_mods)

