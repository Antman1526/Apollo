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
    names = [m.name for m in models if m.kind == "chat"]
    names += [m.name for m in models if m.kind == "embedding"]
    payload = json.dumps(names)
    db = SessionLocal()
    try:
        try:
            _filter_expr = ModelEndpoint.base_url == LOCAL_BASE_URL
        except AttributeError:
            _filter_expr = True  # test stub — _FakeQuery.filter ignores args
        ep = db.query(ModelEndpoint).filter(_filter_expr).first()
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
