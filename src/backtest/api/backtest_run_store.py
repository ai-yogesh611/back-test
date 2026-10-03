"""Backtest run ledger — persistence for completed backtests (PRD R1-R8).

Repository over ``backtest_runs`` / ``backtest_compare_runs`` /
``backtest_run_series`` (migration 018,
``docs/BACKTEST-RUN-PERSISTENCE-PRD.md``). The ledger is append-only: one
immutable row per completed run, provenance and readiness frozen at run time.

Design rules enforced here, not upstream:
* Writes are fail-SOFT-BUT-LOUD at the caller (endpoint returns 200 with
  ``persisted=false``); this module raises on real ledger failures so the
  caller can decide, and logs ``[run-ledger]`` events either way.
* The heavy series commits in its own transaction and flips the parent row's
  ``series_status`` to ``present`` in the SAME transaction — the ledger can
  never claim a series it does not have.
* Every metric passes the optimizer's sanitizer (:func:`clamp`), so SQLite
  stores exactly what PostgreSQL stores and neither can be poisoned by NaN.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import subprocess
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import delete, func, select, update

from backtest.adapters.backtest_adapter import BacktestAdapter
from backtest.db.models import (
    BACKTEST_LEDGER_TABLES,
    BacktestCompareRun,
    BacktestRun,
    BacktestRunSeries,
)
from backtest.optimization.store import clamp, clean_json

log = logging.getLogger("backtest.ledger")

PAYLOAD_VERSION = BacktestAdapter.PAYLOAD_VERSION

#: Retention cap for the heavy series rows (PRD R7: decided at 500/group).
SERIES_CAP_PER_GROUP = 500
#: A sweep runs after this many committed series writes — off the request path
#: by policy, cheap enough that the cap drifting by one interval is harmless.
_SWEEP_EVERY = 25

_REPO_ROOT = Path(__file__).resolve().parents[3]

_GIT_SENTINEL = object()
_git_sha_cache: Any = _GIT_SENTINEL


class LedgerError(Exception):
    """Raised by write/read helpers for conditions the caller must surface."""


class PayloadVersionMismatch(LedgerError):
    """Stored payload shape cannot be reconstructed by this build (PRD §5.3)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def jsonable(value: Any) -> Any:
    """Decimal → float, datetimes → ISO — JSON-safe projection of a row dict."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _row_dict(obj: Any, columns: Optional[list[str]] = None) -> dict[str, Any]:
    cols = columns or [c.key for c in obj.__table__.columns]
    return {c: jsonable(getattr(obj, c)) for c in cols}


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def config_hash(cfg: dict[str, Any]) -> str:
    """Canonical, deterministic hash of the run's economic determinants.

    ``allow_nan=False`` by design: NaN in *params* is a hard error (the
    metric sanitizer is for outputs, never inputs). All economic inputs
    (fees/slippage ride inside ``strategy_params`` in this codebase) must be
    inside the hashed dict — see PRD R1 boundary note.
    """
    canonical = json.dumps(
        {
            "strategy": cfg.get("strategy"),
            "symbol": cfg.get("symbol"),
            "timeframe": cfg.get("timeframe"),
            "from_date": cfg.get("from_date"),
            "to_date": cfg.get("to_date"),
            "params": cfg.get("strategy_params") or {},
            "engine": cfg.get("engine"),
            "capital": cfg.get("capital"),
        },
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _app_git_sha() -> Optional[str]:
    """Short git SHA of the running checkout, captured once per process."""
    global _git_sha_cache
    if _git_sha_cache is _GIT_SENTINEL:
        try:
            out = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=_REPO_ROOT,
                capture_output=True,
                timeout=2,
                check=True,
            )
            _git_sha_cache = out.stdout.decode().strip() or None
        except Exception:  # noqa: BLE001 — fingerprint is best-effort context
            _git_sha_cache = None
    return _git_sha_cache  # type: ignore[return-value]


def code_fingerprint(strategy_cls: Any) -> dict[str, Any]:
    """Code-side attestation: which build + which strategy source ran."""
    sha = None
    try:
        path = inspect.getsourcefile(strategy_cls)
        if path:
            sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, TypeError):
        sha = None
    return {"app_git_sha": _app_git_sha(), "strategy_sha256": sha}


#: Flat-column projection of ``payload["metrics"]``. Scale matches the
#: optimizer: total_return/cagr/max_drawdown as decimal fractions,
#: win_rate as a 0-100 percentage.
_FLAT_METRICS = {
    "sharpe": ("sharpe", "score"),
    "sortino": ("sortino", "score"),
    "calmar": ("calmar", "score"),
    "total_return": ("total_return_pct", "score"),
    "cagr": ("cagr_pct", "score"),
    "max_drawdown": ("max_drawdown_pct", "score"),
    "profit_factor": ("profit_factor", "score"),
    "win_rate": ("win_rate_pct", "pct"),
    "total_trades": ("total_trades", "int"),
}


def _flat_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col, (key, kind) in _FLAT_METRICS.items():
        raw = metrics.get(key)
        if raw is None:
            out[col] = None
            continue
        if kind == "int":
            out[col] = clamp(raw, "int")
        elif kind == "pct":
            out[col] = clamp(raw, "pct")
        else:
            # *_pct display metrics are percent-scaled; ledger columns store
            # fractions (same scale optimization_results writes).
            value = raw / 100.0 if key.endswith("_pct") else raw
            out[col] = clamp(value, "score")
    return out


def _flat_attestation(prov: dict[str, Any]) -> dict[str, Any]:
    return {
        "data_source": str(prov.get("data_source") or "")[:30] or None,
        "bars_count": clamp(prov.get("bars_count"), "int"),
        "data_fetch_date": _parse_date(prov.get("data_fetch_date")),
        "fetched_first_ts": _parse_ts(prov.get("data_from")),
        "fetched_last_ts": _parse_ts(prov.get("data_to")),
    }


def _alert_persist_failed(where: str, err: str) -> None:
    """Route ledger write failures through the alert broker (PRD R8).

    The broker dedupes on ``type:subject`` and enforces the re-notify
    cooldown, which is the rate limit R8 asks for — no local throttle here.
    """
    try:
        from backtest.alerts.broker import get_alert_broker

        get_alert_broker().raise_alert(
            "system_error",
            "warning",
            f"Backtest ledger write failed ({where}): {err}",
            data={"where": where, "error": err},
            subject=f"run-ledger:{where}",
        )
    except Exception:  # noqa: BLE001 — alerting must never mask the run
        log.debug("[run-ledger] alert dispatch unavailable", exc_info=True)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class BacktestRunLedger:
    """Repository over the ledger tables for a connected DatabaseManager."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self._series_writes = 0
        self._sweep_lock = threading.Lock()
        self.last_persist_error: Optional[str] = None

    # -- schema -------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create missing ledger tables (idempotent; alembic owns prod)."""
        from backtest.db.models import Base

        Base.metadata.create_all(
            self.db.engine, tables=list(BACKTEST_LEDGER_TABLES), checkfirst=True
        )

    # -- writes -------------------------------------------------------------

    def save_run(
        self,
        payload: dict[str, Any],
        *,
        kind: str = "single",
        created_by: Optional[str] = None,
        strategy_cls: Any = None,
        parent_compare_id: Optional[str] = None,
        optimization_run_id: Optional[str] = None,
        session: Any = None,
    ) -> str:
        """Insert the immutable R1 row (txn 1). Returns run_id.

        ``session`` lets compare writes fold parent+children into ONE
        transaction; standalone runs pass none and get their own.
        Raises on any DB failure — the caller converts that into
        ``persisted=false``; nothing here swallows an error silently.
        """
        cfg = payload.get("config") or {}
        metrics = payload.get("metrics") or {}
        prov = payload.get("provenance") or {}
        # Computed up front: the row is detached once the session closes, and
        # the log line below must not lazily reload expired attributes.
        h = config_hash(cfg)
        sid = str(cfg.get("strategy") or "")[:100]
        sym = str(cfg.get("symbol") or "")[:30]
        date_from = _parse_date(cfg.get("from_date"))
        date_to = _parse_date(cfg.get("to_date"))
        row = BacktestRun(
            config_hash=h,
            kind=kind,
            parent_compare_id=parent_compare_id,
            optimization_run_id=optimization_run_id,
            strategy_id=sid,
            symbol=sym,
            timeframe=str(cfg.get("timeframe") or "")[:10],
            date_from=date_from,
            date_to=date_to,
            capital=clamp(cfg.get("capital"), "money"),
            engine=str(cfg.get("engine") or "")[:30],
            payload_version=PAYLOAD_VERSION,
            params=clean_json(cfg.get("strategy_params") or {}),
            config=clean_json(cfg),
            readiness=clean_json(payload.get("readiness")),
            cost_shock=clean_json(payload.get("cost_shock")),
            metrics=clean_json(metrics),
            provenance=clean_json(prov),
            code_fingerprint=clean_json(
                code_fingerprint(strategy_cls) if strategy_cls is not None else {}
            ),
            created_by=created_by,
            **_flat_metrics(metrics),
            **_flat_attestation(prov),
        )
        if date_from is None or date_to is None:
            raise LedgerError("payload config is missing from_date/to_date")
        own = session is None
        if own:
            # session() context manager commits on exit.
            with self.db.session() as s:
                s.add(row)
                s.flush()
                run_id = row.run_id
        else:
            session.add(row)
            session.flush()
            run_id = row.run_id
        log.info(
            "[run-ledger] persist_ok run=%s hash=%s kind=%s %s/%s %s..%s",
            run_id[:8],
            h[:12],
            kind,
            sid,
            sym,
            date_from,
            date_to,
        )
        return run_id

    def save_series(self, run_id: str, payload: dict[str, Any]) -> int:
        """Insert the R2 row and flip the parent to ``present`` — one txn.

        Failure here is NOT a ledger failure: the parent row keeps
        ``series_status='write_failed'`` and still lists with flat metrics.
        Returns bytes written.
        """
        body = {
            "trades": clean_json(payload.get("trades")),
            "equity": clean_json(payload.get("equity")),
            "drawdown": clean_json(payload.get("drawdown")),
            "signals": clean_json(payload.get("signals")),
            "extras": clean_json(
                {
                    "benchmark": payload.get("benchmark"),
                    "monte_carlo": payload.get("monte_carlo"),
                }
            ),
        }
        size = len(json.dumps(body, separators=(",", ":"), default=str).encode())
        with self.db.session() as s:
            s.add(
                BacktestRunSeries(
                    run_id=run_id, bytes_written=size, **body
                )
            )
            # Flip in the SAME transaction: 'present' only ever exists where
            # the series row exists (PRD R7 UI invariant).
            s.execute(
                update(BacktestRun)
                .where(
                    BacktestRun.run_id == run_id,
                    BacktestRun.series_status == "write_failed",
                )
                .values(series_status="present")
            )
        self._series_writes += 1
        if self._series_writes % _SWEEP_EVERY == 0:
            self._schedule_sweep()
        log.debug("[run-ledger] series run=%s bytes=%d", run_id[:8], size)
        return size

    def fail_persist(self, where: str, err: Any) -> dict[str, Any]:
        """Log + alert a failed ledger write and return the response fields.

        One funnel for every ``persisted=false`` path so the event shape is
        identical everywhere (PRD R8).
        """
        message = f"{type(err).__name__}: {err}"
        self.last_persist_error = f"{where}: {message}"
        log.error("[run-ledger] persist_failed %s err=%s", where, message)
        _alert_persist_failed(where, message)
        return {"persisted": False, "persist_error": message}

    def ok_persist(self, run_id: str) -> dict[str, Any]:
        return {"persisted": True, "run_id": run_id}

    # -- retention (PRD R7) ---------------------------------------------------

    def _schedule_sweep(self) -> None:
        """Fire the sweep on a daemon thread — never in the request path."""
        if not self._sweep_lock.acquire(blocking=False):
            return  # a sweep is already running
        threading.Thread(
            target=self._sweep_and_release, name="ledger-retention", daemon=True
        ).start()

    def _sweep_and_release(self) -> None:
        try:
            self.sweep_series()
        except Exception as exc:  # noqa: BLE001 — next trigger retries
            log.warning("[run-ledger] retention sweep failed: %s", exc)
        finally:
            self._sweep_lock.release()

    def sweep_series(self, cap: int = SERIES_CAP_PER_GROUP) -> int:
        """Evict series beyond the newest ``cap`` per (strategy,symbol,timeframe).

        Two-step, one transaction (PRD R7): flip parents to ``evicted`` first,
        then delete the series rows — so the UI can never read ``present``
        for a missing blob, and a missed sweep only delays, never corrupts.
        """
        rn = (
            func.row_number()
            .over(
                partition_by=(
                    BacktestRun.strategy_id,
                    BacktestRun.symbol,
                    BacktestRun.timeframe,
                ),
                order_by=BacktestRun.created_at.desc(),
            )
            .label("rn")
        )
        subq = (
            select(BacktestRun.run_id, rn)
            .where(BacktestRun.series_status == "present")
            .subquery()
        )
        stale = select(subq.c.run_id).where(subq.c.rn > cap)
        with self.db.session() as s:
            victims = list(s.execute(stale).scalars())
            if not victims:
                return 0
            s.execute(
                update(BacktestRun)
                .where(BacktestRun.run_id.in_(victims))
                .values(series_status="evicted")
            )
            s.execute(
                delete(BacktestRunSeries).where(BacktestRunSeries.run_id.in_(victims))
            )
        log.info("[run-ledger] retention swept %d series rows", len(victims))
        return len(victims)

    # -- reads ----------------------------------------------------------------

    def list_runs(
        self,
        *,
        strategy: Optional[str] = None,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        kind: Optional[str] = None,
        min_sharpe: Optional[float] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        stmt = select(BacktestRun)
        count = select(func.count()).select_from(BacktestRun)
        conds = []
        if strategy:
            conds.append(BacktestRun.strategy_id == strategy)
        if symbol:
            conds.append(BacktestRun.symbol == symbol.upper())
        if timeframe:
            conds.append(BacktestRun.timeframe == timeframe)
        if kind:
            conds.append(BacktestRun.kind == kind)
        if min_sharpe is not None:
            conds.append(BacktestRun.sharpe >= min_sharpe)
        for c in conds:
            stmt = stmt.where(c)
            count = count.where(c)
        stmt = stmt.order_by(BacktestRun.created_at.desc()).limit(limit).offset(offset)
        with self.db.session() as s:
            rows = [_row_dict(o, _LIST_COLUMNS) for o in s.execute(stmt).scalars()]
            total = s.execute(count).scalar() or 0
        return {"runs": rows, "total": total, "limit": limit, "offset": offset}

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        with self.db.session() as s:
            run = s.get(BacktestRun, run_id)
            if run is None:
                return None
            meta = _row_dict(run)
            series = s.get(BacktestRunSeries, run_id)
            series_body = _row_dict(series) if series is not None else None
        stored_version = meta["payload_version"]
        # A bumped PAYLOAD_VERSION is by convention a BREAKING shape change
        # (additive keys ride inside the stored blobs); so any mismatch
        # refuses rather than re-rendering a wrong chart.
        if stored_version != PAYLOAD_VERSION:
            raise PayloadVersionMismatch(
                f"stored payload_version={stored_version}, this build writes {PAYLOAD_VERSION}"
            )
        payload = {
            "config": meta.pop("config", None),
            "metrics": meta.pop("metrics", None),
            "cost_shock": meta.pop("cost_shock", None),
            "provenance": meta.pop("provenance", None),
            "readiness": meta.pop("readiness", None),
        }
        extras = (series_body or {}).get("extras") or {}
        payload.update(
            {
                "trades": (series_body or {}).get("trades"),
                "equity": (series_body or {}).get("equity"),
                "drawdown": (series_body or {}).get("drawdown"),
                "signals": (series_body or {}).get("signals"),
                "benchmark": extras.get("benchmark"),
                "monte_carlo": extras.get("monte_carlo"),
            }
        )
        return {
            "payload": payload,
            "ledger": {
                "run_id": meta["run_id"],
                "kind": meta["kind"],
                "series_status": meta["series_status"],
                "created_at": meta["created_at"],
                "data_source": meta["data_source"],
                "code_fingerprint": meta["code_fingerprint"],
                "config_hash": meta["config_hash"],
            },
        }

    def stats(self) -> dict[str, Any]:
        with self.db.session() as s:
            day_ago = _now().timestamp() - 86_400
            total = s.execute(select(func.count()).select_from(BacktestRun)).scalar() or 0
            last24 = s.execute(
                select(func.count())
                .select_from(BacktestRun)
                .where(BacktestRun.created_at >= datetime.fromtimestamp(day_ago, timezone.utc))
            ).scalar() or 0
            series_rows, series_bytes = s.execute(
                select(func.count(), func.coalesce(func.sum(BacktestRunSeries.bytes_written), 0)).select_from(
                    BacktestRunSeries
                )
            ).one()
            evicted = s.execute(
                select(func.count())
                .select_from(BacktestRun)
                .where(BacktestRun.series_status == "evicted")
            ).scalar() or 0
            failed = s.execute(
                select(func.count())
                .select_from(BacktestRun)
                .where(BacktestRun.series_status == "write_failed")
            ).scalar() or 0
        return {
            "total_runs": total,
            "last_24h": last24,
            "series_rows": series_rows,
            "series_bytes_total": int(series_bytes or 0),
            "series_evicted": evicted,
            "series_write_failed": failed,
            # Process-local (best effort by design — the ERROR log holds the
            # durable history; see PRD R8): last failure seen by THIS store.
            "last_persist_error": self.last_persist_error,
        }


_LIST_COLUMNS = [
    "run_id",
    "config_hash",
    "kind",
    "strategy_id",
    "symbol",
    "timeframe",
    "date_from",
    "date_to",
    "engine",
    "sharpe",
    "sortino",
    "calmar",
    "total_return",
    "cagr",
    "max_drawdown",
    "win_rate",
    "profit_factor",
    "total_trades",
    "series_status",
    "data_source",
    "readiness",
    "created_at",
]


def build_ledger() -> Optional[BacktestRunLedger]:
    """Attach to the configured database; ``None`` if unavailable.

    Mirrors the optimizer's optional-attachment pattern: the web app must
    still serve backtests with the ledger offline — the write path then
    reports ``persisted=false`` instead of pretending.
    """
    try:
        from backtest.db import DatabaseManager

        manager = DatabaseManager.from_env()
        manager.connect()
        ledger = BacktestRunLedger(manager)
        ledger.ensure_schema()
        log.info("[run-ledger] attached (%s)", manager.config.safe_url)
        return ledger
    except Exception:  # noqa: BLE001 - optional feature
        log.warning("[run-ledger] database unavailable — persistence disabled", exc_info=True)
        return None
