"""Scan + sync orchestration shared by the API routes and startup."""
from __future__ import annotations

import logging

from services.localmodels.config import get_local_model_dirs
from services.localmodels.scanner import scan_dirs, LocalModel
from services.localmodels.registry import sync_managed_endpoint
from services.localmodels.server_manager import get_server

logger = logging.getLogger(__name__)


def rescan() -> list[LocalModel]:
    """Scan configured dirs, refresh the server catalog, and sync the picker."""
    models = scan_dirs(get_local_model_dirs())
    get_server().set_catalog(models)  # keep server catalog in sync
    sync_managed_endpoint(models)
    return models


def startup_scan() -> None:
    try:
        rescan()
    except Exception as e:
        logger.warning("Local model startup scan failed: %s", e)
