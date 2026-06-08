"""Pure helpers for the Paperclip reverse proxy: URL + header hygiene."""
from __future__ import annotations

from typing import Dict, Mapping

# Hop-by-hop headers must not be forwarded (RFC 7230 §6.1).
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
_DROP_REQUEST = _HOP_BY_HOP | {"host"}
# Let the response layer recompute framing/length from the streamed body.
_DROP_RESPONSE = _HOP_BY_HOP | {"content-length", "content-encoding"}


def build_upstream_url(base: str, subpath: str, query: str) -> str:
    base = base.rstrip("/")
    path = subpath.lstrip("/")
    url = f"{base}/{path}"
    if query:
        url = f"{url}?{query}"
    return url


def filter_request_headers(headers: Mapping[str, str]) -> Dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _DROP_REQUEST}


def filter_response_headers(headers: Mapping[str, str]) -> Dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _DROP_RESPONSE}
