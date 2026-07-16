import secrets
import time
from enum import StrEnum
from uuid import UUID

import cachetools
import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.config import settings
from api.core.database import get_osm_session, get_task_session
from api.core.jwt import validate_and_decode_token
from api.core.logging import get_logger
from api.src.users.schemas import WorkspaceUserRoleType

# Test outline:
# @test: Test that the permissions structure here matches what is described in CLAUDE.md
# @test: Test that the methods on the UserInfo class return the correct values for a given set of project groups and workspace roles
# @test: Test that any failed network requests are handled gracefully
# @test: Test that the caching mechanism works correctly and evicts entries when roles change
# @test: Test that when WS_OSM_TOKEN_BRIDGE_ENABLED, a validated token is mirrored into oauth_access_tokens with the doorkeeper application + system-owner user + caller user auto-provisioned and expires_in from the JWT exp; and is a no-op when disabled
# @test: Test that re-presenting a token reactivates its OSM row (revoked_at cleared, expiry refreshed) and that a rotated (superseded) token is revoked, both gated on WS_OSM_TOKEN_BRIDGE_ENABLED

# Set up logger for this module
logger = get_logger(__name__)

# TTL cache keyed by a user's OIDC subject. Evict entries when roles change. We
# still validate the JWT signature and expiry on every request before reading a
# cached record.
_user_info_cache: cachetools.TTLCache[UUID, "UserInfo"] = cachetools.TTLCache(
    maxsize=1000, ttl=60 * 60
)  # type: ignore[assignment]  # cachetools ctor can't infer key/value types

# Shared HTTP client for TDEI backend calls. Initialized by main.py lifespan.
_tdei_client: httpx.AsyncClient | None = None


def init_tdei_client() -> None:
    global _tdei_client
    _tdei_client = httpx.AsyncClient(
        base_url=settings.TDEI_BACKEND_URL,
        timeout=httpx.Timeout(connect=10, read=30, write=30, pool=10),
    )


async def close_tdei_client() -> None:
    global _tdei_client
    if _tdei_client is not None:
        await _tdei_client.aclose()
        _tdei_client = None


class TdeiProjectGroupUser:
    """One member of a TDEI project group, as returned by
    ``GET /project-group/{pg_id}/users``.

    Field names are normalised here because the upstream JSON shape varies
    across deployments (`user_id` vs `id`, `username` vs `display_name`).
    """

    def __init__(
        self,
        *,
        auth_uid: str,
        email: str | None,
        display_name: str | None,
    ) -> None:
        self.auth_uid = auth_uid
        self.email = email
        self.display_name = display_name


async def fetch_project_group_users(
    project_group_id: str,
    bearer_token: str,
) -> list[TdeiProjectGroupUser]:
    """Page through ``GET /project-group/{pg_id}/users`` on TDEI.

    Returns every member of the project group. The endpoint is paginated
    server-side; we fetch all pages so the caller can look up an
    arbitrary user UUID without guessing page numbers. Raises 502 if
    TDEI is unreachable, 401 if the token is rejected.
    """
    if _tdei_client is None:  # pragma: no cover — lifespan should init this
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="TDEI client is not initialised",
        )

    headers = {"Authorization": f"Bearer {bearer_token}"}
    page_no = 1
    page_size = 200
    out: list[TdeiProjectGroupUser] = []
    while True:
        try:
            response = await _tdei_client.get(
                f"project-group/{project_group_id}/users",
                headers=headers,
                params={"page_no": page_no, "page_size": page_size},
            )
        except httpx.RequestError:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not reach TDEI backend to fetch project group users",
            ) from None

        if response.status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="TDEI rejected the bearer token",
            )
        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"TDEI returned {response.status_code} when listing "
                    f"users for project group {project_group_id}"
                ),
            )

        try:
            page = response.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="TDEI returned a non-JSON body",
            ) from None

        if not isinstance(page, list) or not page:
            break

        for row in page:
            uid = row.get("user_id")
            if not uid:
                continue
            out.append(
                TdeiProjectGroupUser(
                    auth_uid=str(uid),
                    email=row.get("email"),
                    display_name=(row.get("username")),
                )
            )

        if len(page) < page_size:
            break
        page_no += 1

    return out


