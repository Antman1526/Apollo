from services.paperclip.proxy import (
    build_upstream_url,
    filter_request_headers,
    filter_response_headers,
)


def test_build_upstream_url_joins_subpath_and_query():
    url = build_upstream_url("http://paperclip:3100", "assets/app.js", "v=2")
    assert url == "http://paperclip:3100/assets/app.js?v=2"


def test_build_upstream_url_root():
    assert build_upstream_url("http://paperclip:3100", "", "") == "http://paperclip:3100/"


def test_build_upstream_url_strips_leading_slash_on_subpath():
    assert build_upstream_url("http://paperclip:3100", "/api/x", "") == "http://paperclip:3100/api/x"


def test_filter_request_headers_drops_hop_by_hop_and_host():
    src = {"Host": "apollo", "Connection": "keep-alive", "Cookie": "a=1", "X-Real": "y"}
    out = filter_request_headers(src)
    assert "host" not in {k.lower() for k in out}
    assert "connection" not in {k.lower() for k in out}
    assert out["Cookie"] == "a=1"
    assert out["X-Real"] == "y"


def test_filter_response_headers_drops_hop_by_hop_and_encoding():
    src = {"Transfer-Encoding": "chunked", "Content-Length": "10", "Content-Type": "text/html"}
    out = filter_response_headers(src)
    keys = {k.lower() for k in out}
    assert "transfer-encoding" not in keys
    assert "content-length" not in keys  # re-derived by the response layer
    assert out["Content-Type"] == "text/html"
