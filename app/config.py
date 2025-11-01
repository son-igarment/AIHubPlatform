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
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    TELEGRAM_BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: Optional[str] = os.getenv("TELEGRAM_CHAT_ID")
    CLICKUP_WEBHOOK_SECRET: Optional[str] = os.getenv("CLICKUP_WEBHOOK_SECRET")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    REDIS_URL: Optional[str] = os.getenv("REDIS_URL")

    # Logging
    LOG_DIR: Path = Path(os.getenv("LOG_DIR", ROOT_DIR / "logs"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Automation scheduler
    AUTOMATION_ENABLED: bool = os.getenv("AUTOMATION_ENABLED", "true").lower() != "false"
    AUTOMATION_INTERVAL_HOURS: int = int(os.getenv("AUTOMATION_INTERVAL_HOURS", "6"))
    AUTOMATION_RUN_AT_STARTUP: bool = os.getenv("AUTOMATION_RUN_AT_STARTUP", "true").lower() == "true"
    # v3 simulation controls (optional)
    AUTOMATION_SIMULATE_CYCLES: int = int(os.getenv("AUTOMATION_SIMULATE_CYCLES", "0"))
    AUTOMATION_SIMULATE_DELAY_SEC: float = float(os.getenv("AUTOMATION_SIMULATE_DELAY_SEC", "0.2"))

    # Data pipeline targets
    AI_CRAWL_ENDPOINT: Optional[str] = os.getenv("AI_CRAWL_ENDPOINT")
    AI_CRAWL_METHOD: str = os.getenv("AI_CRAWL_METHOD", "GET").upper()
    AI_CRAWL_PAYLOAD: Optional[str] = os.getenv("AI_CRAWL_PAYLOAD")
    AI_UPDATE_ENDPOINT: Optional[str] = os.getenv("AI_UPDATE_ENDPOINT")
    AI_UPDATE_METHOD: str = os.getenv("AI_UPDATE_METHOD", "POST").upper()
    AI_UPDATE_PAYLOAD: Optional[str] = os.getenv("AI_UPDATE_PAYLOAD")
    AI_HTTP_TIMEOUT: int = int(os.getenv("AI_HTTP_TIMEOUT", "60"))
    AI_API_KEY: Optional[str] = os.getenv("AI_API_KEY")
    AI_EXTRA_HEADERS: Optional[str] = os.getenv("AI_EXTRA_HEADERS")

    # AI generation defaults
    AI_MODEL: str = os.getenv("AI_MODEL", "gpt-4o-mini")
    AI_TIMEOUT_MS: int = int(os.getenv("AI_TIMEOUT_MS", "1800"))
    AI_CACHE_TTL_SECONDS: int = int(os.getenv("AI_CACHE_TTL_SECONDS", "600"))
    AI_CACHE_MAX: int = int(os.getenv("AI_CACHE_MAX", "32"))

    # Embedding / knowledge base
    KNOWLEDGE_DB_PATH: Path = Path(os.getenv("KNOWLEDGE_DB_PATH", ROOT_DIR / "aihub_knowledge.db"))
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "384"))

    # Telegram notifications
    TELEGRAM_THREAD_ID: Optional[str] = os.getenv("TELEGRAM_THREAD_ID")
    TELEGRAM_PARSE_MODE: str = os.getenv("TELEGRAM_PARSE_MODE", "Markdown")
    TELEGRAM_DISABLE_NOTIFICATIONS: bool = os.getenv("TELEGRAM_DISABLE_NOTIFICATIONS", "false").lower() == "true"
    TELEGRAM_TIMEOUT: int = int(os.getenv("TELEGRAM_TIMEOUT", "15"))

    # Resilience / HTTP hardening
    HTTP_TIMEOUT_SEC: int = int(os.getenv("HTTP_TIMEOUT_SEC", "8"))
    HTTP_MAX_RETRIES: int = int(os.getenv("HTTP_MAX_RETRIES", "2"))
    HTTP_BACKOFF_BASE_MS: int = int(os.getenv("HTTP_BACKOFF_BASE_MS", "120"))
    HTTP_CIRCUIT_FAIL_THRESHOLD: int = int(os.getenv("HTTP_CIRCUIT_FAIL_THRESHOLD", "5"))
    HTTP_CIRCUIT_RESET_SEC: int = int(os.getenv("HTTP_CIRCUIT_RESET_SEC", "30"))

    # AI circuit breaker + retries
    AI_MAX_RETRIES: int = int(os.getenv("AI_MAX_RETRIES", "1"))
    AI_CIRCUIT_FAIL_THRESHOLD: int = int(os.getenv("AI_CIRCUIT_FAIL_THRESHOLD", "3"))
    AI_CIRCUIT_RESET_SEC: int = int(os.getenv("AI_CIRCUIT_RESET_SEC", "20"))

    # Metrics cache TTL
    METRICS_CACHE_TTL_SECONDS: int = int(os.getenv("METRICS_CACHE_TTL_SECONDS", "60"))

    @property
    def access_expires(self) -> timedelta:
        return timedelta(minutes=self.ACCESS_TOKEN_EXPIRE_MINUTES)

    @property
    def refresh_expires(self) -> timedelta:
        return timedelta(days=self.REFRESH_TOKEN_EXPIRE_DAYS)

    def validate_required(self) -> None:
        required_map = {
            "OPENAI_API_KEY": self.OPENAI_API_KEY,
            "TELEGRAM_BOT_TOKEN": self.TELEGRAM_BOT_TOKEN,
            "TELEGRAM_CHAT_ID": self.TELEGRAM_CHAT_ID,
            "CLICKUP_WEBHOOK_SECRET": self.CLICKUP_WEBHOOK_SECRET,
            "DATABASE_URL": self.DATABASE_URL,
        }
        missing = [key for key, value in required_map.items() if value is None or str(value).strip() == ""]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


settings = Settings()
settings.validate_required()
settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