def evict_user_from_cache(auth_uid: UUID) -> None:
    """
    Evict a user's cached UserInfo object so that their next request re-fetches
    permissions.

    Call this after modifying a user's roles in the OSM DB to ensure the change
    takes effect on their next request rather than after the cache TTL expires.
    """
    _user_info_cache.pop(auth_uid, None)


security = HTTPBearer()


# @test: Test that this matches what is described in CLAUDE.md and that the methods on the UserInfo class return the correct values for a given set of project groups and workspace roles
class TdeiProjectGroupRole(StrEnum):
    MEMBER = "member"
    POINT_OF_CONTACT = "poc"
    FLEX_GENERATOR = "flex_data_generator"
    OSW_GENERATOR = "osw_data_generator"
    PATHWAYS_GENERATOR = "pathways_data_generator"


class UserInfoPGMembership:
    project_group_name: str
    project_group_id: str
    tdeiRoles: list[TdeiProjectGroupRole]

    def __init__(
        self,
        project_group_name: str,
        project_group_id: str,
        tdeiRoles: list[TdeiProjectGroupRole],
    ):
        self.project_group_name = project_group_name
        self.project_group_id = project_group_id
        self.tdeiRoles = tdeiRoles


# @test: Test that the values populated in this class match the expected values from the JWT and TDEI API responses, and that the methods return the correct roles based on the user's project group memberships and workspace roles
# @test: Test that the osmWorkspaceRoles, accessibleWorkspaceIds, and projectGroups attributes are correctly populated based on the user's roles in the OSM DB and TDEI API responses
# @test: Test that the comments in this doc that describe what is supposed to be in the attributes are accurate and match the actual data being stored in those attributes and what is returned. The comments should be considered authoratative
class UserInfo:
    credentials: str
    user_uuid: UUID
    user_name: str
    token_jti: str  # JWT ID used to detect token rotation on cache hits

    # workspaceId, role from OSM DB
    osmWorkspaceRoles: dict[int, list[WorkspaceUserRoleType]]

    # from tasking manager DB: pgId -> workspaceIds
    accessibleWorkspaceIds: dict[str, list[int]]

    # from TDEI/KeyCloak
    projectGroups: list[UserInfoPGMembership]

    # PG ids that the user has X (or any) membership with
    def getProjectGroupIds(self, withRole="any") -> list[str]:
        pgids = []
        for pg in self.projectGroups:
            if withRole == "any" or pg.tdeiRoles.__contains__(withRole):
                pgids.append(pg.project_group_id)
        return pgids

    # we get admin/lead rights in two ways:
    # 1. from OSM DB explicitly
    # 2. from TDEI if user has "point_of_contact" role in any PG that owns the workspace
    def isWorkspaceLead(self, workspaceId: int) -> bool:
        if (
            workspaceId in self.osmWorkspaceRoles
            and WorkspaceUserRoleType.LEAD in self.osmWorkspaceRoles[workspaceId]
        ):
            return True

        for pg in self.projectGroups:
            if TdeiProjectGroupRole.POINT_OF_CONTACT in pg.tdeiRoles:
                if workspaceId in self.accessibleWorkspaceIds.get(
                    pg.project_group_id, []
                ):
                    return True

        return False

    # user has been granted validator rights in OSM DB for this workspace
    def isWorkspaceValidator(self, workspaceId: int) -> bool:
        if (
            workspaceId in self.osmWorkspaceRoles
            and WorkspaceUserRoleType.VALIDATOR in self.osmWorkspaceRoles[workspaceId]
        ):
            return True
        return False

    # user has has any association with a project group that owns the workspace
    def isWorkspaceContributor(self, workspaceId: int) -> bool:
        for pgid, wsids in self.accessibleWorkspaceIds.items():
            if workspaceId in wsids:
                return True
        return False

    def effective_role(self, workspaceId: int) -> WorkspaceUserRoleType:
        if self.isWorkspaceLead(workspaceId):
            return WorkspaceUserRoleType.LEAD
        if self.isWorkspaceValidator(workspaceId):
            return WorkspaceUserRoleType.VALIDATOR
        return WorkspaceUserRoleType.CONTRIBUTOR


