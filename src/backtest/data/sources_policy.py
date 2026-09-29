"""Which market-data sources this deployment is allowed to run on.

Backed by ``config/data_sources.yaml``; see that file for the full rationale.
The short version: a run on synthetic candles can return a robustness score of
8/10 and read as certified, on prices that were never traded.

    from backtest.data.sources_policy import source_policy
    source_policy().is_enabled("db")     # -> True
    source_policy().is_enabled("synthetic")  # -> False
    source_policy().describe()           # -> [{name, enabled, label, certifiable}]

**This is a policy layer, not a removal.** ``SyntheticSource`` still exists and
still generates candles; the CLI flag still parses; the test fixtures still
build synthetic series on purpose. What changed is that the *app* will not
serve a backtest or an optimization on a disabled source, and will say why
instead of returning results that look real.

A missing or broken config is not fatal — it falls back to
:data:`FALLBACK_SOURCES`, which is the conservative choice: synthetic off, real
sources on. A typo in a YAML file should not be the thing that quietly re-enables
generated data.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Mapping

from backtest.data.source_tags import APP_SOURCE_TAGS

logger = logging.getLogger("backtest")

__all__ = [
    "SourceSpec",
    "SourcePolicy",
    "source_policy",
    "reset_source_policy",
    "FALLBACK_SOURCES",
    "PROFILE_ENV",
]

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "data_sources.yaml"
PROFILE_ENV = "BACKTEST_DATA_PROFILE"

#: Used when the file is missing, unreadable, or does not describe a source.
#: Synthetic is off here too: a broken file must not be the thing that quietly
#: re-enables generated data.
FALLBACK_SOURCES: dict[str, dict[str, Any]] = {
    "synthetic": {"enabled": False, "label": "Synthetic (random walk)", "certifiable": False},
    "db": {"enabled": True, "label": "Real Data (PostgreSQL)", "certifiable": True},
    "mstock": {"enabled": True, "label": "Broker (mstock)", "certifiable": True},
    "csv": {"enabled": True, "label": "CSV files", "certifiable": False},
}


class SourceSpec:
    """One row of the policy: is it allowed, and what does it mean."""

    __slots__ = ("name", "enabled", "label", "certifiable", "note")

    def __init__(
        self,
        name: str,
        enabled: bool = False,
        label: str = "",
        certifiable: bool = False,
        note: str = "",
    ) -> None:
        self.name = name
        self.enabled = bool(enabled)
        self.label = label or name
        self.certifiable = bool(certifiable)
        self.note = note

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "label": self.label,
            "certifiable": self.certifiable,
            "note": self.note,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SourceSpec {self.name} enabled={self.enabled}>"


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        logger.warning("[data-policy] %s not found — using built-in defaults", path)
        return {}
    except Exception:  # noqa: BLE001 — a broken file must not break startup
        logger.warning(
            "[data-policy] could not read %s — using built-in defaults", path, exc_info=True
        )
        return {}
    return data if isinstance(data, dict) else {}


class SourcePolicy:
    """Resolved policy for this process."""

    def __init__(self, sources: Mapping[str, SourceSpec], profile: str = "default") -> None:
        self._sources: dict[str, SourceSpec] = dict(sources)
        self.profile = profile

    # -- queries -----------------------------------------------------------
    def is_enabled(self, name: str) -> bool:
        spec = self._sources.get(str(name or "").strip().lower())
        return bool(spec and spec.enabled)

    def is_certifiable(self, name: str) -> bool:
        spec = self._sources.get(str(name or "").strip().lower())
        return bool(spec and spec.enabled and spec.certifiable)

    def get(self, name: str) -> SourceSpec | None:
        return self._sources.get(str(name or "").strip().lower())

    def enabled_names(self) -> list[str]:
        return [n for n, s in self._sources.items() if s.enabled]

    def describe(self) -> list[dict[str, Any]]:
        """Rows for the UI. Disabled sources are included, flagged, not hidden —
        a source that silently vanishes is impossible to debug."""
        return [self._sources[n].as_dict() for n in sorted(self._sources)]

    # -- the refusal -------------------------------------------------------
    def refusal_for(self, name: str) -> str | None:
        """None when the source may be used, else a message a user can act on."""
        raw = str(name or "").strip().lower()
        spec = self._sources.get(raw)
        if spec is None:
            return (
                f"Unknown data source '{name}'. Known sources: "
                f"{', '.join(sorted(self._sources)) or 'none'}."
            )
        if not spec.enabled:
            return (
                f"Data source '{spec.label}' is disabled in config/data_sources.yaml. "
                f"{spec.note or 'It is not certification-grade.'} "
                f"Enabled sources: {', '.join(self.enabled_names()) or 'none'}."
            )
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SourcePolicy profile={self.profile} enabled={self.enabled_names()}>"


_POLICY: SourcePolicy | None = None


def build_policy(
    path: Path | None = None, profile: str | None = None, env: Mapping[str, str] | None = None
) -> SourcePolicy:
    """Resolve the policy from disk. Exposed separately so tests can build one
    from a fixture file without touching the process-wide cache."""
    env = os.environ if env is None else env
    cfg_path = Path(path) if path is not None else _CONFIG_PATH
    data = _load_yaml(cfg_path)

    base = data.get("default")
    base = base if isinstance(base, dict) else {}
    name = profile or env.get(PROFILE_ENV) or data.get("active_profile") or "default"
    named = (data.get("profiles") or {}).get(str(name))
    merged = _deep_merge(base, named if isinstance(named, dict) else {})

    raw_sources = merged.get("sources")
    if not isinstance(raw_sources, dict) or not raw_sources:
        logger.warning("[data-policy] no sources described — using built-in defaults")
        raw_sources = FALLBACK_SOURCES

    specs: dict[str, SourceSpec] = {}
    for key, value in raw_sources.items():
        value = value if isinstance(value, dict) else {"enabled": value}
        fallback = FALLBACK_SOURCES.get(key, {})
        specs[str(key)] = SourceSpec(
            name=str(key),
            enabled=value.get("enabled", fallback.get("enabled", False)),
            label=value.get("label", fallback.get("label", str(key))),
            certifiable=value.get("certifiable", fallback.get("certifiable", False)),
            note=str(value.get("note", "") or "").strip(),
        )
    _warn_about_vocabulary(specs)
    return SourcePolicy(specs, profile=str(name))


def _warn_about_vocabulary(specs: Mapping[str, SourceSpec]) -> None:
    """Cross-check the config against the app's source vocabulary.

    A misspelled key is the failure mode worth catching: ``postgress:
    enabled: true`` reads as real data, matches nothing, and silently leaves
    the real source disabled.
    """
    known = set(APP_SOURCE_TAGS)
    unknown = sorted(set(specs) - known)
    if unknown:
        logger.warning(
            "[data-policy] %s describes unknown source(s) %s — the app's vocabulary is %s. "
            "They will never match a running source.",
            _CONFIG_PATH.name,
            ", ".join(unknown),
            ", ".join(sorted(known)),
        )
    missing = sorted(known - set(specs))
    if missing:
        # Not fatal: an unlisted source falls back to FALLBACK_SOURCES, which
        # is the conservative answer. Worth saying out loud, though.
        logger.warning(
            "[data-policy] %s does not describe %s — using built-in defaults for them.",
            _CONFIG_PATH.name,
            ", ".join(missing),
        )


def source_policy() -> SourcePolicy:
    """The process-wide policy, resolved once."""
    global _POLICY
    if _POLICY is None:
        _POLICY = build_policy()
    return _POLICY


def reset_source_policy() -> None:
    """Drop the cache — for tests, and for anything that edits the config."""
    global _POLICY
    _POLICY = None
