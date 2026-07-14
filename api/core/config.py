import json

from pydantic_settings import BaseSettings, SettingsConfigDict


# Test outline:
# @test: Test that environment variables of the same name as the members of this class are correctly loaded into the members of this class.
# @test: Test that any environment variables that are not set in the environment are correctly loaded into the members of this class with their default values.
# @test: Test that strings and number values, empty strings and URLs are all correctly loaded as exemplified by the default values
class Settings(BaseSettings):
    """Application settings."""

    PROJECT_NAME: str = "Workspaces API"

    # Allowed CORS origins. Accepts either a comma-separated list OR a JSON
    # array (the deployment stack historically supplies a JSON array), plus "*"
    # for all origins. Read via `cors_origins_list`, not this raw string.
    # Examples:
    #   CORS_ORIGINS="https://workspaces.example.com,https://leaderboard.example.com"
    #   CORS_ORIGINS='["https://workspaces.example.com"]'
    CORS_ORIGINS: str = ""

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

    # OSM token bridge: when enabled, a validated TDEI token is mirrored into the
    # OSM database's `oauth_access_tokens` table so osm-rails (doorkeeper) and
    # cgimap authenticate it via their standard OAuth2 path -- no custom JWT
    # handling needed in those services. The backend also auto-creates the
    # doorkeeper `oauth_applications` row (keyed by WS_OSM_OAUTH_CLIENT_UID and
    # owned by the dedicated system user below) that these tokens belong to, so
    # no manual OSM setup is required.
    WS_OSM_TOKEN_BRIDGE_ENABLED: bool = True

    # Stable client id (uid) for the auto-created doorkeeper application. Point
    # this at an existing application's uid to reuse it instead of creating one.
    WS_OSM_OAUTH_CLIENT_UID: str = "workspaces-backend"

    # Scopes granted to the application and mirrored tokens. Must cover the OSM
    # API operations the frontend performs; see `lib/oauth.rb` in the OSM website.
    WS_OSM_OAUTH_SCOPES: str = (
        "read_prefs write_prefs write_api write_changeset_comments "
        "read_gpx write_gpx write_notes"
    )

    # Dedicated OSM `users` row that owns the auto-created doorkeeper application
    # (satisfies oauth_applications.owner_id, a NOT-NULL FK to users). It never
    # signs in; these values just need to be stable and unique among users.
    WS_OSM_SYSTEM_USER_AUTH_UID: str = "workspaces-backend-system"
    WS_OSM_SYSTEM_USER_DISPLAY_NAME: str = "Workspaces Backend (system)"
    WS_OSM_SYSTEM_USER_EMAIL: str = "workspaces-backend-system@tdei.us"

    SENTRY_DSN: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        """Allowed CORS origins as a list.

        Tolerates both formats the deployment has used: a JSON array
        (``["https://a","https://b"]``) and a comma-separated string
        (``https://a,https://b``). ``"*"`` is passed through as-is.
        """
        raw = self.CORS_ORIGINS.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return [str(o).strip() for o in parsed if str(o).strip()]
        return [o.strip() for o in raw.split(",") if o.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # ignore unknown environment variables
    )


settings = Settings()