# can't use the ORM here since the ORM uses us! (circular dependency)
def get_osm_db_session(
    session: AsyncSession = Depends(get_osm_session),
) -> AsyncSession:
    return session


def get_task_db_session(
    session: AsyncSession = Depends(get_task_session),
) -> AsyncSession:
    return session


# @test:


async def validate_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    osm_db_session: AsyncSession = Depends(get_osm_db_session),
    task_db_session: AsyncSession = Depends(get_task_db_session),
) -> UserInfo:
    """
    Dependency that gets the current authenticated user from the TDEI/KeyCloak
    access token and fetches permissions from TDEI APIs.

    We validate the JWT's signature and expiry on every request. The expensive
    TDEI API and DB lookups are cached for 1 hour and should be evicted when a
    user's role changes via evict_user_from_cache().
    """
    token = credentials.credentials

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = validate_and_decode_token(token)
    except Exception:
        raise credentials_exception

    user_id_str: str | None = payload.get("sub")
    if user_id_str is None:
        raise credentials_exception

    try:
        user_uuid = UUID(user_id_str)
    except ValueError:
        raise credentials_exception from None

    # Cache keyed by user UUID. If the token rotated (new "jti") since we
    # created the cache entry, evict it so we fetch fresh claims:
    #
    if user_uuid in _user_info_cache:
        cached = _user_info_cache[user_uuid]
        current_jti = payload.get("jti", "")
        if cached.token_jti == current_jti:
            logger.info("Token validation cache hit")
            return cached
        logger.info("Token validation cache miss: token rotated")
        # The old token is superseded; revoke its OSM row so it stops
        # authenticating against osm-rails/cgimap before its own exp.
        # TODO: this assumes a single active token per user. Truly concurrent
        # sessions (multiple valid jtis for one user) will flap -- each request
        # revokes the other session's OSM token and reactivates its own via the
        # DO UPDATE upsert. To support multi-session, track multiple active jtis
        # per user (cache + revoke set) instead of a single cached token.
        await _revoke_osm_token(osm_db_session, cached.credentials)
        del _user_info_cache[user_uuid]

    # Cache miss: fetch TDEI roles and DB data:
    user_info = await _validate_token_uncached(
        token, user_uuid, payload, osm_db_session, task_db_session
    )
    _user_info_cache[user_uuid] = user_info

    return user_info


async def _ensure_osm_user(
    session: AsyncSession,
    *,
    auth_uid: str,
    email: str | None,
    display_name: str,
) -> int | None:
    """Idempotently provision an OSM ``users`` row (auth_provider ``TDEI``) and
    return its id. ``email`` is synthesised when absent -- the column is UNIQUE
    and NOT NULL."""
    await session.execute(
        text(
            "INSERT INTO users (auth_uid, email, display_name, auth_provider, "
            "status, pass_crypt, data_public, email_valid, terms_seen, "
            "creation_time, terms_agreed, tou_agreed) "
            "VALUES (:auth_uid, :email, :name, 'TDEI', 'active', :pass_crypt, "
            "true, true, true, (now() at time zone 'utc'), "
            "(now() at time zone 'utc'), (now() at time zone 'utc')) "
            "ON CONFLICT (auth_uid) DO NOTHING"
        ),
        {
            "auth_uid": auth_uid,
            "email": email or f"{auth_uid}@tdei.invalid",
            "name": display_name,
            # OSM's User model validates pass_crypt length 8..255. TDEI manages
            # auth, so this is a throwaway that just satisfies that rule (a too-
            # short value makes the user invalid and breaks Rails operations
            # that re-validate it via `validates :author, :associated => true`,
            # e.g. posting a changeset comment).
            "pass_crypt": secrets.token_hex(16),
        },
    )
    row = (
        await session.execute(
            text(
                "SELECT id FROM users "
                "WHERE auth_provider = 'TDEI' AND auth_uid = :auth_uid"
            ),
            {"auth_uid": auth_uid},
        )
    ).first()
    return row[0] if row else None


