from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    database_password: str = ""
    is_mocking: bool = False
    web_origins: str = "http://localhost:8081,http://127.0.0.1:8081"
    cors_origin_regex: str | None = (
        r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+|100\.(6[4-9]|[7-9]\d|1[0-1]\d|12[0-7])\.\d+\.\d+)(:\d+)?$"
    )
    supabase_url: str = ""
    supabase_jwt_secret: str | None = None
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_timeout_seconds: float = 75.0
    gemini_scan_model: str = "gemini-3.1-pro-preview"
    gemini_scan_timeout_seconds: float = 90.0
    scan_storage_dir: str = "var/scans"
    scan_job_ttl_minutes: int = 30
    scan_stuck_timeout_seconds: int = 300
    scan_worker_concurrency: int = 2

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
