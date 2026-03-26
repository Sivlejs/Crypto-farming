from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://vgpu:vgpu@localhost:5432/vgpu"
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    worker_token_expire_hours: int = 24 * 30
    otel_endpoint: str = "http://localhost:4317"
    enable_otel: bool = False


settings = Settings()
