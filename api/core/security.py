import json
import os

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

security = HTTPBearer()
class UserInfo:
    scheme: str
    credentials: str
    projectGroups: list[str]

async def validate_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> UserInfo:
    """Dependency to get current authenticated user."""

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # FIXME: verify signature of JWT token
        payload = jwt.get_unverified_claims(credentials.credentials)
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise credentials_exception

    except JWTError:
        raise credentials_exception

    async with httpx.AsyncClient() as client:
            headers = {
                'Authorization': 'Bearer ' + credentials.credentials,
                'Content-Type': 'application/json',
            }

            authorizationUrl = os.environ.get("TM_TDEI_BACKEND_URL", "https://portal-api-dev.tdei.us/api/v1/") + "/project-group-roles/" + user_id + "?page_no=1&page_size=50"
            response = await client.get(authorizationUrl, headers=headers)

            # token is not valid or server unavailable
            if response.status_code != 200:
                raise credentials_exception

            try:
                content = response.read()
                j = json.loads(content)
            except json.JSONDecodeError:
                raise credentials_exception

            pgs = []
            for i in j: 
                pgs.append(i["tdei_project_group_id"])

    r = UserInfo()
    r.scheme = credentials.scheme
    r.credentials = credentials.credentials
    r.projectGroups = pgs

    return r




