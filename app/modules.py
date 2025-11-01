import json
import logging
from pathlib import Path
from typing import Dict, List

from .config import ROOT_DIR


logger = logging.getLogger("modules")

MODULES_FILE = ROOT_DIR / "config" / "modules.json"


DEFAULT_MODULES: Dict[str, bool] = {
    "auth": True,
    "generator": True,
    "scheduler": True,
    "crawl": True,
    "analytics": True,
}


def _load_raw() -> Dict[str, bool]:
    if not MODULES_FILE.exists():
        return {}
    try:
        data = json.loads(MODULES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # Normalize keys to lowercase str and values to bool
            return {str(k).strip().lower(): bool(v) for k, v in data.items() if str(k).strip()}
    except Exception:  # pylint: disable=broad-except
        logger.exception("Failed to read modules.json; recreating with defaults")
    return {}


def _save_raw(data: Dict[str, bool]) -> None:
    MODULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODULES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def ensure_modules_file() -> None:
    existing = _load_raw()
    changed = False
    for k, v in DEFAULT_MODULES.items():
        if k not in existing:
            existing[k] = v
            changed = True
    if changed or not MODULES_FILE.exists():
        _save_raw(existing)


def list_modules() -> Dict[str, bool]:
    ensure_modules_file()
    return _load_raw()


def set_module(name: str, enabled: bool) -> bool:
    name = name.strip().lower()
    if not name:
        raise ValueError("Module name required")
    data = list_modules()
    if name not in data:
        # If a new module name is provided, default add it disabled unless explicitly enabled
        data[name] = bool(enabled)
    else:
        data[name] = bool(enabled)
    _save_raw(data)
    return data[name]


def toggle_module(name: str) -> bool:
    name = name.strip().lower()
    data = list_modules()
    if name not in data:
        # Add new module on first toggle, default to True after toggle
        data[name] = True
    else:
        data[name] = not data[name]
    _save_raw(data)
    return data[name]

