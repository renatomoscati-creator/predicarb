"""
Live tick collector.

Subscribes to the Polymarket WebSocket orderbook stream for a single
token_id, converts each OrderBook snapshot/delta into a Tick, and
persists it to:
  - SQLite (via TickWriter, always)
  - CSV file (optional, for later backtesting)

Runs until cancelled (KeyboardInterrupt / asyncio.CancelledError).
"""
from __future__ import annotations

import asyncio
import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.polymarket.auth import PolymarketSigner
from src.polymarket.models import OrderBook
from src.polymarket.ws_stream import WsStream
from src.storage.db import Database
from src.storage.models import Tick
from src.storage.writer import TickWriter

logger = logging.getLogger(__name__)

_CSV_HEADER = ["ts_utc", "yes_bid", "yes_ask", "yes_mid", "bid_size", "ask_size"]


def _ob_to_tick(ob: OrderBook) -> Tick:
    bid = ob.best_yes_bid
    ask = ob.best_yes_ask
    mid = (bid.price + ask.price) / 2.0 if bid and ask else None
    return Tick(
        ticker=ob.ticker,
        ts_utc=datetime.now(timezone.utc).isoformat(),
        yes_bid=bid.price if bid else None,
        yes_ask=ask.price if ask else None,
        yes_mid=mid,
        bid_size=bid.size if bid else None,
        ask_size=ask.size if ask else None,
    )


class TickCollector:
    """
    Async tick collector.

    Parameters
    ----------
    db          Initialised Database instance.
    ticker      YES outcome token_id to subscribe to.
    csv_path    If provided, ticks are also appended to this CSV file.
    signer      Optional PolymarketSigner for authenticated WS connections.
    ws_url      Override WebSocket URL (for testing).
    """

    def __init__(
        self,
        db: Database,
        ticker: str,
        csv_path: Optional[Path] = None,
        signer: Optional[PolymarketSigner] = None,
        ws_url: Optional[str] = None,
    ) -> None:
        self._db = db
        self._ticker = ticker
        self._csv_path = csv_path
        self._signer = signer
        self._ws_url = ws_url

    async def run(self) -> int:
        """
        Stream orderbook updates until cancelled.

        Returns the total number of ticks collected.
        """
        writer = TickWriter(self._db)
        writer.start()

        csv_file = None
        csv_writer = None
        if self._csv_path is not None:
            append = self._csv_path.exists()
            csv_file = self._csv_path.open("a" if append else "w", newline="")
            csv_writer = csv.DictWriter(csv_file, fieldnames=_CSV_HEADER)
            if not append:
                csv_writer.writeheader()
            logger.info("Writing ticks to CSV: %s (append=%s)", self._csv_path, append)

        ws_kwargs = {"ticker": self._ticker, "signer": self._signer}
        if self._ws_url is not None:
            ws_kwargs["ws_url"] = self._ws_url
        stream = WsStream(**ws_kwargs)

        count = 0
        try:
            async for ob in stream.stream():
                tick = _ob_to_tick(ob)
                writer.put(tick)

                if csv_writer is not None:
                    csv_writer.writerow({
                        "ts_utc": tick.ts_utc,
                        "yes_bid": tick.yes_bid if tick.yes_bid is not None else "",
                        "yes_ask": tick.yes_ask if tick.yes_ask is not None else "",
                        "yes_mid": tick.yes_mid if tick.yes_mid is not None else "",
                        "bid_size": tick.bid_size if tick.bid_size is not None else "",
                        "ask_size": tick.ask_size if tick.ask_size is not None else "",
                    })
                    if csv_file is not None:
                        csv_file.flush()

                count += 1
                logger.debug(
                    "Tick #%d: ticker=%s bid=%s ask=%s",
                    count, self._ticker, tick.yes_bid, tick.yes_ask,
                )

        except asyncio.CancelledError:
            logger.info("TickCollector cancelled after %d ticks.", count)
        finally:
            writer.stop()
            if csv_file is not None:
                csv_file.close()

        return count
