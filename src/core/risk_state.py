"""Persistent risk state and kill switch.

Fail-closed contract: whenever risk state is untrusted — unreadable,
corrupt, unexpectedly missing, stale after a persistence failure, or
inconsistent with the exchange — new orders are blocked immediately.
The 3-consecutive-failure threshold (PERSIST_FAILURE_LATCH_THRESHOLD)
controls latching of the *persistent* kill only; it never permits
trading while state is untrusted.

Two kill representations, either one blocks:
- a `storage/KILL_SWITCH` file (operable when the database itself is
  the failure — always writable unless the disk is gone), and
- the `risk_state.kill_engaged` database flag (queryable/auditable).

Partial-failure order is deliberately file-then-database: whichever
write succeeds still blocks. Clearing requires the operator to clear
BOTH (see clear_kill()). Restarts and UTC date changes never clear a
kill switch.

Operator reset procedure (no automation does this):
1. Stop all trading loops.
2. Reconcile positions manually on the exchange.
3. Delete the KILL_SWITCH file AND call
   Database().clear_risk_kill(symbol, mode, day) — or the single
   clear_kill() helper below, which does both and reports what it did.
4. If state was missing/corrupt (not merely kill-engaged), call
   rebaseline() to write a fresh, reconciled row before restarting.
5. Restart the loop, which re-verifies everything from scratch.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.database import Database

logger = logging.getLogger(__name__)

KILL_SWITCH_PATH = "storage/KILL_SWITCH"

# Consecutive persistence-interaction failures before the kill is
# latched persistently. ANY failure blocks its own tick immediately;
# this threshold only decides when the block becomes a latched,
# operator-cleared kill.
PERSIST_FAILURE_LATCH_THRESHOLD = 3

# Reason codes — stable strings surfaced in logs and the kill file.
R_KILL_FILE = "KILL_FILE"
R_KILL_FLAG = "KILL_FLAG"
R_STATE_CORRUPT = "STATE_CORRUPT"
R_STATE_MISSING_UNEXPECTED = "STATE_MISSING_UNEXPECTED"
R_PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
R_UNVERIFIABLE = "UNVERIFIABLE"
R_POSITION_UNTRACKED = "POSITION_UNTRACKED"
R_COST_BASIS_UNKNOWN = "COST_BASIS_UNKNOWN"
R_UNATTRIBUTED_CLOSURE = "UNATTRIBUTED_CLOSURE"
R_POSITION_MISMATCH = "POSITION_MISMATCH"

_QTY_TOL_REL = 1e-9
_QTY_TOL_ABS = 1e-12


def utc_today() -> str:
    """Current UTC calendar date as YYYY-MM-DD (matches daily-loss semantics)."""
    return datetime.now(timezone.utc).date().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_kill_path() -> Path:
    """Default kill-switch file location (overridable per call for tests)."""
    return Path(KILL_SWITCH_PATH)


def read_kill_file(path: Path | str) -> str | None:
    """Reads the kill-switch file.

    Returns None when the file is absent (normal: not engaged), or the
    recorded reason when present (engaged — even an empty/unparseable
    file means engaged, with the raw content or "UNKNOWN" as reason).
    Raises OSError when the file cannot be stat'ed/read: callers must
    treat that as UNVERIFIABLE and block, never as disengaged.
    """
    p = Path(path)
    try:
        present = p.exists()
    except OSError:
        raise
    if not present:
        return None
    try:
        text = p.read_text(encoding="utf-8").strip()
    except OSError:
        raise
    if not text:
        return "UNKNOWN"
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text or "UNKNOWN"
    if isinstance(payload, dict) and payload.get("reason"):
        return str(payload["reason"])
    return text or "UNKNOWN"


def write_kill_file(
    path: Path | str,
    reason: str,
    symbol: str = "",
    mode: str = "",
    day: str = "",
) -> None:
    """Engages the file kill switch. Raises OSError on failure."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "reason": reason,
                "symbol": symbol,
                "mode": mode,
                "day": day,
                "engaged_at": _now_iso(),
            }
        ),
        encoding="utf-8",
    )


def remove_kill_file(path: Path | str) -> bool:
    """Removes the kill-switch file. Returns True if one existed."""
    p = Path(path)
    try:
        existed = p.exists()
    except OSError:
        raise
    if not existed:
        return False
    p.unlink()
    return True


def _coerce_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"corrupt risk_state row: {name}={value!r} is not a number.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"corrupt risk_state row: {name}={value!r} is not finite.")
    return result


