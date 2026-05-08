"""
Polymarket WebSocket orderbook stream.

Connects to wss://ws-subscriptions-clob.polymarket.com/ws/market,
subscribes to orderbook updates for a single token_id (YES outcome token),
maintains local book state by applying snapshots and deltas,
and yields an updated OrderBook on every change.

Reconnects automatically with exponential backoff on any error.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Dict, List, Optional

import websockets

from src.polymarket.auth import PolymarketSigner
from src.polymarket.models import OrderBook, Quote

logger = logging.getLogger(__name__)

_RECONNECT_DELAYS = [1, 2, 4, 8, 16, 32, 60]  # seconds

_DEFAULT_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class _BookState:
    """
    Maintains the local orderbook for a single token_id.

    Polymarket sends:
      - "book" event (snapshot): full bids/asks arrays
      - "price_change" event (delta): array of changes with side + price + size
    """

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self._bids: Dict[float, int] = {}  # price -> size
        self._asks: Dict[float, int] = {}
        self.last_update_ts: Optional[datetime] = None

    def apply_snapshot(self, bids: List[Dict], asks: List[Dict]) -> None:
        self._bids.clear()
        self._asks.clear()
        for level in bids:
            p, s = float(level["price"]), int(float(level["size"]))
            if s > 0:
                self._bids[p] = s
        for level in asks:
            p, s = float(level["price"]), int(float(level["size"]))
            if s > 0:
                self._asks[p] = s
        self.last_update_ts = datetime.now(timezone.utc)

    def apply_delta(self, changes: List[Dict]) -> None:
        """
        Each change: {"price": "0.48", "size": "1000", "side": "BUY"}
        side "BUY" → bid side; "SELL" → ask side.
        size "0" means remove the level.
        """
        for change in changes:
            p = float(change["price"])
            s = int(float(change["size"]))
            side = change.get("side", "").upper()
            book = self._bids if side == "BUY" else self._asks
            if s == 0:
                book.pop(p, None)
            else:
                book[p] = s
        self.last_update_ts = datetime.now(timezone.utc)

    def to_orderbook(self) -> OrderBook:
        yes_bids = sorted(
            [Quote(price=p, size=q) for p, q in self._bids.items()],
            key=lambda x: -x.price,
        )
        yes_asks = sorted(
            [Quote(price=p, size=q) for p, q in self._asks.items()],
            key=lambda x: x.price,
        )
        return OrderBook(
            ticker=self.ticker,
            yes_bids=yes_bids,
            yes_asks=yes_asks,
            last_update_ts=self.last_update_ts,
        )


class WsStream:
    """
    Async generator that yields OrderBook updates for a single YES token_id.

    Usage::

        stream = WsStream(ticker="<yes_token_id>")
        async for ob in stream.stream():
            print(ob.best_yes_bid, ob.best_yes_ask)
    """

    def __init__(
        self,
        ticker: str,
        ws_url: str = _DEFAULT_WS_URL,
        signer: Optional[PolymarketSigner] = None,
        max_reconnect_attempts: int = 10,
    ) -> None:
        self.ticker = ticker
        self._ws_url = ws_url
        self._signer = signer
        self._max_reconnect_attempts = max_reconnect_attempts

    def _auth_headers(self) -> Dict[str, str]:
        if self._signer is None:
            return {}
        return self._signer.headers("GET", "/ws/market")

    def _subscribe_message(self) -> str:
        return json.dumps({
            "assets_ids": [self.ticker],
            "type": "Market",
        })

    async def stream(self) -> AsyncIterator[OrderBook]:
        state = _BookState(self.ticker)
        attempt = 0

        while True:
            try:
                extra_headers = self._auth_headers()
                logger.info(
                    "WS connecting to %s for ticker=%s (attempt %d)",
                    self._ws_url, self.ticker, attempt + 1,
                )
                async with websockets.connect(
                    self._ws_url,
                    additional_headers=extra_headers,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    attempt = 0

                    await ws.send(self._subscribe_message())
                    logger.info("WS subscribed for token_id=%s", self.ticker)

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning("WS received non-JSON frame: %r", raw)
                            continue

                        event_type = msg.get("event_type")

                        if event_type == "book":
                            state.apply_snapshot(
                                bids=msg.get("bids") or [],
                                asks=msg.get("asks") or [],
                            )
                            yield state.to_orderbook()

                        elif event_type == "price_change":
                            state.apply_delta(changes=msg.get("changes") or [])
                            yield state.to_orderbook()

                        else:
                            logger.debug("WS event_type=%s ignored", event_type)

            except asyncio.CancelledError:
                logger.info("WS stream cancelled for %s", self.ticker)
                return

            except Exception as exc:
                attempt += 1
                if attempt > self._max_reconnect_attempts:
                    logger.error(
                        "WS max reconnect attempts (%d) exceeded for %s",
                        self._max_reconnect_attempts, self.ticker,
                    )
                    raise

                delay = _RECONNECT_DELAYS[min(attempt - 1, len(_RECONNECT_DELAYS) - 1)]
                logger.warning(
                    "WS error for %s (attempt %d/%d): %s — reconnecting in %ds",
                    self.ticker, attempt, self._max_reconnect_attempts, exc, delay,
                )
                await asyncio.sleep(delay)
