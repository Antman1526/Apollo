# routes/compare_helpers.py
"""Shared endpoint-resolution helpers for ad-hoc, client-picked models.

Compare and Council both let the caller name a raw endpoint URL + model
(picked from the model list) rather than a configured "role" like
reviewer/utility — resolve_endpoint() doesn't apply there. This is the one
place that turns such a URL into a dispatchable (chat_url, headers) pair,
looking up whatever API key is on file for that endpoint's base URL.
"""

from typing import Dict, Tuple

from core.database import ModelEndpoint, SessionLocal
from src.endpoint_resolver import build_chat_url, build_headers, normalize_base


def resolve_ad_hoc_endpoint(endpoint_url: str) -> Tuple[str, Dict[str, str]]:
    """Resolve a raw endpoint URL to (chat_url, headers).

    Headers are empty when no matching ``ModelEndpoint`` (or no API key on
    it) is found — the URL may be a local/unauthenticated endpoint.
    """
    base = normalize_base(endpoint_url)
    chat_url = build_chat_url(base)
    headers: Dict[str, str] = {}
    db = SessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(ModelEndpoint.base_url == base).first()
        if ep and ep.api_key:
            headers = build_headers(ep.api_key, base)
    finally:
        db.close()
    return chat_url, headers
