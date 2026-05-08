import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.config import get_settings
from src.polymarket.client import PolymarketClient
from src.logging_config import configure_logging, get_logger
from src.strategy.edge_calculator import EdgeFilters, compute_edge


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def cmd_health(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.logs_dir, settings.log_level)
    logger = get_logger(__name__)

    logger.info("Running health check for env=%s", settings.env)

    client = PolymarketClient(settings)
    health = client.get_health()
    account = client.get_account_summary()

    print(f"Environment : {settings.env}")
    print(f"API base   : {settings.api_base_url}")
    print(f"Connectivity: {'OK' if health.ok else 'ERROR'}")
    print(f"Latency    : {health.latency_ms:.1f} ms")

    if account.balance_cents is not None:
        balance_dollars = account.balance_cents / 100.0
        print(f"Balance    : ${balance_dollars:,.2f}")
    else:
        print("Balance    : unavailable (unauthenticated or error)")

    if not health.ok:
        logger.error("Health check failed: %s", health.message)
        return 1
    return 0


def cmd_list_markets(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.logs_dir, settings.log_level)
    logger = get_logger(__name__)

    client = PolymarketClient(settings)
    keywords = args.keywords or []
    limit = args.limit
    event_ticker = getattr(args, "event_ticker", None)
    series_ticker = getattr(args, "series_ticker", None)

    logger.info(
        "Listing markets with keywords=%s event_ticker=%s series_ticker=%s limit=%s",
        keywords, event_ticker, series_ticker, limit,
    )

    try:
        markets, latency_ms = client.get_markets(
            keywords=keywords,
            limit=limit,
            event_ticker=event_ticker,
            series_ticker=series_ticker,
        )
    except Exception as exc:
        logger.exception("Failed to list markets: %s", exc)
        print(f"Error fetching markets: {exc}", file=sys.stderr)
        return 1

    print(f"# Markets (limit={limit}, latency={latency_ms:.1f} ms)")
    header = f"{'Ticker':<66} {'YesBid':>7} {'YesAsk':>7} {'Vol24h':>10}  Title"
    print(header)
    print("-" * len(header))

    for m in markets:
        yes_bid_str = f"{m.yes_bid:.2f}" if m.yes_bid is not None else "-"
        yes_ask_str = f"{m.yes_ask:.2f}" if m.yes_ask is not None else "-"
        vol_str = f"{m.volume_24h:d}" if m.volume_24h is not None else "-"
        print(
            f"{m.ticker}  {yes_bid_str:>7} {yes_ask_str:>7} {vol_str:>10}  {m.title}"
        )

    if not markets:
        print("No markets matched the given keywords.")

    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.logs_dir, settings.log_level)
    logger = get_logger(__name__)

    client = PolymarketClient(settings)
    ticker = args.ticker
    interval = args.interval

    logger.info("Watching ticker=%s interval=%s", ticker, interval)

    try:
        while True:
            try:
                ob = client.get_orderbook(ticker)
            except Exception as exc:
                logger.exception("Failed to fetch orderbook: %s", exc)
                print(f"Error fetching orderbook: {exc}", file=sys.stderr)
                time.sleep(interval)
                continue

            bid = ob.best_yes_bid
            ask = ob.best_yes_ask

            bid_str = f"{bid.price:.2f}" if bid else "-"
            ask_str = f"{ask.price:.2f}" if ask else "-"

            mid_prob_str = "-"
            if bid and ask:
                mid = (bid.price + ask.price) / 2.0
                mid_prob_str = f"{mid:.2f}"

            now = _utcnow()
            if ob.last_update_ts:
                age_sec = (now - ob.last_update_ts).total_seconds()
                age_str = f"{age_sec:.1f}s"
            else:
                age_str = "n/a"

            print(
                f"{now.isoformat()}Z Ticker={ticker} "
                f"Bid={bid_str} Ask={ask_str} MidProb={mid_prob_str} Age={age_str}"
            )

            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("Watch interrupted by user.")
        print("\nStopped watching.")
        return 0


