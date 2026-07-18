"""Small, safe primitives for reporting handled failures.

Callers choose an outcome based on user impact:

* ``critical``: the operation cannot continue and must fail or return an error.
* ``degraded``: a non-essential dependency is unavailable but the user can
  continue with a clearly reduced capability.
* ``best_effort``: cleanup, telemetry, or secondary UI work failed after the
  primary operation already completed.

The helper deliberately accepts metadata rather than request or payload
objects. That keeps prompt, tool-body, and credential data out of logs.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Mapping

Outcome = Literal["critical", "degraded", "best_effort"]

_SENSITIVE_CONTEXT_PARTS = ("token", "password", "secret", "body", "content")


def sanitize_context(context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate and copy diagnostic context suitable for structured logs.

    Context is intentionally shallow. A useful diagnostic should identify an
    operation (for example ``task_id`` or ``tool_name``), not reproduce input
    data. Rejecting unsafe field names makes accidental prompt or secret
    logging fail visibly during development.
    """

    if context is None:
        return {}

    safe: dict[str, Any] = {}
    for key, value in context.items():
        normalized = str(key).lower()
        if any(part in normalized for part in _SENSITIVE_CONTEXT_PARTS):
            raise ValueError(f"unsafe observability context key: {key}")
        safe[str(key)] = value
    return safe


def report_exception(
    logger: logging.Logger,
    event: str,
    error: BaseException,
    *,
    outcome: Outcome,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Log a handled exception under Apollo's severity contract.

    The returned record is safe to attach to a component-health or task-state
    response. It deliberately excludes the exception message because upstream
    libraries may include user-supplied or credential-bearing values there.
    """

    if outcome not in {"critical", "degraded", "best_effort"}:
        raise ValueError(f"unknown observability outcome: {outcome}")

    safe_context = sanitize_context(context)
    record = {
        "event": event,
        "outcome": outcome,
        "error_type": type(error).__name__,
        **safe_context,
    }
    message = "event=%s outcome=%s error_type=%s context=%s"
    args = (event, outcome, type(error).__name__, safe_context)

    if outcome == "critical":
        logger.error(message, *args)
    elif outcome == "degraded":
        logger.warning(message, *args)
    else:
        logger.debug(message, *args, exc_info=(type(error), error, error.__traceback__))

    return record
