"""Tests for api/core/jwt.py token validation.

The @test comments are boilerplate; the module's real job is to validate a
JWT's RS256 signature against the JWKS and decode its payload. We cover:
- a properly-signed token validates and its payload is returned
- a malformed token raises (rather than returning a payload)
- a token signed by the wrong key raises (no false-positive validation)
- JWKS/network failures propagate as a typed error (not a bare Exception)
"""

from types import SimpleNamespace

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import PyJWKClientError, PyJWTError

import api.core.jwt as jwtmod


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _fake_jwks(monkeypatch, *, public_key=None, exc=None):
    class FakeJWKSClient:
        def get_signing_key_from_jwt(self, token):
            if exc is not None:
                raise exc
            return SimpleNamespace(key=public_key)

    monkeypatch.setattr(jwtmod, "_get_jwks_client", lambda: FakeJWKSClient())


def test_valid_token_decodes_payload(monkeypatch, rsa_key):
    token = pyjwt.encode(
        {"sub": "user-abc", "jti": "j1", "preferred_username": "alice"},
        rsa_key,
        algorithm="RS256",
    )
    _fake_jwks(monkeypatch, public_key=rsa_key.public_key())

    payload = jwtmod.validate_and_decode_token(token)

    assert payload["sub"] == "user-abc"
    assert payload["preferred_username"] == "alice"


def test_malformed_token_raises(monkeypatch, rsa_key):
    _fake_jwks(monkeypatch, public_key=rsa_key.public_key())

    with pytest.raises(PyJWTError):
        jwtmod.validate_and_decode_token("not-a-real-jwt")


def test_token_signed_with_wrong_key_raises(monkeypatch, rsa_key):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = pyjwt.encode({"sub": "user-abc"}, other_key, algorithm="RS256")
    # JWKS returns the *wrong* public key -> signature check must fail.
    _fake_jwks(monkeypatch, public_key=rsa_key.public_key())

    with pytest.raises(PyJWTError):
        jwtmod.validate_and_decode_token(token)


def test_jwks_network_error_propagates_typed(monkeypatch):
    _fake_jwks(monkeypatch, exc=PyJWKClientError("could not fetch JWKS"))

    with pytest.raises(PyJWKClientError):
        jwtmod.validate_and_decode_token("a.b.c")