def cmd_signal(args: argparse.Namespace) -> int:
    from src.benchmark.csv_benchmark import ConstantBenchmark
    settings = get_settings()
    configure_logging(settings.logs_dir, settings.log_level)
    logger = get_logger(__name__)

    client = PolymarketClient(settings)
    ticker = args.ticker
    benchmark_prob = args.benchmark

    logger.info("Computing signal for ticker=%s benchmark=%s", ticker, benchmark_prob)

    try:
        ob = client.get_orderbook(ticker)
    except Exception as exc:
        logger.exception("Failed to fetch orderbook: %s", exc)
        print(f"Error fetching orderbook: {exc}", file=sys.stderr)
        return 1

    filters = EdgeFilters(
        max_spread=args.max_spread,
        min_depth=args.min_depth,
        max_staleness_seconds=args.max_staleness,
    )
    result = compute_edge(orderbook=ob, benchmark=ConstantBenchmark(benchmark_prob), filters=filters)

    print(f"Ticker          : {ticker}")
    print(f"Benchmark prob  : {result.benchmark_prob:.4f}")

    if result.market_mid is None or result.edge is None:
        print("Market mid prob : n/a (missing side)")
        print("Edge            : n/a")
        print(f"Filters passed  : {result.passed_filters} ({result.reason})")
        print("Decision        : NO TRADE")
        return 0

    print(f"Market mid prob : {result.market_mid:.4f}")
    print(f"Edge            : {result.edge:.4f}")
    print(f"Filters passed  : {result.passed_filters} ({result.reason})")

    if not result.passed_filters:
        decision = "NO TRADE"
    else:
        if result.edge > 0:
            decision = "TRADE: BUY YES"
        elif result.edge < 0:
            decision = "TRADE: SELL YES"
        else:
            decision = "NO TRADE"

    print(f"Decision        : {decision}")
    return 0


def cmd_trade(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.logs_dir, settings.log_level)
    logger = get_logger(__name__)

    client = PolymarketClient(settings)
    ticker = args.ticker
    side = args.side.upper()
    price = args.price
    size = args.size
    tif = args.tif.upper()

    logger.info(
        "Placing order: ticker=%s side=%s price=%s size=%s tif=%s",
        ticker, side, price, size, tif,
    )

    result = client.place_order(
        token_id=ticker,
        side=side,
        price=price,
        size=size,
        time_in_force=tif,
    )

    print(f"Success       : {result.success}")
    print(f"Order ID      : {result.poly_order_id or 'n/a'}")
    print(f"Status        : {result.status}")
    if result.message:
        print(f"Message       : {result.message}")

    return 0 if result.success else 1


def cmd_positions(args: argparse.Namespace) -> int:
    from src.storage.db import Database

    settings = get_settings()
    configure_logging(settings.logs_dir, settings.log_level)

    db = Database(settings.data_dir / "polymarket_bot.sqlite")
    db.init()

    positions = db.get_all_positions()
    if not positions:
        print("No positions found.")
        return 0

    header = f"{'Ticker':<20} {'Net':>6} {'AvgCost':>8} {'RealPnL':>10}  {'Updated'}"
    print(header)
    print("-" * len(header))
    for p in positions:
        print(
            f"{p.ticker:<20} {p.yes_count:>6} {p.avg_cost:>8.4f} "
            f"{p.realized_pnl:>+10.4f}  {p.ts_utc}"
        )
    return 0