@dataclass
class RiskState:
    """In-memory view of one (symbol, mode, day) risk row."""

    symbol: str
    mode: str
    day: str
    session_id: str
    realized_loss: float = 0.0
    open_qty: float = 0.0
    entry_price: float = 0.0
    entry_fee: float = 0.0
    basis_known: bool = False
    total_pnl: float = 0.0
    num_trades: int = 0
    kill_engaged: bool = False
    kill_reason: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "RiskState":
        """Validates and converts a database row; ValueError if corrupt."""
        try:
            state = cls(
                symbol=str(row["symbol"]),
                mode=str(row["mode"]),
                day=str(row["day"]),
                session_id=str(row["session_id"]),
                realized_loss=_coerce_float(
                    row.get("realized_loss", 0.0), "realized_loss"
                ),
                open_qty=_coerce_float(row.get("open_qty", 0.0), "open_qty"),
                entry_price=_coerce_float(row.get("entry_price", 0.0), "entry_price"),
                entry_fee=_coerce_float(row.get("entry_fee", 0.0), "entry_fee"),
                basis_known=bool(row.get("basis_known", 0)),
                total_pnl=_coerce_float(row.get("total_pnl", 0.0), "total_pnl"),
                num_trades=int(row.get("num_trades", 0)),
                kill_engaged=bool(row.get("kill_engaged", 0)),
                kill_reason=str(row.get("kill_reason") or ""),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"corrupt risk_state row: {e}") from e
        if state.realized_loss < 0.0:
            raise ValueError("corrupt risk_state row: negative realized_loss.")
        if state.open_qty < 0.0:
            raise ValueError("corrupt risk_state row: negative open_qty.")
        if state.entry_price < 0.0 or state.entry_fee < 0.0:
            raise ValueError("corrupt risk_state row: negative entry price/fee.")
        if state.num_trades < 0:
            raise ValueError("corrupt risk_state row: negative num_trades.")
        if state.kill_engaged and not state.kill_reason:
            raise ValueError("corrupt risk_state row: kill engaged without reason.")
        if state.open_qty > 0.0 and state.basis_known and state.entry_price <= 0.0:
            raise ValueError("corrupt risk_state row: known basis without entry price.")
        return state


def _has_session_today(
    db: Database, symbol: str, mode: str, day: str, exclude_session_id: str = ""
) -> bool:
    """True if the sessions table shows a same-symbol/mode session started
    today OTHER than the caller's own just-started session (which always
    exists by the time the gate runs — counting it would block every
    first run). Raises on database failure (caller treats as unverifiable)."""
    sessions = db.list_sessions(limit=500)
    return any(
        s.get("symbol") == symbol
        and s.get("mode") == mode
        and s.get("session_id") != exclude_session_id
        and str(s.get("started_at") or "").startswith(day)
        for s in sessions
    )


