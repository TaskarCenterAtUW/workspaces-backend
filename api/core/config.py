from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    PROJECT_NAME: str = "Workspaces API"
    DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost:5432/tasking_manager"
    DEBUG: bool = False

    WS_LONGFORM_SCHEMA_URL: str = (
        "https://raw.githubusercontent.com/TaskarCenterAtUW/asr-quests/refs/heads/main/schema/schema.json"
    )
    WS_OSM_HOST: str = "http://osm-rails:3000"

    SENTRY_DSN: str = (
        "https://ee0b098ee77451fb4a3f01c77eb2546e@o4510431738200064.ingest.us.sentry.io/4510630433980416"
    )

    # JWT Settings
    JWT_SECRET: str = "your-secret-key"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION: int = 24 * 60  # 1d

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
