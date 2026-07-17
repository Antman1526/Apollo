"""Compatibility exports for legacy imports of ``core.constants``.

The application path contract lives in :mod:`src.constants`; retaining this
module avoids two divergent copies of the same runtime configuration.
"""

from src.constants import *  # noqa: F403
