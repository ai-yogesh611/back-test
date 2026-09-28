"""Broker profile store — DB-first persistence for the Cost & Risk Settings panel.

Phase 1 of the architect-certified design (docs/drafts/BROKER-CAPITAL-SETTINGS-PANEL.md §0):

* **DB-first**: panel edits land in ``broker_profiles`` (+ audit rows); the fee
  engine reads profiles from here at runner-construction time.
* **YAML bootstrap**: on first use the store seeds itself from
  ``config/brokers.yaml`` (or the built-in presets when the file is absent) so
  a fresh deployment starts with the same rates the yaml path produced.
* **Running runners are never mutated** (v2 §0): profiles are snapshotted into
  a :class:`~backtest.simulator.fees.CommissionCalculator` at construction;
  edits apply to runs started afterwards.

Every mutation writes ``broker_profile_audit`` rows (who/when/old→new) — the
tax-audit trail the contract-note validator stamps into.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Mapping

from sqlalchemy import select

from backtest.db.models import BrokerProfileAudit, BrokerProfileRow
from backtest.simulator.fees import (
    BROKER_PRESETS,
    IndiaEquityFees,
    get_broker_preset,
)

__all__ = ["BrokerProfileStore", "get_broker_profile_store"]

logger = logging.getLogger("backtest.api.settings")


def _preset_rows() -> list[dict[str, Any]]:
    """Seed payloads for every built-in preset (idempotent bootstrap)."""
    rows: list[dict[str, Any]] = []
    for name, factory in sorted(BROKER_PRESETS.items()):
        profile = factory()
        schedule = profile.fee_schedule
        rates: dict[str, Any] = {}
        if isinstance(schedule, IndiaEquityFees):
            rates = {
                "stt_delivery": str(schedule.stt_delivery),
                "stt_intraday_sell": str(schedule.stt_intraday_sell),
                "exchange_txn_equity": str(schedule.exchange_txn_equity),
                "sebi_turnover": str(schedule.sebi_turnover),
                "ipft": str(schedule.ipft),
                "stamp_duty_delivery": str(schedule.stamp_duty_delivery),
                "stamp_duty_intraday": str(schedule.stamp_duty_intraday),
                "gst_rate": str(schedule.gst_rate),
                "dp_charges": str(schedule.dp_charges),
            }
        commission = profile.commission_model.to_dict()
        delivery = (
            profile.delivery_commission_model.to_dict()
            if profile.delivery_commission_model is not None
            else None
        )
        options = (
            profile.options_commission_model.to_dict()
            if profile.options_commission_model is not None
            else None
        )
        rows.append(
            {
                "profile_id": name,
                "profile_name": name.replace("_", " ").title(),
                "is_preset": True,
                "currency": profile.currency,
                "default_segment": profile.default_segment,
                "commission_model": {
                    "default": commission,
                    **({"delivery": delivery} if delivery else {}),
                    **({"options": options} if options else {}),
                },
                "statutory_rates": rates,
                "minimum_commission": (
                    float(profile.minimum_commission)
                    if profile.minimum_commission is not None
                    else None
                ),
            }
        )
    return rows


class BrokerProfileStore:
    """CRUD + audit for broker cost-model profiles, DB-backed."""

    def __init__(self, manager: Any) -> None:
        self._manager = manager
        BrokerProfileRow.ensure_schema(manager)
        BrokerProfileAudit.ensure_schema(manager)

    # -- active broker selector (certified Phase 1) ------------------------

    def get_active_broker(self) -> str | None:
        """The panel-selected active broker, or None (yaml ``active_broker`` wins)."""
        with self._manager.session() as session:
            row = session.get(BrokerProfileRow, "__active__")
            if row is None or row.commission_model.get("active") is None:
                return None
            return str(row.commission_model["active"])

    def set_active_broker(self, profile_id: str, changed_by: str = "admin") -> None:
        """Record the panel's active-broker choice (audit-trailed).

        Uses a sentinel row (``__active__``) rather than an is_active flag so
        the choice itself is audited like any other profile change.
        """
        with self._manager.session() as session:
            row = session.get(BrokerProfileRow, profile_id)
            if row is None:
                raise ValueError(f"unknown profile {profile_id!r}")
            marker = session.get(BrokerProfileRow, "__active__")
            old = marker.commission_model.get("active") if marker is not None else None
            if marker is None:
                marker = BrokerProfileRow(
                    profile_id="__active__",
                    profile_name="Active broker selection",
                    is_preset=False,
                    commission_model={"active": profile_id},
                    statutory_rates={},
                )
                session.add(marker)
            else:
                marker.commission_model = {**marker.commission_model, "active": profile_id}
            self._audit(session, "__active__", "active_broker", old, profile_id, changed_by)

    # -- bootstrap ---------------------------------------------------------

    def seed_from_yaml(self) -> int:
        """Insert preset rows missing from the DB. Returns rows added."""
        added = 0
        with self._manager.session() as session:
            existing = {
                row.profile_id
                for row in session.execute(select(BrokerProfileRow)).scalars()
            }
            for payload in _preset_rows():
                if payload["profile_id"] in existing:
                    continue
                session.add(BrokerProfileRow(**payload))
                added += 1
        if added:
            logger.info("seeded %d broker profiles from presets/yaml", added)
        return added

    # -- reads -------------------------------------------------------------

    def list_profiles(self) -> list[dict[str, Any]]:
        with self._manager.session() as session:
            rows = session.execute(select(BrokerProfileRow)).scalars().all()
            return [self._row_to_dict(row) for row in rows]

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        with self._manager.session() as session:
            row = session.get(BrokerProfileRow, profile_id)
            return self._row_to_dict(row) if row is not None else None

    def get_audit(self, profile_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._manager.session() as session:
            stmt = (
                select(BrokerProfileAudit)
                .where(BrokerProfileAudit.profile_id == profile_id)
                .order_by(BrokerProfileAudit.changed_at.desc())
                .limit(limit)
            )
            return [
                {
                    "audit_id": row.audit_id,
                    "profile_id": row.profile_id,
                    "field_changed": row.field_changed,
                    "old_value": row.old_value,
                    "new_value": row.new_value,
                    "changed_by": row.changed_by,
                    "changed_at": row.changed_at.isoformat() if row.changed_at else None,
                }
                for row in session.execute(stmt).scalars()
            ]

    # -- writes ------------------------------------------------------------

    def upsert_profile(
        self, payload: Mapping[str, Any], changed_by: str = "admin"
    ) -> dict[str, Any]:
        """Create or update a profile; audit every changed field.

        Preset rows may be edited (the audit trail records the override) but
        ``is_preset`` itself is immutable — an override keeps its lineage.
        """
        profile_id = str(payload.get("profile_id", "")).strip()
        if not profile_id:
            raise ValueError("profile_id is required")
        statutory = dict(payload.get("statutory_rates") or {})
        self._validate_rates(statutory)

        with self._manager.session() as session:
            row = session.get(BrokerProfileRow, profile_id)
            if row is None:
                row = BrokerProfileRow(
                    profile_id=profile_id,
                    profile_name=str(payload.get("profile_name") or profile_id),
                    is_preset=bool(payload.get("is_preset", False)),
                    currency=str(payload.get("currency") or "INR"),
                    default_segment=str(payload.get("default_segment") or "equity_delivery"),
                    commission_model=dict(payload.get("commission_model") or {}),
                    statutory_rates=statutory,
                    minimum_commission=payload.get("minimum_commission"),
                )
                session.add(row)
                self._audit(session, profile_id, "__created__", None, "new profile", changed_by)
            else:
                changes = self._apply_update(row, payload, statutory, changed_by, session)
                if not changes:
                    return self._row_to_dict(row)
            session.flush()
            return self._row_to_dict(row)

    def mark_validated(
        self,
        profile_id: str,
        document_id: str,
        when: date | None = None,
        changed_by: str = "admin",
    ) -> None:
        """Stamp a profile with the contract-note validation (v2 §0 #6)."""
        with self._manager.session() as session:
            row = session.get(BrokerProfileRow, profile_id)
            if row is None:
                raise ValueError(f"unknown profile {profile_id!r}")
            row.validated_on = when or date.today()
            row.contract_note_ref = document_id
            self._audit(
                session,
                profile_id,
                "validated",
                row.contract_note_ref,
                document_id,
                changed_by,
            )

    def resolve(self, profile_id: str):
        """A live :class:`BrokerProfile` for runner construction (DB row first,
        built-in preset fallback so a wiped DB never blocks a spawn)."""
        stored = self.get_profile(profile_id)
        if stored is not None:
            from backtest.simulator.fees import BrokerProfile

            return BrokerProfile(**BrokerProfileRow(**self._db_shape(stored)).to_profile_kwargs())
        return get_broker_preset(profile_id)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _db_shape(stored: dict[str, Any]) -> dict[str, Any]:
        return {
            "profile_id": stored["profile_id"],
            "profile_name": stored["profile_name"],
            "is_preset": stored["is_preset"],
            "currency": stored["currency"],
            "default_segment": stored["default_segment"],
            "commission_model": stored["commission_model"],
            "statutory_rates": stored["statutory_rates"],
            "minimum_commission": stored.get("minimum_commission"),
        }

    @staticmethod
    def _row_to_dict(row: BrokerProfileRow) -> dict[str, Any]:
        return {
            "profile_id": row.profile_id,
            "profile_name": row.profile_name,
            "is_preset": row.is_preset,
            "currency": row.currency,
            "default_segment": row.default_segment,
            "commission_model": row.commission_model,
            "statutory_rates": row.statutory_rates,
            "minimum_commission": (
                float(row.minimum_commission) if row.minimum_commission is not None else None
            ),
            "validated_on": row.validated_on.isoformat() if row.validated_on else None,
            "contract_note_ref": row.contract_note_ref,
        }

    @staticmethod
    def _validate_rates(rates: Mapping[str, Any]) -> None:
        """Refuse negative or non-numeric statutory rates (fail closed)."""
        allowed = {
            "stt_delivery",
            "stt_intraday_sell",
            "stt_futures_sell",
            "stt_options_sell",
            "exchange_txn_equity",
            "exchange_txn_futures",
            "exchange_txn_options",
            "sebi_turnover",
            "ipft",
            "stamp_duty_delivery",
            "stamp_duty_intraday",
            "stamp_duty_futures",
            "stamp_duty_options",
            "gst_rate",
            "dp_charges",
        }
        for key, value in rates.items():
            if key not in allowed:
                raise ValueError(f"unknown statutory rate {key!r}")
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key}: not a number ({value!r})") from exc
            if number < 0:
                raise ValueError(f"{key}: must not be negative")

    def _apply_update(
        self,
        row: BrokerProfileRow,
        payload: Mapping[str, Any],
        statutory: dict[str, Any],
        changed_by: str,
        session: Any,
    ) -> list[str]:
        changes: list[str] = []
        fields = {
            "profile_name": str(payload.get("profile_name") or row.profile_name),
            "currency": str(payload.get("currency") or row.currency),
            "default_segment": str(payload.get("default_segment") or row.default_segment),
            "minimum_commission": payload.get("minimum_commission", row.minimum_commission),
        }
        for name, new in fields.items():
            old = getattr(row, name)
            if old != new:
                self._audit(
                    session, row.profile_id, name,
                    str(old) if old is not None else None,
                    str(new) if new is not None else None, changed_by,
                )
                setattr(row, name, new)
                changes.append(name)
        old_rates = json.dumps(row.statutory_rates, sort_keys=True)
        new_rates = json.dumps(statutory, sort_keys=True)
        if old_rates != new_rates:
            self._audit(
                session, row.profile_id, "statutory_rates", old_rates, new_rates, changed_by
            )
            row.statutory_rates = statutory
            changes.append("statutory_rates")
        if payload.get("commission_model") is not None:
            old_model = json.dumps(row.commission_model, sort_keys=True)
            new_model = json.dumps(payload["commission_model"], sort_keys=True)
            if old_model != new_model:
                self._audit(
                    session, row.profile_id, "commission_model", old_model, new_model, changed_by
                )
                row.commission_model = dict(payload["commission_model"])
                changes.append("commission_model")
        return changes

    @staticmethod
    def _audit(
        session: Any,
        profile_id: str,
        field: str,
        old: Any,
        new: Any,
        changed_by: str,
    ) -> None:
        session.add(
            BrokerProfileAudit(
                profile_id=profile_id,
                field_changed=field,
                old_value=None if old is None else str(old),
                new_value=None if new is None else str(new),
                changed_by=changed_by,
            )
        )


_STORE: BrokerProfileStore | None = None


def get_broker_profile_store() -> BrokerProfileStore:
    """Process-wide store (built on the app's DatabaseManager)."""
    global _STORE
    if _STORE is None:
        from backtest.db import DatabaseManager

        _STORE = BrokerProfileStore(DatabaseManager.from_env())
        _STORE.seed_from_yaml()
    return _STORE