class RiskGate:
    """Fail-closed risk gate binding one trading loop to persisted state.

    Usage (both loops follow this exact shape):
        gate = RiskGate(db=db, kill_path=..., symbol=..., mode=..., session_id=...)
        if (reason := gate.startup()) is not None: abort trading, log reason, return
        # each tick, before any order path:
        if (reason := gate.check()) is not None: skip trading this tick
        # after each close / session end:
        gate.record(...) / gate.persist()  (failures counted; latch at 3)
    """

    def __init__(
        self,
        db: Database,
        kill_path: Path | str | None,
        symbol: str,
        mode: str,
        session_id: str,
        day: str | None = None,
    ) -> None:
        self._db = db
        self._kill_path = (
            Path(kill_path) if kill_path is not None else default_kill_path()
        )
        self._state = RiskState(
            symbol=symbol, mode=mode, day=day or utc_today(), session_id=session_id
        )
        self._initialized = False
        self._failures = 0
        self._latched: str | None = None

    @property
    def state(self) -> RiskState:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    # ── Startup ──

    def startup(self) -> str | None:
        """Establishes the trusted baseline. Returns a block reason, or
        None when trading may proceed. Never raises: every failure mode
        maps to a block reason (fail closed)."""
        try:
            file_reason = read_kill_file(self._kill_path)
        except OSError as e:
            logger.error("Kill file unreadable (%s) — blocking: %s", self._kill_path, e)
            return R_UNVERIFIABLE
        if file_reason is not None:
            self._latched = R_KILL_FILE
            logger.error("Kill file engaged (%s) — blocking.", file_reason)
            return R_KILL_FILE

        try:
            row = self._db.load_risk_state(
                self._state.symbol, self._state.mode, self._state.day
            )
        except Exception as e:
            logger.error("Risk state unreadable — blocking: %s", e)
            return R_UNVERIFIABLE

        if row is None:
            try:
                evidence = _has_session_today(
                    self._db,
                    self._state.symbol,
                    self._state.mode,
                    self._state.day,
                    exclude_session_id=self._state.session_id,
                )
            except Exception as e:
                logger.error("Session evidence unreadable — blocking: %s", e)
                return R_UNVERIFIABLE
            if evidence:
                logger.error(
                    "Session started today has no risk_state row — blocking (%s).",
                    R_STATE_MISSING_UNEXPECTED,
                )
                return R_STATE_MISSING_UNEXPECTED
            try:
                prior = self._db.load_latest_risk_state_before(
                    self._state.symbol, self._state.mode, self._state.day
                )
            except Exception as e:
                logger.error("Prior-day risk lookup failed — blocking: %s", e)
                return R_UNVERIFIABLE
            if prior is None:
                logger.info(
                    "No prior risk state: verified first run, writing baseline."
                )
                self._initialized = True
                if not self.persist():
                    self.engage(R_PERSISTENCE_FAILED)
                    return R_PERSISTENCE_FAILED
                return None
            try:
                prior_state = RiskState.from_row(prior)
            except ValueError as e:
                logger.error("Prior-day risk state corrupt — blocking: %s", e)
                return R_STATE_CORRUPT
            # Carry-forward: adopt the prior row's position intent (and any
            # latched kill — kills survive rollover) into today. Counters
            # mirror a no-restart rollover exactly: daily loss resets to 0
            # per UTC semantics, totals carry. The unchanged reconcile
            # matrix in the runner still verifies everything against the
            # exchange before a single order; nothing here approves trading.
            logger.warning(
                "Carrying forward prior-day risk state from %s "
                "(open_qty=%s, kill=%s) — reconcile must still accept it.",
                prior_state.day,
                prior_state.open_qty,
                prior_state.kill_engaged,
            )
            self._state.open_qty = prior_state.open_qty
            self._state.entry_price = prior_state.entry_price
            self._state.entry_fee = prior_state.entry_fee
            self._state.basis_known = prior_state.basis_known
            self._state.realized_loss = 0.0
            self._state.total_pnl = prior_state.total_pnl
            self._state.num_trades = prior_state.num_trades
            self._state.kill_engaged = prior_state.kill_engaged
            self._state.kill_reason = prior_state.kill_reason
            self._initialized = True
            if not self.persist():
                self.engage(R_PERSISTENCE_FAILED)
                return R_PERSISTENCE_FAILED
            if self._state.kill_engaged:
                logger.error(
                    "Carried kill flag still engaged (%s) — blocking.",
                    self._state.kill_reason,
                )
                self._latched = R_KILL_FLAG
                return R_KILL_FLAG
            return None

        try:
            self._state = RiskState.from_row(row)
        except ValueError as e:
            logger.error("Risk state corrupt — blocking: %s", e)
            return R_STATE_CORRUPT
        if self._state.kill_engaged:
            self._latched = R_KILL_FLAG
            logger.error(
                "Database kill flag engaged (%s) — blocking.", self._state.kill_reason
            )
            return R_KILL_FLAG
        self._initialized = True
        logger.info(
            "Risk state resumed: day=%s loss=%.4f trades=%d open_qty=%s",
            self._state.day,
            self._state.realized_loss,
            self._state.num_trades,
            self._state.open_qty,
        )
        return None

    # ── Per-tick gate ──

    def check(self) -> str | None:
        """Re-verifies kill representations and state freshness for one tick.
        Returns a block reason, or None when the tick may trade."""
        if self._latched is not None:
            self._heal_latch()
            return self._latched
        today = utc_today()
        if self._initialized and today != self._state.day:
            self._state.day = today
            self._state.realized_loss = 0.0
            if not self.persist():
                # persist() already counted the failure and latched at
                # threshold: report the latch, else a single-failure block.
                return self._latched or R_UNVERIFIABLE
        try:
            file_reason = read_kill_file(self._kill_path)
        except OSError as e:
            logger.error("Kill file unreadable mid-run — blocking: %s", e)
            return R_UNVERIFIABLE
        if file_reason is not None:
            self._latched = R_KILL_FILE
            return R_KILL_FILE
        try:
            row = self._db.load_risk_state(
                self._state.symbol, self._state.mode, self._state.day
            )
        except Exception as e:
            return self._note_failed(f"risk flag re-read failed: {e}")
        if row is not None:
            try:
                flag = RiskState.from_row(row)
            except ValueError as e:
                return self._note_failed(f"risk row turned corrupt mid-run: {e}")
            if flag.kill_engaged:
                self._latched = R_KILL_FLAG
                return R_KILL_FLAG
        if self._failures > 0:
            return R_UNVERIFIABLE
        return None

    # ── Persistence with failure counting ──

    def persist(self) -> bool:
        """Writes current state; counts failures, latches at threshold."""
        try:
            self._db.save_risk_state(
                symbol=self._state.symbol,
                mode=self._state.mode,
                day=self._state.day,
                session_id=self._state.session_id,
                realized_loss=self._state.realized_loss,
                open_qty=self._state.open_qty,
                entry_price=self._state.entry_price,
                entry_fee=self._state.entry_fee,
                basis_known=self._state.basis_known,
                total_pnl=self._state.total_pnl,
                num_trades=self._state.num_trades,
                kill_engaged=self._state.kill_engaged,
                kill_reason=self._state.kill_reason or None,
            )
        except Exception as e:
            self._note_failed(f"risk persist failed: {e}")
            return False
        self._failures = 0
        return True

    def _note_failed(self, what: str) -> str:
        self._failures += 1
        logger.error("%s (consecutive failures: %d).", what, self._failures)
        if self._failures >= PERSIST_FAILURE_LATCH_THRESHOLD:
            self.engage(R_PERSISTENCE_FAILED)
            return R_PERSISTENCE_FAILED
        return R_UNVERIFIABLE

    def record(
        self,
        *,
        loss_add: float,
        open_qty: float,
        entry_price: float,
        entry_fee: float,
        basis_known: bool,
        total_pnl: float,
        num_trades: int,
    ) -> bool:
        """Records position/counter state (opens and closes) and persists.
        Returns False when persistence failed — the caller must treat
        state as stale (the next check() blocks until a persist
        succeeds)."""
        self._state.realized_loss += loss_add
        self._state.open_qty = open_qty
        self._state.entry_price = entry_price
        self._state.entry_fee = entry_fee
        self._state.basis_known = basis_known
        self._state.total_pnl = total_pnl
        self._state.num_trades = num_trades
        return self.persist()

    def _heal_latch(self) -> None:
        """Best-effort re-persist of a latched kill, called on every tick
        while latched. Rewrites whichever representation is missing or
        unverifiable and logs loudly (CRITICAL) on every failed retry.

        This narrows — but cannot eliminate — the dual-outage window: if
        both stores stay down, nothing durable records the latch, and a
        restart can only rely on startup failing closed on the still-dead
        stores. If both stores recover silently and the process restarts
        with no operator awareness, the latch is lost — durable stores
        are the only real guarantee, and this design does not claim
        otherwise. Healing never clears, never permits, never raises."""
        self._heal_attempts = getattr(self, "_heal_attempts", 0) + 1
        try:
            file_reason: str | None = read_kill_file(self._kill_path)
        except OSError as e:
            logger.critical(
                "Latched kill (%s) file unverifiable, retrying write "
                "(heal attempt %d): %s",
                self._latched,
                self._heal_attempts,
                e,
            )
            file_reason = None
        if file_reason is None:
            try:
                write_kill_file(
                    self._kill_path,
                    str(self._latched),
                    symbol=self._state.symbol,
                    mode=self._state.mode,
                    day=self._state.day,
                )
                logger.warning("Latched kill re-persisted to file.")
            except OSError as e:
                logger.critical(
                    "Latched kill file rewrite failed (heal attempt %d): %s",
                    self._heal_attempts,
                    e,
                )
        try:
            row = self._db.load_risk_state(
                self._state.symbol, self._state.mode, self._state.day
            )
        except Exception as e:
            logger.critical(
                "Latched kill flag unreadable, cannot verify (heal attempt %d): %s",
                self._heal_attempts,
                e,
            )
            return
        flagged = False
        if row is not None:
            try:
                flagged = RiskState.from_row(row).kill_engaged
            except ValueError:
                flagged = False
        if not flagged:
            try:
                self._db.save_risk_state(
                    symbol=self._state.symbol,
                    mode=self._state.mode,
                    day=self._state.day,
                    session_id=self._state.session_id,
                    realized_loss=self._state.realized_loss,
                    open_qty=self._state.open_qty,
                    entry_price=self._state.entry_price,
                    entry_fee=self._state.entry_fee,
                    basis_known=self._state.basis_known,
                    total_pnl=self._state.total_pnl,
                    num_trades=self._state.num_trades,
                    kill_engaged=True,
                    kill_reason=str(self._latched),
                )
                logger.warning("Latched kill re-persisted to database flag.")
            except Exception as e:
                logger.critical(
                    "Latched kill flag rewrite failed (heal attempt %d): %s",
                    self._heal_attempts,
                    e,
                )

    # ── Kill switch ──

    def engage(self, reason: str) -> None:
        """Latches the kill in memory and best-effort persists both
        representations (file first, then database). Blocking never
        depends on those writes succeeding — the latch itself blocks."""
        self._latched = reason
        self._state.kill_engaged = True
        self._state.kill_reason = reason
        try:
            write_kill_file(
                self._kill_path,
                reason,
                symbol=self._state.symbol,
                mode=self._state.mode,
                day=self._state.day,
            )
        except OSError as e:
            logger.error("Kill file write failed (still latched locally): %s", e)
        try:
            self._db.save_risk_state(
                symbol=self._state.symbol,
                mode=self._state.mode,
                day=self._state.day,
                session_id=self._state.session_id,
                realized_loss=self._state.realized_loss,
                open_qty=self._state.open_qty,
                entry_price=self._state.entry_price,
                entry_fee=self._state.entry_fee,
                basis_known=self._state.basis_known,
                total_pnl=self._state.total_pnl,
                num_trades=self._state.num_trades,
                kill_engaged=True,
                kill_reason=reason,
            )
        except Exception as e:
            logger.error("Kill flag write failed (still latched locally): %s", e)
        logger.error("Kill switch engaged (%s) — new orders blocked.", reason)

    def clear(self) -> dict[str, bool]:
        """Operator-only reset: clears both representations and the local
        latch. The caller must have reconciled positions manually first.
        Restarts never call this."""
        try:
            file_removed = remove_kill_file(self._kill_path)
        except OSError as e:
            logger.error("Kill file removal failed: %s", e)
            raise
        try:
            self._db.clear_risk_kill(
                self._state.symbol, self._state.mode, self._state.day
            )
            db_cleared = True
        except Exception as e:
            logger.error("Kill flag clear failed: %s", e)
            raise
        self._latched = None
        self._failures = 0
        self._state.kill_engaged = False
        self._state.kill_reason = ""
        return {"file_removed": file_removed, "db_cleared": db_cleared}

    def rebaseline(
        self,
        *,
        realized_loss: float = 0.0,
        open_qty: float = 0.0,
        entry_price: float = 0.0,
        entry_fee: float = 0.0,
        basis_known: bool = False,
        total_pnl: float = 0.0,
        num_trades: int = 0,
    ) -> None:
        """Operator-only: writes a fresh reconciled row (kill cleared).
        Used after STATE_MISSING_UNEXPECTED / STATE_CORRUPT / divergence
        blocks, once the operator has reconciled manually. Raises on
        database failure (a baseline that cannot be written is not a
        baseline)."""
        self.clear()
        self._state.realized_loss = realized_loss
        self._state.open_qty = open_qty
        self._state.entry_price = entry_price
        self._state.entry_fee = entry_fee
        self._state.basis_known = basis_known
        self._state.total_pnl = total_pnl
        self._state.num_trades = num_trades
        self._state.day = utc_today()
        self._initialized = True
        if not self.persist():
            self.engage(R_PERSISTENCE_FAILED)
            raise RuntimeError("rebaseline persist failed — kill latched instead.")

    # ── Exchange reconciliation (live only) ──

    def reconcile_exchange(self, exchange_qty: float) -> str | None:
        """Compares the authoritative exchange quantity against persisted
        intent. Returns a block reason, or None when consistent. Never
        invents a cost basis: unknown entry data blocks."""
        if not math.isfinite(exchange_qty):
            return R_UNVERIFIABLE
        exchange_abs = abs(exchange_qty)
        intent = self._state.open_qty
        if exchange_abs == 0.0 and intent == 0.0:
            return None
        if exchange_abs == 0.0:
            return R_UNATTRIBUTED_CLOSURE
        if intent == 0.0:
            return R_POSITION_UNTRACKED
        if not math.isclose(
            exchange_abs, intent, rel_tol=_QTY_TOL_REL, abs_tol=_QTY_TOL_ABS
        ):
            return R_POSITION_MISMATCH
        if not self._state.basis_known or self._state.entry_price <= 0.0:
            return R_COST_BASIS_UNKNOWN
        return None
