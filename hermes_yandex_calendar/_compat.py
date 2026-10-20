"""Compat layer: Hermes' ``get_provider_env`` when present, an env-only shim otherwise.

This plugin is a *standalone tool* (no WebSearchProvider), so the only host
symbol it needs is ``get_provider_env`` for reading its settings and secrets.
Inside Hermes that helper resolves the active profile's settings and secrets;
the plugin itself opens no file. Guard the import with ``ImportError`` ONLY —
real import errors (version mismatch, circular imports) must surface rather
than silently fall back to the shim.
"""

from __future__ import annotations

import os

try:
    from agent.web_search_provider import get_provider_env

    HERMES_AVAILABLE = True
except ImportError:  # pragma: no cover - only outside Hermes (tests / standalone)
    HERMES_AVAILABLE = False

    def get_provider_env(name: str) -> str:  # type: ignore[misc]
        """Read a variable from the process environment, stripped, or ``""`` when unset."""
        return (os.environ.get(name) or "").strip()


__all__ = ["HERMES_AVAILABLE", "get_provider_env"]
