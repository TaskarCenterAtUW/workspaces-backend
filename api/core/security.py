import json
import os
import requests_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from sqlalchemy import UUID

security = HTTPBearer()
session = requests_cache.CachedSession('pg_user_cache', expire_after=300)

class UserInfoPG:
    project_group_name: str
    project_group_id: str
    roles: list[str]

    def __init__(self, project_group_name: str, project_group_id: str, roles: list[str]):
        self.project_group_name = project_group_name
        self.project_group_id = project_group_id
        self.roles = roles

class UserInfo:
    scheme: str
    credentials: str
    user_uuid: UUID
    user_name: str
    projectGroups: list[UserInfoPG]

    def getProjectGroupIds(self, withRole = "any") -> list[str]:
        pgids = []
        for pg in self.projectGroups:
            if(withRole == "any" or pg.roles.__contains__(withRole)):
                pgids.append(pg.project_group_id)
        return pgids


async def validate_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> UserInfo:
    """Dependency to get current authenticated user."""

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    jwks_client = jwt.PyJWKClient("https://account-dev.tdei.us/realms/tdei/protocol/openid-connect/certs")
    signing_key = jwks_client.get_signing_key_from_jwt(credentials.credentials)

    jwtDecoded = jwt.decode_complete(
        credentials.credentials,
        key=signing_key.key,
        algorithms=["RS256"],
    )

    payload = jwtDecoded.get("payload", {})

    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    headers = {
        "Authorization": "Bearer " + credentials.credentials,
        "Content-Type": "application/json",
    }

    # TODO: fix if user has > 50 PGs
    authorizationUrl = (
        os.environ.get(
            "TM_TDEI_BACKEND_URL", "https://portal-api-dev.tdei.us/api/v1/"
        )
        + "/project-group-roles/"
        + user_id
        + "?page_no=1&page_size=50"
    )

    response = session.get(authorizationUrl, headers=headers)

    # token is not valid or server unavailable
    if response.status_code != 200:
        raise credentials_exception

    try:
        content = response.text
        j = json.loads(content)
    except json.JSONDecodeError:
        raise credentials_exception

    pgs = []
    for i in j:
        pgs.append(
            UserInfoPG(
                project_group_id=i["tdei_project_group_id"],
                project_group_name=i["project_group_name"],
                roles=i["roles"]
            )
        )

    r = UserInfo()
    r.scheme = credentials.scheme
    r.credentials = credentials.credentials
    r.user_uuid = payload.get("sub", "unknown")
    r.user_name = payload.get("preferred_username", "unknown")
    r.projectGroups = pgs

    return r
