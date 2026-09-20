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
    # Settings first: the scan can block for a long time on a fresh install
    # (macOS holds a folder read until the user answers the permission
    # prompt), and the routing switch must not wait on it.
    try:
        from services.localmodels.helper import enable_fast_lane_once
        if enable_fast_lane_once():
            logger.info("Fast Lane routing enabled (helper model answers short messages)")
    except Exception as e:
        logger.warning("Fast Lane migration failed: %s", e)
    try:
        rescan()
    except Exception as e:
        logger.warning("Local model startup scan failed: %s", e)
