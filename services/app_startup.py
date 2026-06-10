"""Small startup helpers for clearer route-registration failures."""

from __future__ import annotations

import logging
from typing import Any, Callable


def include_router_checked(app: Any, router: Any, label: str, logger: logging.Logger | None = None) -> Any:
    """Include a router and raise a labeled startup error if registration fails."""
    try:
        app.include_router(router)
    except Exception as exc:
        if logger:
            logger.exception("Failed to register %s routes", label)
        raise RuntimeError(f"Failed to register {label} routes") from exc
    if logger:
        logger.debug("Registered %s routes", label)
    return router


def build_and_include_router(
    app: Any,
    label: str,
    factory: Callable[..., Any],
    *args: Any,
    logger: logging.Logger | None = None,
    **kwargs: Any,
) -> Any:
    """Build a router inside the labeled registration guard, then include it."""
    try:
        router = factory(*args, **kwargs)
    except Exception as exc:
        if logger:
            logger.exception("Failed to build %s routes", label)
        raise RuntimeError(f"Failed to build {label} routes") from exc
    return include_router_checked(app, router, label, logger=logger)
