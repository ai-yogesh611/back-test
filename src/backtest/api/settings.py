"""Settings API — Cost & Risk Settings panel backend (certified Phase 1).

Endpoints for broker cost-model profiles:

* ``GET  /api/settings/brokers``              list profiles (+ preset lineage, validation stamp)
* ``GET  /api/settings/brokers/<id>``         one profile
* ``PUT  /api/settings/brokers/<id>``         upsert (audited per field)
* ``GET  /api/settings/brokers/<id>/audit``   who/when/old→new trail
* ``POST /api/settings/brokers/<id>/validate`` contract-note reconciliation
  (``CommissionCalculator.validate_against_contract_note``) — PASS stamps the
  profile; FAIL returns per-component mismatches. Advisory for paper,
  the hard gate for live arming is Phase 2.

Layering: this module talks to the store (:mod:`backtest.api.broker_profiles_store`)
and the fee engine; it never mutates running runners (v2 §0 — edits apply to
runs started after the save).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backtest.logging_config import get_logger

settings_bp = Blueprint("settings_api", __name__)
log = get_logger(__name__)


def _store():
    from backtest.api.broker_profiles_store import get_broker_profile_store

    return get_broker_profile_store()


@settings_bp.get("/api/settings/brokers")
def list_broker_profiles() -> tuple:
    try:
        return jsonify({"profiles": _store().list_profiles()}), 200
    except Exception as exc:  # noqa: BLE001 — panel must degrade, not 500-loop
        log.exception("list broker profiles failed")
        return jsonify({"error": str(exc)}), 503


@settings_bp.get("/api/settings/brokers/<profile_id>")
def get_broker_profile(profile_id: str) -> tuple:
    profile = _store().get_profile(profile_id)
    if profile is None:
        return jsonify({"error": f"unknown profile {profile_id!r}"}), 404
    return jsonify({"profile": profile}), 200


@settings_bp.put("/api/settings/brokers/<profile_id>")
def upsert_broker_profile(profile_id: str) -> tuple:
    payload = request.get_json(silent=True) or {}
    payload["profile_id"] = profile_id
    try:
        saved = _store().upsert_profile(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        log.exception("upsert broker profile failed")
        return jsonify({"error": str(exc)}), 503
    log.info("broker profile %s updated via settings panel", profile_id)
    return jsonify({"profile": saved}), 200


@settings_bp.get("/api/settings/active-broker")
def get_active_broker() -> tuple:
    """The panel-selected active broker (null = yaml ``active_broker`` wins)."""
    return jsonify({"active_broker": _store().get_active_broker()}), 200


@settings_bp.put("/api/settings/active-broker")
def set_active_broker() -> tuple:
    body = request.get_json(silent=True) or {}
    profile_id = str(body.get("profile_id", "")).strip()
    try:
        _store().set_active_broker(profile_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    log.info("active broker set to %s via settings panel", profile_id)
    return jsonify({"active_broker": profile_id}), 200


@settings_bp.get("/api/settings/brokers/<profile_id>/audit")
def broker_profile_audit(profile_id: str) -> tuple:
    limit = min(int(request.args.get("limit", 100)), 500)
    return jsonify({"audit": _store().get_audit(profile_id, limit)}), 200


@settings_bp.post("/api/settings/brokers/<profile_id>/validate")
def validate_broker_profile(profile_id: str) -> tuple:
    """Reconcile the stored profile against a real contract note.

    Body: ``{"trade_value": 125000, "quantity": 100, "side": "SELL",
    "segment": "equity_delivery", "expected": {"brokerage": 20, "stt": 125, ...},
    ["document_id": "note-123"]}``. All-PASS stamps the profile (audit-trailed);
    any mismatch returns the component list with HTTP 422 and stamps nothing.
    """
    from backtest.simulator.fees import CommissionCalculator

    body = request.get_json(silent=True) or {}
    try:
        expected = body.get("expected") or {}
        if not expected:
            return jsonify({"error": "expected: component→amount map is required"}), 400
        store = _store()
        profile = store.resolve(profile_id)
        calc = CommissionCalculator(broker=profile)
        mismatches = calc.validate_against_contract_note(
            trade_value=body["trade_value"],
            quantity=body["quantity"],
            side=body.get("side", "SELL"),
            segment=body.get("segment", "equity_delivery"),
            expected=expected,
            tolerance=body.get("tolerance", 0.05),
        )
    except KeyError as exc:
        return jsonify({"error": f"missing field: {exc}"}), 400
    except Exception as exc:  # noqa: BLE001 — fee engine validation errors are 4xx-able
        log.exception("contract-note validation failed for %s", profile_id)
        return jsonify({"error": str(exc)}), 400

    if mismatches:
        return jsonify({"status": "FAIL", "mismatches": mismatches}), 422

    document_id = str(body.get("document_id") or "unreferenced note")
    try:
        store.mark_validated(profile_id, document_id)
    except Exception:  # noqa: BLE001 — stamping must not mask a PASS
        log.exception("validation stamp failed for %s", profile_id)
    return jsonify({"status": "PASS", "stamped": document_id}), 200


# ---------------------------------------------------------------------------
# Phase 2 — segments + global live kill-switch (certified v2 §0 #2/#4/#6)
# ---------------------------------------------------------------------------


def _segments_store():
    from backtest.api.segments_store import get_segments_store

    return get_segments_store()


@settings_bp.get("/api/settings/segments")
def list_segments() -> tuple:
    try:
        return jsonify({"segments": _segments_store().list_segments()}), 200
    except Exception as exc:  # noqa: BLE001 — panel must degrade, not 500-loop
        log.exception("list segments failed")
        return jsonify({"error": str(exc)}), 503


@settings_bp.get("/api/settings/segments/<segment_id>")
def get_segment(segment_id: str) -> tuple:
    seg = _segments_store().get_segment(segment_id)
    if seg is None:
        return jsonify({"error": f"unknown segment {segment_id!r}"}), 404
    return jsonify({"segment": seg}), 200


@settings_bp.put("/api/settings/segments/<segment_id>")
def upsert_segment(segment_id: str) -> tuple:
    payload = request.get_json(silent=True) or {}
    payload["segment_id"] = segment_id
    try:
        saved = _segments_store().upsert_segment(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        log.exception("upsert segment failed")
        return jsonify({"error": str(exc)}), 503
    log.info("segment %s updated via settings panel", segment_id)
    return jsonify({"segment": saved}), 200


@settings_bp.delete("/api/settings/segments/<segment_id>")
def delete_segment(segment_id: str) -> tuple:
    try:
        if not _segments_store().delete_segment(segment_id):
            return jsonify({"error": f"unknown segment {segment_id!r}"}), 404
    except Exception as exc:  # noqa: BLE001
        log.exception("delete segment failed")
        return jsonify({"error": str(exc)}), 503
    return jsonify({"deleted": segment_id}), 200


@settings_bp.get("/api/settings/segments/<segment_id>/audit")
def segment_audit(segment_id: str) -> tuple:
    limit = min(int(request.args.get("limit", 100)), 500)
    return jsonify({"audit": _segments_store().get_audit(segment_id, limit)}), 200


@settings_bp.get("/api/settings/segments/<segment_id>/arming")
def segment_arming(segment_id: str) -> tuple:
    """The live-arming checklist for one segment (empty blockers = armed)."""
    try:
        blockers = _segments_store().live_arming_blockers(segment_id)
    except Exception as exc:  # noqa: BLE001 — fail closed, not 500
        log.exception("arming check failed")
        return jsonify({"blockers": [f"panel error: {exc}"]}), 503
    return jsonify({"segment_id": segment_id, "armed": not blockers, "blockers": blockers}), 200


@settings_bp.get("/api/settings/live-kill-switch")
def get_kill_switch() -> tuple:
    """True only when the global switch was explicitly armed (default OFF)."""
    try:
        return jsonify({"enabled": _segments_store().is_live_kill_switch_on()}), 200
    except Exception as exc:  # noqa: BLE001 — panel must degrade, not 500-loop
        log.exception("kill-switch read failed")
        return jsonify({"error": str(exc)}), 503


@settings_bp.put("/api/settings/live-kill-switch")
def set_kill_switch() -> tuple:
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled"))
    try:
        _segments_store().set_live_kill_switch(enabled)
    except Exception as exc:  # noqa: BLE001
        log.exception("kill-switch write failed")
        return jsonify({"error": str(exc)}), 503
    log.warning(
        "GLOBAL LIVE KILL-SWITCH %s via settings panel",
        "ARMED" if enabled else "ENGAGED (all live paused)",
    )
    return jsonify({"enabled": enabled}), 200
