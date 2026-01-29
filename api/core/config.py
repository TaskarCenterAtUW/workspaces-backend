from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    """Application settings."""

    PROJECT_NAME: str = "Workspaces API"

    TASK_DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost:5432/tasking_manager"
    OSM_DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost:5432/tasking_manager"

    DEBUG: bool = False

    # used for validation
    WS_LONGFORM_SCHEMA_URL: str = (
        "https://raw.githubusercontent.com/TaskarCenterAtUW/asr-quests/refs/heads/main/schema/schema.json"
    )

    # proxy destination--"osm-rails" is a virtual docker network endpoint
    WS_OSM_HOST: str = "http://osm-rails:3000"
    #WS_OSM_HOST: str = "https://osm.workspaces-dev.sidewalks.washington.edu"

    SENTRY_DSN: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

settings = Settings()
