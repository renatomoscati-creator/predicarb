"""
WebSocket-based live trading runner.

Consumes a WsStream and calls TradingRunner.step(ob) on every orderbook
update.  Lower latency than the REST polling loop — signal → order latency
drops from ~5 s (poll interval) to sub-second.

Usage::

    runner = TradingRunner(client=..., db=..., ...)
    ws = WsTradingRunner(runner=runner, signer=signer)
    asyncio.run(ws.run())
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from src.polymarket.auth import PolymarketSigner
from src.polymarket.ws_stream import WsStream, _DEFAULT_WS_URL
from src.strategy.runner import TradingRunner

logger = logging.getLogger(__name__)


class WsTradingRunner:
    """
    Real-time trading runner driven by a WebSocket orderbook stream.

    Parameters
    ----------
    runner      A fully configured TradingRunner (benchmark, filters, DB, etc.)
    signer      Optional credentials for authenticated WS connection.
    ws_url      WebSocket endpoint (defaults to Polymarket production URL).
    """

    def __init__(
        self,
        runner: TradingRunner,
        signer: Optional[PolymarketSigner] = None,
        ws_url: str = _DEFAULT_WS_URL,
    ) -> None:
        self._runner = runner
        self._ws = WsStream(
            ticker=runner._ticker,
            ws_url=ws_url,
            signer=signer,
        )

    async def run(self, max_ticks: Optional[int] = None) -> int:
        """
        Consume the WS stream and process each orderbook update.

        Parameters
        ----------
        max_ticks   Stop after this many ticks (None = run forever).
                    Primarily useful for testing.

        Returns
        -------
        int
            Number of ticks processed.
        """
        logger.info(
            "WsTradingRunner started: ticker=%s dry_run=%s",
            self._runner._ticker,
            self._runner._dry_run,
        )
        count = 0
        try:
            async for ob in self._ws.stream():
                self._runner.step(ob)
                count += 1
                if max_ticks is not None and count >= max_ticks:
                    break
        except asyncio.CancelledError:
            logger.info("WsTradingRunner cancelled after %d ticks.", count)
        except KeyboardInterrupt:
            logger.info("WsTradingRunner stopped by user after %d ticks.", count)
        return count