def cmd_orders(args: argparse.Namespace) -> int:
    from src.storage.db import Database

    settings = get_settings()
    configure_logging(settings.logs_dir, settings.log_level)

    db = Database(settings.data_dir / "polymarket_bot.sqlite")
    db.init()

    status_filter = args.status or None
    orders = db.get_recent_orders(limit=args.limit, status=status_filter)
    if not orders:
        print("No orders found.")
        return 0

    header = (
        f"{'ID':>5} {'Ticker':<20} {'Side':<5} {'Act':<5} "
        f"{'Cnt':>5} {'Price':>7} {'Status':<12} {'PolyID':<36}  Timestamp"
    )
    print(header)
    print("-" * len(header))
    for o in orders:
        print(
            f"{o.id:>5} {o.ticker:<20} {o.side:<5} {o.action:<5} "
            f"{o.count:>5} {o.price:>7.4f} {o.status:<12} "
            f"{(o.poly_order_id or 'n/a'):<36}  {o.ts_utc}"
        )
    return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    from src.storage.db import Database
    from src.strategy.order_monitor import OrderMonitor
    from src.strategy.position_manager import PositionManager

    settings = get_settings()
    configure_logging(settings.logs_dir, settings.log_level)
    logger = get_logger(__name__)

    db = Database(settings.data_dir / "polymarket_bot.sqlite")
    db.init()

    client = PolymarketClient(settings)

    # Build per-ticker position managers for all open orders
    open_orders = db.get_open_orders()
    tickers = {o.ticker for o in open_orders}
    if not tickers:
        print("No open orders to monitor.")
        return 0

    print(f"Monitoring {len(open_orders)} open order(s) across {len(tickers)} ticker(s).")

    # Use a single position manager per ticker; run_once if --once flag
    pms = {t: PositionManager(db=db, ticker=t) for t in tickers}

    def _make_monitor(ticker: str) -> OrderMonitor:
        return OrderMonitor(db=db, client=client, position_manager=pms[ticker])

    if args.once:
        total = 0
        for ticker in tickers:
            n = _make_monitor(ticker).check_once()
            total += n
        print(f"Fills detected: {total}")
        return 0

    # For simplicity: one monitor covering all open orders (position managers keyed by ticker)
    # We create a single monitor with no position_manager and handle pm lookup in a wrapper
    monitor = _MultiTickerMonitor(db=db, client=client, pms=pms)
    monitor.run(interval=args.interval)
    return 0


class _MultiTickerMonitor:
    """Internal helper: OrderMonitor variant that routes fills to per-ticker PositionManagers."""

    def __init__(self, db, client, pms):
        self._db = db
        self._client = client
        self._pms = pms

    def run(self, interval: float = 10.0) -> None:
        import logging as _logging
        logger = _logging.getLogger(__name__)
        logger.info("Multi-ticker monitor started (interval=%.1fs)", interval)
        try:
            while True:
                open_orders = self._db.get_open_orders()
                for order in open_orders:
                    pm = self._pms.get(order.ticker)
                    if pm is None:
                        from src.strategy.position_manager import PositionManager
                        pm = PositionManager(db=self._db, ticker=order.ticker)
                        self._pms[order.ticker] = pm
                    from src.strategy.order_monitor import OrderMonitor
                    mon = OrderMonitor(db=self._db, client=self._client, position_manager=pm)
                    try:
                        mon._check_order(order)
                    except Exception as exc:
                        logger.error("Monitor error for order %s: %s", order.id, exc)
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Monitor stopped.")