async def _ensure_osm_oauth_application(session: AsyncSession) -> int | None:
    """Ensure the doorkeeper application the mirrored tokens belong to exists,
    creating it (owned by a dedicated system user) if needed. Idempotent by the
    configured client uid; returns the application id."""
    owner_id = await _ensure_osm_user(
        session,
        auth_uid=settings.WS_OSM_SYSTEM_USER_AUTH_UID,
        email=settings.WS_OSM_SYSTEM_USER_EMAIL,
        display_name=settings.WS_OSM_SYSTEM_USER_DISPLAY_NAME,
    )
    if owner_id is None:
        return None
    await session.execute(
        text(
            "INSERT INTO oauth_applications (owner_type, owner_id, name, uid, "
            "secret, redirect_uri, scopes, confidential, created_at, updated_at) "
            "VALUES ('User', :owner_id, :name, :uid, :secret, "
            "'urn:ietf:wg:oauth:2.0:oob', :scopes, true, "
            "(now() at time zone 'utc'), (now() at time zone 'utc')) "
            "ON CONFLICT (uid) DO NOTHING"
        ),
        {
            "owner_id": owner_id,
            "name": "Workspaces Backend",
            "uid": settings.WS_OSM_OAUTH_CLIENT_UID,
            # Never used (tokens are inserted directly rather than issued via the
            # OAuth flow), but the column is NOT NULL.
            "secret": secrets.token_hex(32),
            "scopes": settings.WS_OSM_OAUTH_SCOPES,
        },
    )
    row = (
        await session.execute(
            text("SELECT id FROM oauth_applications WHERE uid = :uid"),
            {"uid": settings.WS_OSM_OAUTH_CLIENT_UID},
        )
    ).first()
    return row[0] if row else None


async def _bridge_token_to_osm(
    session: AsyncSession,
    *,
    user_uuid: UUID,
    user_name: str,
    email: str | None,
    token: str,
    exp: int | None,
) -> None:
    """Mirror a validated TDEI token into the OSM database so osm-rails
    (doorkeeper) and cgimap authenticate it through their standard OAuth2 path,
    with no custom JWT handling required in those services.

    Auto-provisions everything it needs: the doorkeeper ``oauth_applications``
    row (owned by a dedicated system user), the caller's ``users`` row, and a
    plaintext ``oauth_access_tokens`` row -- no manual OSM setup. Doorkeeper
    stores tokens plaintext (``SecretStoring::Plain``), so the raw JWT matches on
    lookup for both osm-rails and cgimap; ``expires_in`` tracks the JWT's ``exp``.

    No-op unless ``WS_OSM_TOKEN_BRIDGE_ENABLED``. Best-effort: a failure here
    must not break token validation -- the ``/api/v1`` routes keep working; only
    the proxied OSM calls would 401 until the row exists.
    """
    if not settings.WS_OSM_TOKEN_BRIDGE_ENABLED:
        return

    try:
        app_id = await _ensure_osm_oauth_application(session)
        if app_id is None:
            return
        user_id = await _ensure_osm_user(
            session, auth_uid=str(user_uuid), email=email, display_name=user_name
        )
        if user_id is None:
            return

        expires_in = max(0, exp - int(time.time())) if exp else None
        await session.execute(
            text(
                "INSERT INTO oauth_access_tokens (application_id, "
                "resource_owner_id, token, scopes, created_at, expires_in) "
                "VALUES (:app_id, :user_id, :token, :scopes, "
                "(now() at time zone 'utc'), :expires_in) "
                # Re-presenting a token reactivates it: refresh the expiry to
                # track this validation and clear any prior revocation, so a
                # token revoked on rotation heals if it is used again.
                "ON CONFLICT (token) DO UPDATE SET "
                "resource_owner_id = EXCLUDED.resource_owner_id, "
                "scopes = EXCLUDED.scopes, "
                "created_at = EXCLUDED.created_at, "
                "expires_in = EXCLUDED.expires_in, "
                "revoked_at = NULL"
            ),
            {
                "app_id": app_id,
                "user_id": user_id,
                "token": token,
                "scopes": settings.WS_OSM_OAUTH_SCOPES,
                "expires_in": expires_in,
            },
        )
        await session.commit()
    except Exception as e:
        # Never fail auth on a bridge error; the OSM row just won't exist yet.
        logger.warning(
            "Failed to bridge TDEI token into OSM oauth_access_tokens: %s", e
        )
        await session.rollback()


