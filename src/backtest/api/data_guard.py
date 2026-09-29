"""Refuse to run on a disabled data source.

Flask-aware half of :mod:`backtest.data.sources_policy`. The policy decides;
this module enforces it at the request boundary, so a disabled source produces
a 409 and a sentence explaining what to do — not a run that looks real.

    from backtest.api.data_guard import guard_source, data_source_status

Guards are applied only to routes that actually consume candles. Reading run
history or listing strategies is unaffected: refusing those would be a broken
app, not a safe one.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from flask import current_app, jsonify

from backtest.data.sources_policy import SourcePolicy, source_policy

__all__ = ["guard_source", "data_source_status", "active_policy"]


def active_policy() -> SourcePolicy:
    """The policy for this app instance.

    Resolved into ``app.config`` at startup so a test (or a deployment that
    reads the config once) can substitute one without touching the module-level
    cache.
    """
    try:
        configured = current_app.config.get("DATA_SOURCE_POLICY")
    except RuntimeError:  # outside an app context
        return source_policy()
    return configured if isinstance(configured, SourcePolicy) else source_policy()


def data_source_status() -> dict[str, Any]:
    """The whole picture, for the UI to render."""
    policy = active_policy()
    name = _configured_name()
    return {
        "active": name,
        "allowed": policy.is_enabled(name),
        "certifiable": policy.is_certifiable(name),
        "refusal": policy.refusal_for(name),
        "sources": policy.describe(),
    }


def _configured_name() -> str:
    try:
        return str(current_app.config.get("BACKTEST_SOURCE", "synthetic") or "synthetic")
    except RuntimeError:
        return "synthetic"


def guard_source() -> Optional[Tuple[Any, int]]:
    """Return a refusal response if the configured source is not allowed."""
    refusal = active_policy().refusal_for(_configured_name())
    if not refusal:
        return None
    return (
        jsonify(
            {
                "error": refusal,
                "data_source": data_source_status(),
                "code": "data_source_disabled",
            }
        ),
        409,
    )
