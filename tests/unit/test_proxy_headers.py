"""Unit tests for the proxy's Accept-Encoding negotiation.

Mitigation for AB#4307 -- see `_UPSTREAM_ACCEPT_ENCODING` in api/main.py for why
the gateway is asked for deflate at all.
"""

import pytest

from api.main import _accepts_deflate


@pytest.mark.parametrize(
    "header",
    [
        "deflate",
        "DEFLATE",
        " deflate ",
        "gzip, deflate",
        "gzip, deflate, br",
        "*",
        "gzip;q=1.0, deflate;q=0.5",
        "deflate;q=0.001",
    ],
)
def test_accepted(header):
    assert _accepts_deflate(header) is True


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "identity",
        "gzip",
        "gzip, br",
        # Naming a coding is not accepting it: q=0 is an explicit refusal.
        "deflate;q=0",
        "gzip, deflate;q=0",
        "*;q=0",
        # A malformed q is treated as a refusal rather than guessed at.
        "deflate;q=banana",
    ],
)
def test_refused(header):
    assert _accepts_deflate(header) is False