def cmd_dashboard(args: argparse.Namespace) -> int:
    from src.dashboard import PredicArbDashboard

    settings = get_settings()
    db_path = settings.data_dir / "polymarket_bot.sqlite"
    app = PredicArbDashboard(db_path=db_path, refresh_interval=args.interval)
    app.run()
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    import asyncio
    from src.polymarket.auth import PolymarketSigner
    from src.storage.db import Database
    from src.strategy.collector import TickCollector

    settings = get_settings()
    configure_logging(settings.logs_dir, settings.log_level)
    logger = get_logger(__name__)

    ticker = args.ticker
    csv_path = Path(args.csv) if args.csv else None

    db = Database(settings.data_dir / "polymarket_bot.sqlite")
    db.init()

    signer: object | None = None
    if (
        settings.polymarket_api_key
        and settings.polymarket_api_secret
        and settings.polymarket_api_passphrase
        and settings.polymarket_address
    ):
        signer = PolymarketSigner(
            api_key=settings.polymarket_api_key,
            api_secret=settings.polymarket_api_secret,
            api_passphrase=settings.polymarket_api_passphrase,
            address=settings.polymarket_address,
        )

    collector = TickCollector(db=db, ticker=ticker, csv_path=csv_path, signer=signer)

    logger.info("Starting tick collector: ticker=%s csv=%s", ticker, csv_path)
    print(f"Collecting ticks for {ticker}{'  →  ' + str(csv_path) if csv_path else ''}  (Ctrl-C to stop)")

    async def _run() -> int:
        task = asyncio.create_task(collector.run())
        try:
            return await task
        except asyncio.CancelledError:
            return 0

    try:
        count = asyncio.run(_run())
    except KeyboardInterrupt:
        count = 0

    print(f"\nCollected {count} ticks.")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from src.benchmark.csv_benchmark import BenchmarkProvider, CsvBenchmark
    from src.storage.db import Database
    from src.strategy.backtest import BacktestEngine

    settings = get_settings()
    configure_logging(settings.logs_dir, settings.log_level)
    logger = get_logger(__name__)

    tick_csv = Path(args.tick_csv)
    if not tick_csv.exists():
        print(f"Error: tick CSV not found: {tick_csv}", file=sys.stderr)
        return 1

    benchmark_prob: float | None = args.benchmark
    benchmark_csv_path: str | None = args.benchmark_csv

    if benchmark_csv_path:
        bcsv = Path(benchmark_csv_path)
        if not bcsv.exists():
            print(f"Error: benchmark CSV not found: {bcsv}", file=sys.stderr)
            return 1
        benchmark: BenchmarkProvider = CsvBenchmark(bcsv)
        benchmark_label = str(bcsv)
    else:
        if benchmark_prob is None:
            print("Error: provide --benchmark or --benchmark-csv.", file=sys.stderr)
            return 1

        class _Const(BenchmarkProvider):
            def get_prob(self, ts_utc: datetime) -> float:
                return benchmark_prob  # type: ignore[return-value]

        benchmark = _Const()
        benchmark_label = f"constant:{benchmark_prob}"

    filters = EdgeFilters(
        max_spread=args.max_spread,
        min_depth=args.min_depth,
        max_staleness_seconds=args.max_staleness,
    )

    engine = BacktestEngine(
        benchmark=benchmark,
        filters=filters,
        ticker=args.ticker,
        size=args.size,
        min_edge=args.min_edge,
        benchmark_label=benchmark_label,
    )

    logger.info("Running backtest on %s", tick_csv)
    trades, run = engine.run(tick_csv)

    # Persist to DB
    db = Database(settings.data_dir / "polymarket_bot.sqlite")
    db.init()
    run_id = db.insert_backtest_run(run)

    # Print summary
    print(f"Backtest run id : {run_id}")
    print(f"Tick CSV        : {tick_csv}")
    print(f"Benchmark       : {benchmark_label}")
    print(f"Total ticks     : (see CSV)")
    print(f"Total trades    : {run.total_trades}")
    if run.total_trades == 0:
        print("No trades were taken (check filters and min-edge).")
        return 0

    print(f"Win rate        : {run.win_rate:.1%}")
    print(f"Total P&L       : {run.total_pnl:+.4f}")
    print(f"Sharpe          : {run.sharpe:.4f}" if run.sharpe is not None else "Sharpe          : n/a")
    print(f"Max drawdown    : {run.max_drawdown:.4f}")

    if args.verbose and trades:
        print()
        header = f"{'Timestamp':<28} {'Decision':<10} {'FillPx':>7} {'Bench':>7} {'Edge':>7} {'PnL':>8}"
        print(header)
        print("-" * len(header))
        for t in trades:
            print(
                f"{t.ts_utc:<28} {t.decision:<10} {t.fill_price:>7.4f} "
                f"{t.benchmark_prob:>7.4f} {t.edge:>+7.4f} {t.pnl:>+8.4f}"
            )

    return 0


