from typing import Dict, Literal, Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from .modules import list_modules, toggle_module, set_module
from .notifications import send_telegram_message
from .security import get_current_user


router = APIRouter()
logger = logging.getLogger("modules")


class ModuleToggleRequest(BaseModel):
    name: str
    action: Optional[Literal["on", "off", "toggle"]] = "toggle"


class ModuleResponse(BaseModel):
    name: str
    enabled: bool
    modules: Dict[str, bool]


@router.post("/module/toggle", response_model=ModuleResponse)
async def module_toggle(payload: ModuleToggleRequest, request: Request, user=Depends(get_current_user)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Module name required")

    if payload.action == "on":
        enabled = set_module(name, True)
    elif payload.action == "off":
        enabled = set_module(name, False)
    else:
        enabled = toggle_module(name)

    all_mods = list_modules()
    on_off = "ON" if enabled else "OFF"

    # Log to modules logger
    sub = getattr(request.state, "user_sub", None)
    logger.info("Module %s %s by=%s", name, on_off, sub or user.id)

    # Telegram notification
    try:
        await send_telegram_message(f"Module {name} {on_off}")
    except Exception:
        logging.getLogger(__name__).exception("Telegram send failed for module toggle")

    return ModuleResponse(name=name, enabled=enabled, modules=all_mods)

