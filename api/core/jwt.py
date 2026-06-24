import jwt

from api.core.config import settings

# Singleton JWKS client reused to take advantage of internal cert/key caching:
_jwks_client: jwt.PyJWKClient | None = None

# Test outline:
# @test: Test that this class validates JSON payloads properly against the JSON schema fetched
# @test: Test that malformed JSON or JSON that doesn't validate
# @test: Test that any failed network requests are handled gracefully and return a proper error (not just "Exception") or worse a false positive validation


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client

    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(
            f"{settings.TDEI_OIDC_URL.rstrip("/")}/realms/"
            f"{settings.TDEI_OIDC_REALM}/protocol/openid-connect/certs"
        )

    return _jwks_client


def validate_and_decode_token(token: str) -> dict:
    # TODO: use an async client like pyjwt-key-fetcher
    signing_key = _get_jwks_client().get_signing_key_from_jwt(token)

    decoded = jwt.decode_complete(
        token,
        key=signing_key.key,
        algorithms=["RS256"],
        # OIDC server does not currently differentiate tokens by audience
        options={"verify_aud": False},
    )

    return decoded.get("payload", {})
