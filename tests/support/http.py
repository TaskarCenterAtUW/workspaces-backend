"""HTTP test doubles for the OSM proxy.

The proxy streams upstream responses with ``response.aiter_raw()``. The
stock ``httpx.MockTransport`` eagerly buffers the body, so re-streaming it
raises ``StreamConsumed``. This transport returns a genuinely streamable
response and records the forwarded request for assertions.
"""

import httpx


class StreamingMockTransport(httpx.AsyncBaseTransport):
    """An httpx transport that returns streamable canned responses.

    ``handler(request) -> (status_code, headers_dict, body_bytes)``.
    The most recent request is available as ``.last_request``.
    """

    def __init__(self, handler):
        self._handler = handler
        self.last_request: httpx.Request | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Read the request body so the upstream call completes cleanly.
        await request.aread()
        self.last_request = request
        status_code, headers, body = self._handler(request)

        async def stream():
            yield body

        return httpx.Response(status_code, headers=headers, content=stream())


def osm_mock_client(handler=None, *, base_url: str = "http://osm-web"):
    """Build an ``AsyncClient`` standing in for the upstream OSM service.

    Returns ``(client, transport)`` so tests can inspect ``transport.last_request``.
    """

    def default_handler(_request):
        return 200, {"content-type": "text/xml"}, b"<osm version='0.6'/>"

    transport = StreamingMockTransport(handler or default_handler)
    client = httpx.AsyncClient(transport=transport, base_url=base_url)
    return client, transport
