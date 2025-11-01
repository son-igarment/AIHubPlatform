import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .config import ROOT_DIR
from .models import Role


logger = logging.getLogger("demo_data")


@dataclass(frozen=True)
class DemoUserRecord:
    email: str
    full_name: str
    role: Role
    password: str
    team: Optional[str] = None
    region: Optional[str] = None


_DATA_FILE = ROOT_DIR / "data" / "demo_users.json"
_DEFAULT_PASSWORD = "Demo@123"
_FALLBACK_USERS: Tuple[DemoUserRecord, ...] = (
    DemoUserRecord(
        email="admin@example.com",
        full_name="Admin User",
        role="Admin",
        password="Demo@123",
    ),
    DemoUserRecord(
        email="dev@example.com",
        full_name="Dev User",
        role="Dev",
        password="Demo@123",
    ),
)


def _normalize_role(raw_role: str) -> Role:
    role = (raw_role or "").strip().lower()
    if role == "admin":
        return "Admin"
    return "Dev"


def _iter_user_records(payload: Dict[str, object], default_password: str) -> Iterable[DemoUserRecord]:
    users = payload.get("users")
    if not isinstance(users, list):
        return []
    normalized: List[DemoUserRecord] = []
    for entry in users:
        if not isinstance(entry, dict):
            continue
        email_raw = str(entry.get("email", "")).strip().lower()
        full_name = str(entry.get("full_name", "")).strip()
        if not email_raw or not full_name:
            continue
        role = _normalize_role(str(entry.get("role", "")))
        password = str(entry.get("password") or default_password or _DEFAULT_PASSWORD)
        team = entry.get("team")
        region = entry.get("region")
        normalized.append(
            DemoUserRecord(
                email=email_raw,
                full_name=full_name,
                role=role,
                password=password,
                team=str(team) if isinstance(team, str) else None,
                region=str(region) if isinstance(region, str) else None,
            )
        )
    return normalized


def load_demo_users() -> Tuple[List[DemoUserRecord], str]:
    """
    Load demo users from disk; fallback to baked defaults if file missing or invalid.
    Returns tuple of (records, default_password).
    """
    if _DATA_FILE.exists():
        try:
            raw = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
            default_password = str(raw.get("default_password") or _DEFAULT_PASSWORD)
            records = list(_iter_user_records(raw, default_password))
            if records:
                logger.debug("Loaded %s demo users from %s", len(records), _DATA_FILE)
                return records, default_password
            logger.warning("Demo user file did not yield records; falling back to defaults.")
        except json.JSONDecodeError:
            logger.exception("Failed to parse demo user file as JSON: %s", _DATA_FILE)
        except Exception:
            logger.exception("Unexpected error while loading demo users.")
    logger.info("Using baked-in demo users (count=%s).", len(_FALLBACK_USERS))
    return list(_FALLBACK_USERS), _DEFAULT_PASSWORD
