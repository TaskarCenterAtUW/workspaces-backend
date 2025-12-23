import json
import os
from enum import StrEnum
import requests
from uuid import UUID

from api.core.logging import get_logger
import jwt
import cachetools
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.database import get_osm_session, get_task_session
from api.src.workspaces.schemas import WorkspaceUserRoleType

# Set up logger for this module
logger = get_logger(__name__)

# TTL cache for token validation (1 hour TTL, max 1000 entries)
_token_cache: cachetools.TTLCache[str, "UserInfo"] = cachetools.TTLCache(
    maxsize=1000, ttl=60 * 60
)

security = HTTPBearer()

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


class UserInfo:
    credentials: str
    user_uuid: UUID
    user_name: str

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
                if workspaceId in self.accessibleWorkspaceIds[pg.project_group_id]:
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


# can't use the ORM here since the ORM uses us! (circular dependency)
def get_osm_db_session(
    session: AsyncSession = Depends(get_osm_session),
) -> AsyncSession:
    return session


def get_task_db_session(
    session: AsyncSession = Depends(get_task_session),
) -> AsyncSession:
    return session

async def validate_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    osm_db_session: AsyncSession = Depends(get_osm_db_session),
    task_db_session: AsyncSession = Depends(get_task_db_session),
) -> UserInfo:
    """Dependency to get current authenticated user from TDEI/KeyCloak token and APIs.

    Results are cached by token for 1 hour to avoid repeated validation calls.
    """
    token = credentials.credentials

    # Check cache first
    if token in _token_cache:
        logger.info("Token validation cache hit")
        return _token_cache[token]

    # Cache miss - perform full validation
    user_info = await _validate_token_uncached(token, osm_db_session, task_db_session)
    _token_cache[token] = user_info
    return user_info


async def _validate_token_uncached(
    token: str,
    osm_db_session: AsyncSession,
    task_db_session: AsyncSession,
) -> UserInfo:
    logger.info("Starting validation of token")

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    jwks_client = jwt.PyJWKClient(
        "https://account-dev.tdei.us/realms/tdei/protocol/openid-connect/certs"
    )

    signing_key = jwks_client.get_signing_key_from_jwt(token)

    jwtDecoded = jwt.decode_complete(
        token,
        key=signing_key.key,
        algorithms=["RS256"],
    )
    payload = jwtDecoded.get("payload", {})

    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    }

    # get user's project groups and roles from TDEI
    # TODO: fix if user has > 50 PGs
    authorizationUrl = (
        os.environ.get("TM_TDEI_BACKEND_URL", "https://portal-api-dev.tdei.us/api/v1/")
        + "/project-group-roles/"
        + user_id
        + "?page_no=1&page_size=50"
    )

    response = requests.get(authorizationUrl, headers=headers)

    # token is not valid or server unavailable
    if response.status_code != 200:
        raise credentials_exception

    try:
        content = response.text
        j = json.loads(content)
    except json.JSONDecodeError:
        raise credentials_exception

    r = UserInfo()
    r.credentials = token
    r.user_uuid = UUID(payload.get("sub", "unknown"))
    r.user_name = payload.get("preferred_username", "unknown")

    # project groups and roles from TDEI KeyCloak
    pgs = []
    for i in j:
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
        pgid = i["tdeiProjectGroupId"]
        wsid = i["id"]
        if pgid not in r.accessibleWorkspaceIds:
            r.accessibleWorkspaceIds[pgid] = []
        r.accessibleWorkspaceIds[pgid].append(wsid)

    # workspace roles from OSM DB
    result = await osm_db_session.execute(
        text(
            "SELECT workspace_id, role FROM user_workspace_roles \
                                               WHERE user_auth_uid = :auth_uid"
        ),
        {"auth_uid": r.user_uuid},
    )
    workspaceRoles = list(result.mappings().all())

    osmRoles = {}
    for i in workspaceRoles:
        if i["workspace_id"] not in osmRoles:
            osmRoles[i["workspace_id"]] = []
        osmRoles[i["workspace_id"]].append(i["role"])
    r.osmWorkspaceRoles = osmRoles

    logger.info("Finished validation of token")

    return r
