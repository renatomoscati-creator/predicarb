"""
Live trading runner.

Polls the orderbook, computes edge, persists ticks/signals, and places
limit orders when the signal passes all filters and edge exceeds the
minimum threshold.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from src.benchmark.csv_benchmark import BenchmarkProvider
from src.polymarket.client import PolymarketClient
from src.polymarket.models import OrderBook
from src.storage.db import Database
from src.storage.models import Order, Signal, Tick
from src.strategy.edge_calculator import EdgeFilters, EdgeResult, compute_edge
from src.strategy.position_manager import PositionManager

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TradingRunner:
    """
    Single-market live trading loop.

    Parameters
    ----------
    client              PolymarketClient instance.
    db                  Database instance (already initialised).
    benchmark           BenchmarkProvider that returns a probability in [0, 1].
    filters             EdgeFilters for spread / depth / staleness checks.
    ticker              YES outcome token_id to trade.
    size                Number of contracts per order.
    min_edge            Minimum |edge| required to place a trade (default 0.02).
    dry_run             If True, log trade decisions without sending orders.
    position_manager    Optional PositionManager for risk limit enforcement.
                        If None, no position limits are applied.
    """

    def __init__(
        self,
        client: PolymarketClient,
        db: Database,
        benchmark: BenchmarkProvider,
        filters: EdgeFilters,
        ticker: str,
        size: int,
        min_edge: float = 0.02,
        dry_run: bool = False,
        position_manager: Optional[PositionManager] = None,
    ) -> None:
        self._client = client
        self._db = db
        self._benchmark = benchmark
        self._filters = filters
        self._ticker = ticker
        self._size = size
        self._min_edge = min_edge
        self._dry_run = dry_run
        self._pm = position_manager

    # ------------------------------------------------------------------
    # Single iteration
    # ------------------------------------------------------------------

    def step(self, ob: Optional[OrderBook] = None) -> Optional[EdgeResult]:
        """
        Fetch orderbook → compute edge → persist tick + signal → trade.

        If ob is provided (e.g. from WsTradingRunner), skips the REST fetch.
        Returns the EdgeResult (always), or None if the orderbook fetch fails.
        """
        if ob is None:
            try:
                ob = self._client.get_orderbook(self._ticker)
            except Exception as exc:
                logger.error("step: orderbook fetch failed for %s: %s", self._ticker, exc)
                return None

        # Persist tick
        bid = ob.best_yes_bid
        ask = ob.best_yes_ask
        mid = (bid.price + ask.price) / 2.0 if bid and ask else None
        tick = Tick(
            ticker=self._ticker,
            ts_utc=_utcnow_iso(),
            yes_bid=bid.price if bid else None,
            yes_ask=ask.price if ask else None,
            yes_mid=mid,
            bid_size=bid.size if bid else None,
            ask_size=ask.size if ask else None,
        )
        try:
            self._db.insert_tick(tick)
        except Exception as exc:
            logger.warning("step: failed to insert tick: %s", exc)

        # Compute edge
        result = compute_edge(orderbook=ob, benchmark=self._benchmark, filters=self._filters)

        # Determine decision
        decision = self._decide(result)

        # Persist signal
        sig = Signal(
            ticker=self._ticker,
            ts_utc=_utcnow_iso(),
            benchmark_prob=result.benchmark_prob,
            market_mid=result.market_mid,
            edge=result.edge,
            passed_filters=result.passed_filters,
            reason=result.reason,
            decision=decision,
        )
        try:
            sig_id = self._db.insert_signal(sig)
        except Exception as exc:
            logger.warning("step: failed to insert signal: %s", exc)
            sig_id = None

        logger.info(
            "ticker=%s mid=%.4f benchmark=%.4f edge=%s filters=%s decision=%s",
            self._ticker,
            result.market_mid or 0.0,
            result.benchmark_prob,
            f"{result.edge:.4f}" if result.edge is not None else "n/a",
            result.passed_filters,
            decision,
        )

        if decision in ("BUY_YES", "SELL_YES"):
            self._execute(decision, result, sig_id)

        return result

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    def run(self, interval: float = 5.0) -> None:
        """
        Poll indefinitely at `interval` seconds.  Stop on KeyboardInterrupt.
        """
        logger.info(
            "TradingRunner started: ticker=%s size=%d min_edge=%.4f dry_run=%s interval=%.1fs",
            self._ticker, self._size, self._min_edge, self._dry_run, interval,
        )
        try:
            while True:
                self.step()
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("TradingRunner stopped by user.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _decide(self, result: EdgeResult) -> str:
        if not result.passed_filters or result.edge is None:
            return "NO_TRADE"
        if abs(result.edge) < self._min_edge:
            return "NO_TRADE"
        if result.edge > 0:
            return "BUY_YES"
        if result.edge < 0:
            return "SELL_YES"
        return "NO_TRADE"

    def _execute(self, decision: str, result: EdgeResult, sig_id: Optional[int]) -> None:
        # --- Position / risk check -------------------------------------------
        if self._pm is not None:
            allowed, reason = self._pm.can_trade(decision, self._size)
            if not allowed:
                logger.info("Trade blocked by position manager: %s", reason)
                return

        side = "BUY" if decision == "BUY_YES" else "SELL"
        # Use the best available price (bid for sells, ask for buys)
        price = result.market_mid  # fallback to mid; client will use limit
        if price is None:
            logger.warning("_execute: no price available, skipping.")
            return

        ts = _utcnow_iso()

        order = Order(
            ticker=self._ticker,
            ts_utc=ts,
            side="yes",
            action=side.lower(),
            count=self._size,
            price=price,
            order_type="limit",
            status="pending",
            signal_id=sig_id,
        )

        try:
            order_id = self._db.insert_order(order)
        except Exception as exc:
            logger.error("_execute: failed to insert order record: %s", exc)
            order_id = None

        if self._dry_run:
            logger.info(
                "DRY RUN — would place %s order: ticker=%s price=%.4f size=%d",
                side, self._ticker, price, self._size,
            )
            if order_id is not None:
                try:
                    self._db.update_order_status(order_id, "dry_run")
                except Exception:
                    pass
            # Record simulated fill so position limits are exercised in dry-run too
            if self._pm is not None:
                self._pm.record_fill(decision, price, self._size)
            return

        try:
            result_order = self._client.place_order(
                token_id=self._ticker,
                side=side,
                price=price,
                size=self._size,
            )
        except Exception as exc:
            logger.error("_execute: place_order raised: %s", exc)
            if order_id is not None:
                try:
                    self._db.update_order_status(order_id, "error")
                except Exception:
                    pass
            return

        new_status = "placed" if result_order.success else "rejected"
        if order_id is not None:
            try:
                self._db.update_order_status(
                    order_id, new_status, poly_order_id=result_order.poly_order_id
                )
            except Exception as exc:
                logger.warning("_execute: failed to update order status: %s", exc)

        logger.info(
            "Order %s: poly_id=%s side=%s price=%.4f size=%d message=%s",
            new_status,
            result_order.poly_order_id or "n/a",
            side,
            price,
            self._size,
            result_order.message or "",
        )
        # Position update for live orders is deferred to OrderMonitor,
        # which polls get_order_status() until the order actually fills.
