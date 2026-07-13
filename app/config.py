from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central app configuration, loaded from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "stackai-agent-runs"
    database_url: str = "sqlite+aiosqlite:///./agent_runs.db"

    # Fake runner behaviour
    runner_speed_factor: float = 20.0  # divides simulated latency for fast demos
    runner_failure_rate: float = 0.15
    runner_max_retries: int = 2

    # OpenTelemetry
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_exporter_otlp_headers: str = ""

    # API
    api_version: str = "v1"
    default_page_size: int = 20
    max_page_size: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()
