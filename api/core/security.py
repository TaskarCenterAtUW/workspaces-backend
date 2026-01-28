import json
import os
from urllib.request import Request
import starlette.requests
import requests_cache

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import UUID

security = HTTPBearer()
session = requests_cache.CachedSession('pg_user_cache', expire_after=300)
class UserInfo:
    scheme: str
    credentials: str
    user_uuid: UUID
    user_name: str
    projectGroups: list[str]


async def validate_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> UserInfo:
    """Dependency to get current authenticated user."""

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # FIXME: verify signature of JWT token. FIXME FIX ME FIX ME FIX ME, CRITICAL SECURITY BUG
        payload = jwt.get_unverified_claims(credentials.credentials)
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise credentials_exception

    except JWTError:
        raise credentials_exception

    async with httpx.AsyncClient() as client:
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
            pgs.append(i["tdei_project_group_id"])

    r = UserInfo()
    r.scheme = credentials.scheme
    r.credentials = credentials.credentials
    r.user_uuid = payload.get("sub", "unknown")
    r.user_name = payload.get("preferred_username", "unknown")
    r.projectGroups = pgs

    return r

async def validate_workspace_role_for_call(
    current_user: UserInfo,
    request: starlette.requests.Request,
    workspace_id: int,
) -> bool:

    method = request.method
    path_params = request.path_params

    return True  # FIXME: implement actual role validation logic here
