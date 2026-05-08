"""
Order monitor.

Polls Polymarket for the status of all open (placed) orders and
records confirmed fills into the database and position manager.

Polymarket GET /order/{id} response fields of interest:
  status:        "LIVE" | "MATCHED" | "DELAYED" | "CANCELLED" | "UNMATCHED"
  size_matched:  decimal string — total contracts filled so far
  price:         decimal string — fill price (or original limit price)

Status mapping:
  LIVE / DELAYED → still open, keep polling
  MATCHED        → fully filled
  UNMATCHED      → expired / zero fill (treat as cancelled)
  CANCELLED      → cancelled by user or system
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from src.polymarket.client import PolymarketClient
from src.storage.db import Database
from src.storage.models import Fill, Order
from src.strategy.position_manager import PositionManager

logger = logging.getLogger(__name__)

# Polymarket statuses that mean the order is done (no more fills expected)
_FILLED_STATUSES = {"MATCHED"}
_CANCELLED_STATUSES = {"CANCELLED", "UNMATCHED"}
_OPEN_STATUSES = {"LIVE", "DELAYED"}


class OrderMonitor:
    """
    Polls all placed orders and transitions them to filled/cancelled.

    Parameters
    ----------
    db                  Initialised Database.
    client              PolymarketClient (authenticated).
    position_manager    Optional PositionManager to record confirmed fills.
    """

    def __init__(
        self,
        db: Database,
        client: PolymarketClient,
        position_manager: Optional[PositionManager] = None,
    ) -> None:
        self._db = db
        self._client = client
        self._pm = position_manager

    # ------------------------------------------------------------------
    # Single check pass
    # ------------------------------------------------------------------

    def check_once(self) -> int:
        """
        Query status of every placed order.

        Returns the number of fills detected in this pass.
        """
        open_orders = self._db.get_open_orders()
        if not open_orders:
            return 0

        fills_detected = 0
        for order in open_orders:
            try:
                detected = self._check_order(order)
                if detected:
                    fills_detected += 1
            except Exception as exc:
                logger.error(
                    "check_once: error checking order id=%s poly_id=%s: %s",
                    order.id, order.poly_order_id, exc,
                )

        return fills_detected

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    def run(self, interval: float = 10.0) -> None:
        """
        Poll indefinitely at `interval` seconds.  Stop on KeyboardInterrupt.
        """
        logger.info("OrderMonitor started (interval=%.1fs)", interval)
        try:
            while True:
                n = self.check_once()
                if n:
                    logger.info("OrderMonitor: detected %d fill(s) this pass.", n)
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("OrderMonitor stopped.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_order(self, order: Order) -> bool:
        """
        Check a single order and act on status changes.

        Returns True if a fill was recorded.
        """
        data = self._client.get_order_status(order.poly_order_id)  # type: ignore[arg-type]
        if data is None:
            logger.warning("_check_order: no data for poly_id=%s", order.poly_order_id)
            return False

        poly_status = (data.get("status") or "").upper()

        if poly_status in _OPEN_STATUSES:
            logger.debug("Order poly_id=%s still open (status=%s)", order.poly_order_id, poly_status)
            return False

        if poly_status in _CANCELLED_STATUSES:
            logger.info("Order poly_id=%s cancelled (status=%s)", order.poly_order_id, poly_status)
            self._db.update_order_status(order.id, "cancelled")  # type: ignore[arg-type]
            return False

        if poly_status in _FILLED_STATUSES:
            return self._record_fill(order, data)

        # Unknown status — log and leave as placed
        logger.warning("Order poly_id=%s unknown status=%s", order.poly_order_id, poly_status)
        return False

    def _record_fill(self, order: Order, data: dict) -> bool:
        """Persist fill and update position manager. Returns True."""
        size_matched_raw = data.get("size_matched") or data.get("size_filled") or str(order.count)
        fill_price_raw = data.get("price") or str(order.price)

        try:
            size_matched = int(float(size_matched_raw))
            fill_price = float(fill_price_raw)
        except (ValueError, TypeError):
            size_matched = order.count
            fill_price = order.price

        logger.info(
            "Fill confirmed: poly_id=%s ticker=%s side=%s price=%.4f size=%d",
            order.poly_order_id, order.ticker, order.action, fill_price, size_matched,
        )

        # Update order status
        self._db.update_order_status(order.id, "filled")  # type: ignore[arg-type]

        # Persist fill record
        from datetime import datetime, timezone
        fill = Fill(
            order_id=order.id,  # type: ignore[arg-type]
            ticker=order.ticker,
            ts_utc=datetime.now(timezone.utc).isoformat(),
            side=order.side,
            count=size_matched,
            price=fill_price,
            fee_cents=0,  # Polymarket fee not returned in basic status; update if available
            poly_fill_id=data.get("id") or order.poly_order_id,
        )
        try:
            self._db.insert_fill(fill)
        except Exception as exc:
            logger.error("_record_fill: failed to insert fill: %s", exc)

        # Update position
        if self._pm is not None:
            decision = "BUY_YES" if order.action == "buy" else "SELL_YES"
            self._pm.record_fill(decision, fill_price, size_matched)

        return True
