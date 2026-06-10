"""Small startup helpers for clearer route-registration failures."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class RouterSpec:
    label: str
    factory: Callable[..., Any]
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)


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


def register_router_specs(
    app: Any,
    specs: Iterable[RouterSpec],
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Register a simple sequence of routers and return them by label."""
    routers: dict[str, Any] = {}
    for spec in specs:
        routers[spec.label] = build_and_include_router(
            app,
            spec.label,
            spec.factory,
            *spec.args,
            logger=logger,
            **spec.kwargs,
        )
    return routers