async def _revoke_osm_token(session: AsyncSession, token: str) -> None:
    """Revoke a superseded token's OSM ``oauth_access_tokens`` row (best-effort).

    Called when a user's token rotates (new ``jti``) so the previous JWT stops
    authenticating against osm-rails/cgimap before its own ``exp`` rather than
    lingering until it expires. No-op unless the bridge is enabled.

    Note: OSM/cgimap are reachable only *through* this proxy, and every request
    first passes ``validate_token`` -- so a TDEI-revoked token is already
    rejected upstream. This revocation is defense-in-depth for the superseded
    token and keeps the OSM token store tidy.
    """
    if not settings.WS_OSM_TOKEN_BRIDGE_ENABLED:
        return
    try:
        await session.execute(
            text(
                "UPDATE oauth_access_tokens "
                "SET revoked_at = (now() at time zone 'utc') "
                "WHERE token = :token AND revoked_at IS NULL"
            ),
            {"token": token},
        )
        await session.commit()
    except Exception as e:
        logger.warning("Failed to revoke superseded OSM token: %s", e)
        await session.rollback()


async def _validate_token_uncached(
    token: str,
    user_uuid: UUID,
    payload: dict,
    osm_db_session: AsyncSession,
    task_db_session: AsyncSession,
) -> UserInfo:
    logger.info("Starting validation of token")

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    }

    r = UserInfo()
    r.user_uuid = user_uuid
    r.credentials = token
    r.token_jti = payload.get("jti", "")
    r.user_name = payload.get("preferred_username", "unknown")

    # get user's project groups and roles from TDEI
    pgs = []

    client = _tdei_client
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TDEI client is not initialized",
        )

    try:
        response = await client.get(
            f"project-group-roles/{user_uuid}",
            headers=headers,
            params={"page_no": 1, "page_size": 1000},
        )
    except httpx.RequestError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach TDEI backend",
        ) from None

    # token is not valid or server unavailable
    if response.status_code != 200:
        raise credentials_exception

    try:
        pg_data = response.json()
    except Exception:
        raise credentials_exception

    for i in pg_data:
        pgs.append(
            UserInfoPGMembership(
                project_group_id=i["tdei_project_group_id"],
                project_group_name=i["project_group_name"],
                tdeiRoles=i["roles"],
            )
        )

    r.projectGroups = pgs

    # workspaces within our set of PGs from tasking manager DB
    pgids = [pg.project_group_id for pg in pgs]
    result = await task_db_session.execute(
        text(
            'SELECT "tdeiProjectGroupId", id FROM workspaces WHERE "tdeiProjectGroupId" = ANY(:pgids)'
        ),
        {"pgids": pgids},
    )
    accessibleWorkspaces = list(result.mappings().all())
    r.accessibleWorkspaceIds = {}
    for i in accessibleWorkspaces:
        pgid = str(i["tdeiProjectGroupId"])  # SQLAlchemy outputs UUID
        wsid = i["id"]
        if pgid not in r.accessibleWorkspaceIds:
            r.accessibleWorkspaceIds[pgid] = []
        r.accessibleWorkspaceIds[pgid].append(wsid)

    # workspace roles from OSM DB
    result = await osm_db_session.execute(
        text("SELECT workspace_id, role FROM user_workspace_roles \
                                               WHERE user_auth_uid = :auth_uid"),
        {"auth_uid": str(r.user_uuid)},
    )
    workspaceRoles = list(result.mappings().all())

    osmRoles = {}
    for i in workspaceRoles:
        if i["workspace_id"] not in osmRoles:
            osmRoles[i["workspace_id"]] = []
        osmRoles[i["workspace_id"]].append(i["role"])
    r.osmWorkspaceRoles = osmRoles

    # Mirror this validated token into the OSM DB so proxied OSM/cgimap calls
    # authenticate via the standard OAuth2 path. No-op unless configured.
    await _bridge_token_to_osm(
        osm_db_session,
        user_uuid=user_uuid,
        user_name=r.user_name,
        email=payload.get("email"),
        token=token,
        exp=payload.get("exp"),
    )

    logger.info("Finished validation of token")

    return r
