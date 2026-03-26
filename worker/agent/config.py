from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    control_plane_url: str = "http://localhost:8000"
    worker_name: str = "worker-default"
    heartbeat_interval_seconds: int = 15
    metrics_port: int = 9100
    log_level: str = "INFO"
    otel_endpoint: str = ""
    enable_otel: bool = False


settings = Settings()