def cmd_run_multi(args: argparse.Namespace) -> int:
    import asyncio

    from src.polymarket.auth import PolymarketSigner
    from src.storage.db import Database
    from src.strategy.multi_runner import MultiMarketRunner
    from src.strategy.position_manager import PositionManager
    from src.strategy.runner import TradingRunner
    from src.strategy.ws_runner import WsTradingRunner

    settings = get_settings()
    configure_logging(settings.logs_dir, settings.log_level)
    logger = get_logger(__name__)

    tickers: list[str] = args.tickers
    benchmark = _build_benchmark(args)
    benchmark_source = getattr(args, "benchmark_source", "constant") or "constant"
    size: int = args.size
    min_edge: float = args.min_edge
    dry_run: bool = args.dry_run

    logger.info(
        "Starting multi-market run: tickers=%s benchmark_source=%s size=%d min_edge=%.4f dry_run=%s",
        tickers, benchmark_source, size, min_edge, dry_run,
    )

    client = PolymarketClient(settings)

    db = Database(settings.data_dir / "polymarket_bot.sqlite")
    db.init()

    filters = EdgeFilters(
        max_spread=args.max_spread,
        min_depth=args.min_depth,
        max_staleness_seconds=args.max_staleness,
    )

    signer: object | None = None
    if (
        settings.polymarket_api_key
        and settings.polymarket_api_secret
        and settings.polymarket_api_passphrase
        and settings.polymarket_address
    ):
        signer = PolymarketSigner(
            api_key=settings.polymarket_api_key,
            api_secret=settings.polymarket_api_secret,
            api_passphrase=settings.polymarket_api_passphrase,
            address=settings.polymarket_address,
        )

    runners: dict[str, WsTradingRunner] = {}
    for ticker in tickers:
        pm = PositionManager(
            db=db,
            ticker=ticker,
            max_long=args.max_long,
            max_short=args.max_short,
            max_loss=args.max_loss,
        )
        trading_runner = TradingRunner(
            client=client,
            db=db,
            benchmark=benchmark,
            filters=filters,
            ticker=ticker,
            size=size,
            min_edge=min_edge,
            dry_run=dry_run,
            position_manager=pm,
        )
        runners[ticker] = WsTradingRunner(runner=trading_runner, signer=signer)  # type: ignore[arg-type]

    multi = MultiMarketRunner(runners)
    print(f"Multi-market WS run: {', '.join(tickers)}  (Ctrl-C to stop)")
    try:
        counts = asyncio.run(multi.run())
    except KeyboardInterrupt:
        counts = {}

    for ticker, n in counts.items():
        print(f"  {ticker}: {n} tick(s) processed")
    print("\nStopped.")
    return 0


def _build_benchmark(args: argparse.Namespace):
    """Construct the appropriate BenchmarkProvider from the registry."""
    from src.benchmark.registry import registry

    source = getattr(args, "benchmark_source", "constant") or "constant"
    return registry.get(
        source,
        benchmark=getattr(args, "benchmark", None),
        zq_meeting_date=getattr(args, "zq_meeting_date", None),
        benchmark_ttl=getattr(args, "benchmark_ttl", 60.0),
    )


