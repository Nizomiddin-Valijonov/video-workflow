import json
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ═══ Ilova ═══
    APP_NAME: str = "VideoWorkflow Studio"
    APP_VERSION: str = "2.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    API_V1_PREFIX: str = "/api"

    # Docker'siz (desktop) rejim: SQLite + in-process navbat.
    # Docker Compose'da bu qiymat false bo'lib qoladi.
    LOCAL_MODE: bool = False

    # Desktop rejimda frontend statik yig'ilmasini (Next.js `out/`)
    # backend o'zi xizmat qiladi. Bo'sh bo'lsa — mount qilinmaydi.
    FRONTEND_DIST: str = ""

    # ═══ Xavfsizlik ═══
    SECRET_KEY: str = "change-me-in-production"
    API_KEY: str | None = None
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    ALGORITHM: str = "HS256"
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    TRUSTED_HOSTS: list[str] = ["localhost", "127.0.0.1", "backend", "*"]
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_BURST: int = 10

    # ═══ Ma'lumotlar bazasi ═══
    DATABASE_URL: str = "postgresql+asyncpg://app:app@localhost:5432/app"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    DB_ECHO: bool = False

    # ═══ Redis ═══
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_MAX_CONNECTIONS: int = 20

    # ═══ Saqlash ═══
    STORAGE_DIR: str = "/data/storage"
    MAX_UPLOAD_MB: int = 500
    MAX_VIDEO_DURATION_SEC: int = 600
    ALLOWED_VIDEO_FORMATS: list[str] = [".mp4", ".mov", ".webm", ".mkv"]
    ALLOWED_MIME_TYPES: list[str] = [
        "video/mp4", "video/quicktime", "video/webm", "video/x-matroska",
    ]

    # ═══ AI / Video tahlil ═══
    AI_PROVIDER: str = "openai"
    AI_MODEL: str = "qwen/qwen3.8-27b"
    AI_FALLBACK_MODEL: str = ""    # Asosiy model 402/429 bersa, shu modelga o'tiladi
    AI_BASE_URL: str = "https://api.groq.com/openai/v1"
    AI_TIMEOUT: int = 1800
    AI_MAX_TOKENS: int = 600
    AI_TEMPERATURE: float = 0.0
    OPENAI_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    
    # Video tahlil sozlamalari
    FRAME_RATE: int = 1            # 1 sekundda 1 kadr
    DIFF_THRESHOLD: float = 0.04
    MAX_FRAMES: int = 2000         # MAX_VIDEO_DURATION_SEC × FRAME_RATE uchun zaxira
    BETWEEN_FRAMES_DELAY: int = 2  # Har kadr orasida 2 sekund (429 uchun)
    USE_OCR: bool = True           # Tesseract OCR (sekin bo'lishi mumkin — o'chirsa ham bo'ladi)

    # ═══ Automation ═══
    WORKER_JOB_TIMEOUT: int = 7200
    WORKER_MAX_RETRIES: int = 3
    STEP_TIMEOUT_SEC: int = 60
    PREVENT_SLEEP: bool = True
    BROWSER_HEADLESS: bool = True
    BROWSER_SLOW_MO: int = 0
    BROWSER_TYPE: str = "chromium" # chromium, chrome, msedge, firefox, webkit
    BROWSER_CHANNEL: str = ""       # masalan: msedge, chrome (o'rnatilgan brauzer)
    BROWSER_EXECUTABLE: str = ""    # yoki to'liq chrome.exe yo'li
    VIEWPORT_WIDTH: int = 1280
    VIEWPORT_HEIGHT: int = 800

    # ═══ Monitoring ═══
    SENTRY_DSN: str = ""
    METRICS_ENABLED: bool = False

    @field_validator("CORS_ORIGINS", "TRUSTED_HOSTS", "ALLOWED_VIDEO_FORMATS", "ALLOWED_MIME_TYPES", mode="before")
    @classmethod
    def _parse_list(cls, value):
        if value in (None, ""):
            return []
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return []
            if value.startswith("["):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return [str(i).strip() for i in parsed if str(i).strip()]
                except json.JSONDecodeError:
                    pass
            return [i.strip() for i in value.split(",") if i.strip()]
        if isinstance(value, list):
            return [str(i).strip() for i in value if str(i).strip()]
        return [str(value)]

    @field_validator("ENVIRONMENT")
    @classmethod
    def _env(cls, v: str) -> str:
        return v.lower()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()