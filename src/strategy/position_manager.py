"""
Position manager.

Tracks the net position in a single YES outcome token, enforces risk
limits, and persists state to SQLite after every fill.

Accounting
----------
- yes_count > 0: net long YES contracts
- yes_count < 0: net short YES contracts (short-selling)
- avg_cost: weighted-average fill price of the *current* open position
- realized_pnl: cumulative realised P&L from closed legs (in dollars,
  i.e. fractional USDC — multiply by contract face value if needed)

Risk limits
-----------
- max_long:   yes_count may not exceed this (None = unlimited)
- max_short:  yes_count may not go below -max_short (None = no shorting)
- max_loss:   halt if realized_pnl < -max_loss (None = no loss limit)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from src.storage.db import Database
from src.storage.models import Position

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PositionManager:
    """
    Single-ticker position tracker with optional risk limits.

    Parameters
    ----------
    db          Initialised Database instance.
    ticker      YES outcome token_id.
    max_long    Maximum net long contracts allowed (None = unlimited).
    max_short   Maximum net short contracts allowed (None = no shorting).
    max_loss    Halt trading if realized_pnl < -max_loss (None = disabled).
    """

    def __init__(
        self,
        db: Database,
        ticker: str,
        max_long: Optional[int] = None,
        max_short: Optional[int] = None,
        max_loss: Optional[float] = None,
    ) -> None:
        self._db = db
        self._ticker = ticker
        self._max_long = max_long
        self._max_short = max_short
        self._max_loss = max_loss

        # Load or initialise position from DB
        existing = db.get_position(ticker)
        if existing is not None:
            self._pos = existing
        else:
            self._pos = Position(
                ticker=ticker,
                ts_utc=_utcnow_iso(),
                yes_count=0,
                avg_cost=0.0,
                realized_pnl=0.0,
            )
            db.upsert_position(self._pos)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def yes_count(self) -> int:
        return self._pos.yes_count

    @property
    def avg_cost(self) -> float:
        return self._pos.avg_cost

    @property
    def realized_pnl(self) -> float:
        return self._pos.realized_pnl

    @property
    def position(self) -> Position:
        return self._pos

    def refresh(self) -> None:
        """Reload position from DB (e.g. after an external update)."""
        pos = self._db.get_position(self._ticker)
        if pos is not None:
            self._pos = pos

    def can_trade(self, decision: str, size: int) -> Tuple[bool, str]:
        """
        Check whether a proposed trade is within risk limits.

        Returns (allowed, reason_string).
        """
        # Loss limit
        if self._max_loss is not None and self._pos.realized_pnl < -self._max_loss:
            return False, f"max_loss_breach: realized_pnl={self._pos.realized_pnl:.4f} < -{self._max_loss}"

        if decision == "BUY_YES":
            new_count = self._pos.yes_count + size
            if self._max_long is not None and new_count > self._max_long:
                return False, (
                    f"max_long_breach: current={self._pos.yes_count} + size={size} "
                    f"= {new_count} > max_long={self._max_long}"
                )

        elif decision == "SELL_YES":
            new_count = self._pos.yes_count - size
            if self._max_short is not None and new_count < -self._max_short:
                return False, (
                    f"max_short_breach: current={self._pos.yes_count} - size={size} "
                    f"= {new_count} < -max_short={-self._max_short}"
                )

        return True, "ok"

    def record_fill(self, decision: str, fill_price: float, size: int) -> None:
        """
        Update position state after a confirmed fill and persist to DB.

        Accounting
        ----------
        BUY YES:
          - Weighted-average cost update for the long leg.
          - If previously short, first close the short (realise P&L on
            the closed portion), then open a long with the remainder.

        SELL YES:
          - If long, close up to yes_count contracts (realise P&L) then
            open a short with any remainder.
          - If flat or short, add to short position.
        """
        if decision == "BUY_YES":
            self._apply_buy(fill_price, size)
        elif decision == "SELL_YES":
            self._apply_sell(fill_price, size)
        else:
            logger.warning("record_fill: unknown decision=%s", decision)
            return

        self._pos.ts_utc = _utcnow_iso()
        try:
            self._db.upsert_position(self._pos)
        except Exception as exc:
            logger.error("record_fill: failed to persist position: %s", exc)

        logger.info(
            "Position updated: ticker=%s yes_count=%d avg_cost=%.4f realized_pnl=%.4f",
            self._ticker, self._pos.yes_count, self._pos.avg_cost, self._pos.realized_pnl,
        )

    # ------------------------------------------------------------------
    # Internal accounting helpers
    # ------------------------------------------------------------------

    def _apply_buy(self, price: float, size: int) -> None:
        current = self._pos.yes_count

        if current >= 0:
            # Flat or long — extend long
            new_count = current + size
            self._pos.avg_cost = (current * self._pos.avg_cost + size * price) / new_count
            self._pos.yes_count = new_count
        else:
            # Currently short — close short first, then open long
            close_size = min(size, -current)
            pnl_per_contract = self._pos.avg_cost - price  # avg_cost = short entry price
            self._pos.realized_pnl += pnl_per_contract * close_size
            remaining = size - close_size
            if remaining > 0:
                # Go long with leftover
                self._pos.yes_count = remaining
                self._pos.avg_cost = price
            else:
                self._pos.yes_count = current + size  # still short or flat
                if self._pos.yes_count == 0:
                    self._pos.avg_cost = 0.0

    def _apply_sell(self, price: float, size: int) -> None:
        current = self._pos.yes_count

        if current <= 0:
            # Flat or short — extend short
            new_count = current - size
            total_short = -current
            new_short = total_short + size
            self._pos.avg_cost = (total_short * self._pos.avg_cost + size * price) / new_short
            self._pos.yes_count = new_count
        else:
            # Currently long — close long first, then open short
            close_size = min(size, current)
            pnl_per_contract = price - self._pos.avg_cost
            self._pos.realized_pnl += pnl_per_contract * close_size
            remaining = size - close_size
            if remaining > 0:
                # Go short with leftover
                self._pos.yes_count = -remaining
                self._pos.avg_cost = price
            else:
                self._pos.yes_count = current - size  # still long or flat
                if self._pos.yes_count == 0:
                    self._pos.avg_cost = 0.0
