# routes/compare_helpers.py
"""Shared endpoint-resolution helpers for ad-hoc, client-picked models.

Compare and Council both let the caller name a raw endpoint URL + model
(picked from the model list) rather than a configured "role" like
reviewer/utility — resolve_endpoint() doesn't apply there. This is the one
place that turns such a URL into a dispatchable (chat_url, headers) pair,
looking up whatever API key is on file for that endpoint's base URL.

SECURITY: the lookup is owner-scoped the same way ``resolve_endpoint`` in
src/endpoint_resolver.py (~276-281) is — a signed-in user must never be able
to spend another user's stored API key just by naming their endpoint URL in
a Council/Compare request. Callers must pass the request's resolved owner.
"""

from typing import Dict, Optional, Tuple

from core.database import ModelEndpoint, SessionLocal
from src.auth_helpers import owner_filter
from src.endpoint_resolver import build_chat_url, build_headers, normalize_base


def resolve_ad_hoc_endpoint(endpoint_url: str, owner: Optional[str] = None) -> Tuple[str, Dict[str, str]]:
    """Resolve a raw endpoint URL to (chat_url, headers), scoped to `owner`.

    Headers are empty when no matching, enabled ``ModelEndpoint`` visible to
    `owner` (or no API key on it) is found — the URL may be a
    local/unauthenticated endpoint, or may belong to a different user.
    """
    base = normalize_base(endpoint_url)
    chat_url = build_chat_url(base)
    headers: Dict[str, str] = {}
    db = SessionLocal()
    try:
        q = db.query(ModelEndpoint).filter(
            ModelEndpoint.base_url == base,
            ModelEndpoint.is_enabled == True,
        )
        ep = owner_filter(q, ModelEndpoint, owner).first() if owner else q.first()
        if ep and ep.api_key:
            headers = build_headers(ep.api_key, base)
    finally:
        db.close()
    return chat_url, headers
