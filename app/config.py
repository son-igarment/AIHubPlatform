import os
from pathlib import Path
from datetime import timedelta
from typing import Optional
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


class Settings:
    APP_NAME: str = os.getenv("APP_NAME", "AIHub Auth API")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

    # Logging
    LOG_DIR: Path = Path(os.getenv("LOG_DIR", ROOT_DIR / "logs"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Automation
    AUTOMATION_ENABLED: bool = os.getenv("AUTOMATION_ENABLED", "true").lower() != "false"
    AUTOMATION_INTERVAL_HOURS: int = int(os.getenv("AUTOMATION_INTERVAL_HOURS", "6"))
    AUTOMATION_RUN_AT_STARTUP: bool = os.getenv("AUTOMATION_RUN_AT_STARTUP", "true").lower() == "true"

    # Data pipeline
    AI_CRAWL_ENDPOINT: Optional[str] = os.getenv("AI_CRAWL_ENDPOINT")
    AI_CRAWL_METHOD: str = os.getenv("AI_CRAWL_METHOD", "GET").upper()
    AI_CRAWL_PAYLOAD: Optional[str] = os.getenv("AI_CRAWL_PAYLOAD")
    AI_UPDATE_ENDPOINT: Optional[str] = os.getenv("AI_UPDATE_ENDPOINT")
    AI_UPDATE_METHOD: str = os.getenv("AI_UPDATE_METHOD", "POST").upper()
    AI_UPDATE_PAYLOAD: Optional[str] = os.getenv("AI_UPDATE_PAYLOAD")
    AI_API_KEY: Optional[str] = os.getenv("AI_API_KEY")
    AI_EXTRA_HEADERS: Optional[str] = os.getenv("AI_EXTRA_HEADERS")
    AI_HTTP_TIMEOUT: int = int(os.getenv("AI_HTTP_TIMEOUT", "60"))

    # Telegram notifications
    TELEGRAM_BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: Optional[str] = os.getenv("TELEGRAM_CHAT_ID")
    TELEGRAM_THREAD_ID: Optional[str] = os.getenv("TELEGRAM_THREAD_ID")
    TELEGRAM_PARSE_MODE: str = os.getenv("TELEGRAM_PARSE_MODE", "Markdown")
    TELEGRAM_DISABLE_NOTIFICATIONS: bool = os.getenv("TELEGRAM_DISABLE_NOTIFICATIONS", "false").lower() == "true"
    TELEGRAM_TIMEOUT: int = int(os.getenv("TELEGRAM_TIMEOUT", "15"))

    @property
    def access_expires(self) -> timedelta:
        return timedelta(minutes=self.ACCESS_TOKEN_EXPIRE_MINUTES)

    @property
    def refresh_expires(self) -> timedelta:
        return timedelta(days=self.REFRESH_TOKEN_EXPIRE_DAYS)


settings = Settings()
settings.LOG_DIR.mkdir(parents=True, exist_ok=True)