def cmd_run(args: argparse.Namespace) -> int:
    import asyncio

    from src.storage.db import Database
    from src.strategy.position_manager import PositionManager
    from src.strategy.runner import TradingRunner

    settings = get_settings()
    configure_logging(settings.logs_dir, settings.log_level)
    logger = get_logger(__name__)

    ticker = args.ticker
    size = args.size
    interval = args.interval
    min_edge = args.min_edge
    dry_run = args.dry_run
    use_ws = args.ws

    benchmark = _build_benchmark(args)
    source_label = getattr(args, "benchmark_source", "constant") or "constant"

    logger.info(
        "Starting run: ticker=%s benchmark_source=%s size=%d min_edge=%.4f dry_run=%s ws=%s",
        ticker, source_label, size, min_edge, dry_run, use_ws,
    )

    client = PolymarketClient(settings)

    db_path = settings.data_dir / "polymarket_bot.sqlite"
    db = Database(db_path)
    db.init()

    filters = EdgeFilters(
        max_spread=args.max_spread,
        min_depth=args.min_depth,
        max_staleness_seconds=args.max_staleness,
    )

    pm = PositionManager(
        db=db,
        ticker=ticker,
        max_long=args.max_long,
        max_short=args.max_short,
        max_loss=args.max_loss,
    )

    runner = TradingRunner(
        client=client,
        db=db,
        benchmark=benchmark,
        filters=filters,
        ticker=ticker,
        size=size,
        min_edge=min_edge,
        dry_run=dry_run,
        position_manager=pm,
    )

    if use_ws:
        from src.polymarket.auth import PolymarketSigner
        from src.strategy.ws_runner import WsTradingRunner

        signer: object | None = None
        if (
            settings.polymarket_api_key
            and settings.polymarket_api_secret
            and settings.polymarket_api_passphrase
            and settings.polymarket_address
        ):
            signer = PolymarketSigner(
                api_key=settings.polymarket_api_key,
                api_secret=settings.polymarket_api_secret,
                api_passphrase=settings.polymarket_api_passphrase,
                address=settings.polymarket_address,
            )

        ws_runner = WsTradingRunner(runner=runner, signer=signer)  # type: ignore[arg-type]
        print(f"WebSocket live run: {ticker}  (Ctrl-C to stop)")
        try:
            asyncio.run(ws_runner.run())
        except KeyboardInterrupt:
            pass
    else:
        try:
            runner.run(interval=interval)
        except KeyboardInterrupt:
            pass

    print("\nStopped.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="polymarket-bot-cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # health
    p_health = subparsers.add_parser("health", help="Check API health and account.")
    p_health.set_defaults(func=cmd_health)

    # list-markets
    p_list = subparsers.add_parser(
        "list-markets", help="List markets filtered by keywords."
    )
    p_list.add_argument(
        "--keywords",
        nargs="+",
        default=[],
        help="Keyword search terms.",
    )
    p_list.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of markets to return (default: 50).",
    )
    p_list.add_argument(
        "--event-ticker",
        dest="event_ticker",
        default=None,
        help="Filter by event tag (Gamma API 'tag' param).",
    )
    p_list.add_argument(
        "--series-ticker",
        dest="series_ticker",
        default=None,
        help="Filter by series (not directly supported by Gamma API; ignored).",
    )
    p_list.set_defaults(func=cmd_list_markets)

    # watch
    p_watch = subparsers.add_parser(
        "watch", help="Watch a single market's orderbook."
    )
    p_watch.add_argument("--ticker", required=True, help="YES outcome token_id to watch (from list-markets).")
    p_watch.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds (default: 2).",
    )
    p_watch.set_defaults(func=cmd_watch)

    # signal
    p_signal = subparsers.add_parser(
        "signal",
        help=(
            "Compute edge vs benchmark for a market "
            "and report whether trade criteria are met."
        ),
    )
    p_signal.add_argument("--ticker", required=True, help="YES outcome token_id to compute edge for (from list-markets).")
    p_signal.add_argument(
        "--benchmark",
        type=float,
        required=True,
        help="Benchmark probability in [0, 1].",
    )
    p_signal.add_argument(
        "--max-spread",
        type=float,
        default=0.1,
        help="Maximum allowed bid/ask spread (default: 0.1).",
    )
    p_signal.add_argument(
        "--min-depth",
        type=int,
        default=None,
        help="Minimum depth (contracts) on both sides; optional.",
    )
    p_signal.add_argument(
        "--max-staleness",
        type=float,
        default=60.0,
        help="Maximum quote staleness in seconds (default: 60).",
    )
    p_signal.set_defaults(func=cmd_signal)

    # trade
    p_trade = subparsers.add_parser(
        "trade",
        help="Place a limit order on Polymarket CLOB (requires L2 credentials).",
    )
    p_trade.add_argument("--ticker", required=True, help="YES token_id to trade.")
    p_trade.add_argument(
        "--side",
        required=True,
        choices=["BUY", "SELL", "buy", "sell"],
        help="Order side: BUY or SELL.",
    )
    p_trade.add_argument("--price", type=float, required=True, help="Limit price in [0, 1].")
    p_trade.add_argument("--size", type=int, required=True, help="Number of shares.")
    p_trade.add_argument(
        "--tif",
        default="GTC",
        choices=["GTC", "FOK", "GTD"],
        help="Time-in-force: GTC (default), FOK, or GTD.",
    )
    p_trade.set_defaults(func=cmd_trade)

    # run
    p_run = subparsers.add_parser(
        "run",
        help="Live trading loop: poll orderbook, compute edge, place orders.",
    )
    p_run.add_argument("--ticker", required=True, help="YES token_id to trade.")
    p_run.add_argument(
        "--benchmark",
        type=float,
        default=None,
        help="Constant benchmark probability in [0, 1]. Required unless --benchmark-source is set.",
    )
    p_run.add_argument(
        "--benchmark-source",
        choices=["constant", "zq"],
        default="constant",
        help="Benchmark source: 'constant' (use --benchmark) or 'zq' for live CME futures (default: constant).",
    )
    p_run.add_argument(
        "--zq-meeting-date",
        default=None,
        help="FOMC meeting date (YYYY-MM-DD) for ZQ benchmark. Required when --benchmark-source=zq.",
    )
    p_run.add_argument(
        "--benchmark-ttl",
        type=float,
        default=60.0,
        help="Live benchmark cache TTL in seconds (default: 60).",
    )
    p_run.add_argument("--size", type=int, required=True, help="Contracts per order.")
    p_run.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds (default: 5).",
    )
    p_run.add_argument(
        "--max-spread",
        type=float,
        default=0.1,
        help="Maximum allowed bid/ask spread (default: 0.1).",
    )
    p_run.add_argument(
        "--min-depth",
        type=int,
        default=None,
        help="Minimum depth (contracts) on both sides; optional.",
    )
    p_run.add_argument(
        "--max-staleness",
        type=float,
        default=60.0,
        help="Maximum quote staleness in seconds (default: 60).",
    )
    p_run.add_argument(
        "--min-edge",
        type=float,
        default=0.02,
        help="Minimum |edge| required to trade (default: 0.02).",
    )
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Log trade decisions without sending orders.",
    )
    p_run.add_argument(
        "--max-long",
        type=int,
        default=None,
        help="Maximum net long contracts allowed (default: unlimited).",
    )
    p_run.add_argument(
        "--max-short",
        type=int,
        default=0,
        help="Maximum net short contracts allowed (default: 0 — no shorting).",
    )
    p_run.add_argument(
        "--max-loss",
        type=float,
        default=None,
        help="Halt trading if realized P&L falls below -MAX_LOSS (default: no limit).",
    )
    p_run.add_argument(
        "--ws",
        action="store_true",
        help="Use WebSocket stream instead of REST polling (lower latency).",
    )
    p_run.set_defaults(func=cmd_run)

    # run-multi
    p_multi = subparsers.add_parser(
        "run-multi",
        help="Live WS trading loop for multiple tickers concurrently.",
    )
    p_multi.add_argument(
        "--tickers",
        nargs="+",
        required=True,
        help="One or more YES token_ids to trade concurrently.",
    )
    p_multi.add_argument(
        "--benchmark",
        type=float,
        required=False,
        default=None,
        help="Constant benchmark probability in [0, 1] (shared across all markets).",
    )
    p_multi.add_argument(
        "--benchmark-source",
        choices=["constant", "zq"],
        default="constant",
        help="Benchmark source: 'constant' (use --benchmark) or 'zq' for live CME futures (default: constant).",
    )
    p_multi.add_argument(
        "--zq-meeting-date",
        default=None,
        help="FOMC meeting date (YYYY-MM-DD) for ZQ benchmark. Required when --benchmark-source=zq.",
    )
    p_multi.add_argument(
        "--benchmark-ttl",
        type=float,
        default=60.0,
        help="Live benchmark cache TTL in seconds (default: 60).",
    )
    p_multi.add_argument("--size", type=int, required=True, help="Contracts per order.")
    p_multi.add_argument(
        "--max-spread",
        type=float,
        default=0.1,
        help="Maximum allowed bid/ask spread (default: 0.1).",
    )
    p_multi.add_argument(
        "--min-depth",
        type=int,
        default=None,
        help="Minimum depth (contracts) on both sides; optional.",
    )
    p_multi.add_argument(
        "--max-staleness",
        type=float,
        default=60.0,
        help="Maximum quote staleness in seconds (default: 60).",
    )
    p_multi.add_argument(
        "--min-edge",
        type=float,
        default=0.02,
        help="Minimum |edge| required to trade (default: 0.02).",
    )
    p_multi.add_argument(
        "--dry-run",
        action="store_true",
        help="Log trade decisions without sending orders.",
    )
    p_multi.add_argument(
        "--max-long",
        type=int,
        default=None,
        help="Maximum net long contracts per market (default: unlimited).",
    )
    p_multi.add_argument(
        "--max-short",
        type=int,
        default=0,
        help="Maximum net short contracts per market (default: 0).",
    )
    p_multi.add_argument(
        "--max-loss",
        type=float,
        default=None,
        help="Halt a market if realized P&L falls below -MAX_LOSS (default: no limit).",
    )
    p_multi.set_defaults(func=cmd_run_multi)

    # backtest
    p_bt = subparsers.add_parser(
        "backtest",
        help="Replay historical tick CSV and report strategy performance.",
    )
    p_bt.add_argument("--ticker", required=True, help="Market token_id label.")
    p_bt.add_argument(
        "--tick-csv",
        required=True,
        help="Path to tick CSV (columns: ts_utc,yes_bid,yes_ask[,bid_size,ask_size]).",
    )
    p_bt.add_argument(
        "--benchmark",
        type=float,
        default=None,
        help="Constant benchmark probability in [0, 1].",
    )
    p_bt.add_argument(
        "--benchmark-csv",
        default=None,
        help="Path to benchmark CSV (columns: ts_utc,prob). Overrides --benchmark.",
    )
    p_bt.add_argument("--size", type=int, default=100, help="Contracts per order (default: 100).")
    p_bt.add_argument(
        "--max-spread",
        type=float,
        default=0.1,
        help="Maximum allowed spread (default: 0.1).",
    )
    p_bt.add_argument("--min-depth", type=int, default=None, help="Minimum depth; optional.")
    p_bt.add_argument(
        "--max-staleness",
        type=float,
        default=None,
        help="Maximum staleness in seconds; optional (no staleness check for CSV data by default).",
    )
    p_bt.add_argument(
        "--min-edge",
        type=float,
        default=0.02,
        help="Minimum |edge| to simulate a trade (default: 0.02).",
    )
    p_bt.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print every simulated trade.",
    )
    p_bt.set_defaults(func=cmd_backtest)

    # collect
    p_col = subparsers.add_parser(
        "collect",
        help="Stream live orderbook via WebSocket and persist ticks to DB (and optionally CSV).",
    )
    p_col.add_argument("--ticker", required=True, help="YES token_id to subscribe to.")
    p_col.add_argument(
        "--csv",
        default=None,
        help="Path to CSV file for tick output (appends if file exists). "
             "Use this file as --tick-csv input for the backtest command.",
    )
    p_col.set_defaults(func=cmd_collect)

    # positions
    p_pos = subparsers.add_parser("positions", help="Show current positions and P&L.")
    p_pos.set_defaults(func=cmd_positions)

    # orders
    p_ord = subparsers.add_parser("orders", help="Show recent orders.")
    p_ord.add_argument("--limit", type=int, default=20, help="Number of orders to show (default: 20).")
    p_ord.add_argument(
        "--status",
        default=None,
        choices=["placed", "filled", "cancelled", "rejected", "error", "dry_run"],
        help="Filter by order status.",
    )
    p_ord.set_defaults(func=cmd_orders)

    # dashboard
    p_dash = subparsers.add_parser(
        "dashboard",
        help="Launch the live terminal dashboard (Textual TUI).",
    )
    p_dash.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Auto-refresh interval in seconds (default: 5).",
    )
    p_dash.set_defaults(func=cmd_dashboard)

    # monitor
    p_mon = subparsers.add_parser(
        "monitor",
        help="Poll open orders until filled/cancelled and update positions.",
    )
    p_mon.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="Polling interval in seconds (default: 10).",
    )
    p_mon.add_argument(
        "--once",
        action="store_true",
        help="Run a single check pass and exit.",
    )
    p_mon.set_defaults(func=cmd_monitor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 1
    return func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
