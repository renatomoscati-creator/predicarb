"""
Multi-market concurrent trading runner.

Runs a WsTradingRunner for each market in parallel using asyncio.gather.
Each market gets its own WebSocket connection, TradingRunner, and
PositionManager so they are fully isolated — a crash or block in one
market does not affect others.

Usage::

    runners = {
        "0xTOKEN_A": WsTradingRunner(runner_a, signer=signer),
        "0xTOKEN_B": WsTradingRunner(runner_b, signer=signer),
    }
    multi = MultiMarketRunner(runners)
    counts = asyncio.run(multi.run())
    # counts == {"0xTOKEN_A": <n_ticks>, "0xTOKEN_B": <n_ticks>}
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

from src.strategy.ws_runner import WsTradingRunner

logger = logging.getLogger(__name__)


class MultiMarketRunner:
    """
    Run N WsTradingRunners concurrently, one per market.

    Parameters
    ----------
    runners
        Mapping of {ticker: WsTradingRunner}.  Each runner is already
        configured with its own TradingRunner, benchmark, and filters.
    """

    def __init__(self, runners: Dict[str, WsTradingRunner]) -> None:
        self._runners = runners

    async def run(self, max_ticks_per_market: Optional[int] = None) -> Dict[str, int]:
        """
        Start all market runners concurrently and wait until all finish.

        Each runner streams indefinitely (or until max_ticks_per_market ticks).
        A runner that raises an exception is logged as an error; its tick count
        is recorded as 0 and the other markets continue unaffected.

        Parameters
        ----------
        max_ticks_per_market
            Optional cap on ticks per market.  Primarily for testing.

        Returns
        -------
        dict
            {ticker: tick_count} for every market.
        """
        if not self._runners:
            return {}

        tickers = list(self._runners.keys())
        logger.info("MultiMarketRunner starting %d market(s): %s", len(tickers), tickers)

        tasks = [
            self._runners[t].run(max_ticks=max_ticks_per_market)
            for t in tickers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        counts: Dict[str, int] = {}
        for ticker, result in zip(tickers, results):
            if isinstance(result, Exception):
                logger.error(
                    "MultiMarketRunner: market %s raised %s: %s",
                    ticker, type(result).__name__, result,
                )
                counts[ticker] = 0
            else:
                counts[ticker] = result  # type: ignore[assignment]

        logger.info("MultiMarketRunner finished. Tick counts: %s", counts)
        return counts
