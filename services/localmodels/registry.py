"""Maintain the single managed ModelEndpoint that represents local models."""
from __future__ import annotations

import json
import logging
import uuid

from core.database import SessionLocal, ModelEndpoint
from services.localmodels.scanner import LocalModel

logger = logging.getLogger(__name__)

LOCAL_BASE_URL = "local://llama.cpp"
LOCAL_ENDPOINT_NAME = "Local (llama.cpp)"


def is_local_endpoint(base_url: str | None) -> bool:
    return bool(base_url) and base_url.startswith("local://")


def sync_managed_endpoint(models: list[LocalModel]) -> None:
    """Create or update the managed endpoint's cached_models from the catalog.

    Chat models are listed first, then embedding models. Safe to call repeatedly.
    """
    # The picker is name-based, so the same model present in two configured
    # dirs (e.g. an external drive and a Desktop copy) must list once —
    # resolution picks the first scanned copy either way.
    names: list[str] = []
    seen: set[str] = set()
    for kind in ("chat", "embedding"):
        for m in models:
            if m.kind == kind and m.name not in seen:
                seen.add(m.name)
                names.append(m.name)
    payload = json.dumps(names)
    db = SessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(
            ModelEndpoint.base_url == LOCAL_BASE_URL
        ).first()
        if ep is None:
            ep = ModelEndpoint(
                id=str(uuid.uuid4()),
                name=LOCAL_ENDPOINT_NAME,
                base_url=LOCAL_BASE_URL,
                api_key=None,
                is_enabled=True,
                model_type="llm",
                owner=None,
                cached_models=payload,
            )
            db.add(ep)
        else:
            ep.cached_models = payload
            ep.is_enabled = True
        db.commit()
        logger.info("Synced %d local models into managed endpoint", len(names))
    except Exception as e:  # never let a scan crash the caller
        logger.warning("Failed to sync managed local endpoint: %s", e)
    finally:
        db.close()
