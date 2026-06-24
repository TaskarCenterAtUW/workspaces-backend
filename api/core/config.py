from pydantic_settings import BaseSettings, SettingsConfigDict

# Test outline:
# @test: Test that environment variables of the same name as the members of this class are correctly loaded into the members of this class.
# @test: Test that any environment variables that are not set in the environment are correctly loaded into the members of this class with their default values.
# @test: Test that strings and number values, empty strings and URLs are all correctly loaded as exemplified by the default values
class Settings(BaseSettings):
    """Application settings."""

    PROJECT_NAME: str = "Workspaces API"

    # JSON array of allowed CORS origins. For example:
    #
    #   ["https://workspaces.example.com", "https://leaderboard.example.com"]
    #
    CORS_ORIGINS: list[str] = []

    TASK_DATABASE_URL: str = (
        "postgresql+asyncpg://user:pass@localhost:5432/tasking_manager"
    )
    OSM_DATABASE_URL: str = (
        "postgresql+asyncpg://user:pass@localhost:5432/tasking_manager"
    )

    TDEI_BACKEND_URL: str = "https://portal-api-dev.tdei.us/api/v1/"
    TDEI_OIDC_URL: str = "https://account-dev.tdei.us/"
    TDEI_OIDC_REALM: str = "tdei"

    DEBUG: bool = False

    # used for validation
    LONGFORM_SCHEMA_URL: str = (
        "https://raw.githubusercontent.com/TaskarCenterAtUW/asr-quests/refs/heads/main/schema/schema.json"
    )
    IMAGERY_SCHEMA_URL: str = (
        "https://raw.githubusercontent.com/TaskarCenterAtUW/asr-imagery-list/refs/heads/main/schema/schema.json"
    )

    # proxy destination--"osm-web" is a virtual docker network endpoint
    WS_OSM_HOST: str = "http://osm-web"

    SENTRY_DSN: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
