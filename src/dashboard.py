"""
PredicArb — interactive dark-mode terminal dashboard.

Tabs:
  Markets    — live KPIs, positions, signals, fills, ticks
  Add Ticker — watch new markets, manage watchlist
  Backtest   — replay tick CSV, view run history

Keys: r = refresh  d = remove selected ticker  q = quit
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.storage.db import Database
from zoneinfo import ZoneInfo

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

ROME = ZoneInfo("Europe/Rome")

# ── iOS Dark palette ──────────────────────────────────────────────────────────
_BG     = "#000000"
_BG2    = "#1C1C1E"
_BG3    = "#2C2C2E"
_BG4    = "#3A3A3C"
_TEXT   = "#FFFFFF"
_TEXT2  = "#EBEBF5"
_SUB    = "#8E8E93"
_BORDER = "#38383A"
_BLUE   = "#0A84FF"
_GREEN  = "#30D158"
_RED    = "#FF453A"
_ORANGE = "#FF9F0A"
_PURPLE = "#BF5AF2"
_YELLOW = "#FFD60A"


# ── helpers ───────────────────────────────────────────────────────────────────

def _rome_hms(ts_utc: Optional[str] = None) -> str:
    if ts_utc:
        try:
            dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(ROME).strftime("%H:%M:%S")
        except Exception:
            return ts_utc[11:19]
    return datetime.now(ROME).strftime("%H:%M:%S")


def _rome_now_full() -> str:
    return datetime.now(ROME).strftime("%d %b %Y  %H:%M:%S")


def _ticker(t: str, n: int = 10) -> str:
    return (t[:n] + "…") if len(t) > n else t


def _pnl(v: float) -> str:
    s = f"{v:+.2f}"
    if v > 0: return f"[bold {_GREEN}]{s}[/]"
    if v < 0: return f"[bold {_RED}]{s}[/]"
    return f"[{_SUB}]{s}[/]"


def _edge(e: Optional[float]) -> str:
    if e is None: return f"[{_SUB}]—[/]"
    s = f"{e:+.3f}"
    if e >  0.02: return f"[{_GREEN}]{s}[/]"
    if e < -0.02: return f"[{_RED}]{s}[/]"
    return f"[{_SUB}]{s}[/]"


def _decision(d: str) -> str:
    d = d.upper()
    if "BUY"  in d: return f"[bold {_GREEN}]▲ BUY[/]"
    if "SELL" in d: return f"[bold {_RED}]▼ SELL[/]"
    return f"[{_SUB}]— hold[/]"


def _side(s: str) -> str:
    s = s.upper()
    return f"[{_GREEN}]▲ BUY[/]" if s == "BUY" else f"[{_RED}]▼ SELL[/]"


# ── base CSS ──────────────────────────────────────────────────────────────────

_BASE_CSS = f"""
Screen {{
    background: {_BG};
    layout: vertical;
}}

TabbedContent {{
    height: 1fr;
    background: {_BG};
}}

TabbedContent ContentSwitcher {{
    background: {_BG};
}}

TabPane {{
    background: {_BG};
    padding: 0;
}}

Tabs {{
    background: {_BG2};
    border-bottom: solid {_BORDER};
}}

Tab {{
    color: {_SUB};
    background: {_BG2};
    padding: 1 4;
}}

Tab:hover {{
    background: {_BG3};
    color: {_TEXT2};
}}

Tab.-active {{
    color: {_BLUE};
    background: {_BG2};
    text-style: bold;
    border-bottom: solid {_BLUE};
}}

Footer {{
    background: {_BG2};
    color: {_SUB};
    border-top: solid {_BORDER};
}}

DataTable {{
    background: {_BG3};
    color: {_TEXT};
}}

DataTable > .datatable--header {{
    background: {_BG4};
    color: {_SUB};
    text-style: bold;
}}

DataTable > .datatable--odd-row {{
    background: {_BG3};
}}

DataTable > .datatable--even-row {{
    background: {_BG2};
}}

DataTable > .datatable--cursor {{
    background: {_BLUE}40;
}}

Input {{
    background: {_BG4};
    color: {_TEXT};
    border: round {_BORDER};
}}

Input:focus {{
    border: round {_BLUE};
}}

Button {{
    background: {_BLUE};
    color: {_TEXT};
    border: none;
}}

Button:hover {{
    background: #0060DF;
}}

Button.danger {{
    background: {_RED};
}}

Button.danger:hover {{
    background: #CC3529;
}}
"""


# ── top bar ───────────────────────────────────────────────────────────────────

class TopBar(Static):
    clock: reactive[str] = reactive("")

    DEFAULT_CSS = f"""
    TopBar {{
        background: {_BG2};
        color: {_TEXT};
        height: 3;
        padding: 0 3;
        text-style: bold;
        content-align: left middle;
        border-bottom: solid {_BORDER};
    }}
    """

    def render(self) -> str:
        dot = f"[{_GREEN}]●[/]"
        return f"  {dot}  PredicArb         [{_SUB}]Rome[/]  {self.clock}"

    def tick(self) -> None:
        self.clock = _rome_now_full()


# ── KPI cards ─────────────────────────────────────────────────────────────────

class MetricCard(Static):
    DEFAULT_CSS = f"""
    MetricCard {{
        background: {_BG2};
        border: round {_BORDER};
        padding: 0 2;
        height: 5;
        width: 1fr;
        margin: 0 1 0 0;
        content-align: center middle;
    }}
    """

    def __init__(self, label: str, **kw):
        super().__init__(**kw)
        self._label = label
        self._val   = "—"

    def set_value(self, v: str) -> None:
        self._val = v
        self.refresh()

    def render(self) -> str:
        return f"[{_SUB}]{self._label}[/]\n{self._val}"


class SummaryRow(Horizontal):
    DEFAULT_CSS = f"""
    SummaryRow {{
        height: 5;
        margin: 1 1 0 1;
        background: {_BG};
    }}
    """

    def compose(self) -> ComposeResult:
        yield MetricCard("Total P&L",   id="kpi-pnl")
        yield MetricCard("Long",        id="kpi-long")
        yield MetricCard("Short",       id="kpi-short")
        yield MetricCard("Open Orders", id="kpi-orders")
        yield MetricCard("Markets",     id="kpi-markets")

    def update(self, d: dict) -> None:
        self.query_one("#kpi-pnl",     MetricCard).set_value(_pnl(d["total_pnl"]))
        self.query_one("#kpi-long",    MetricCard).set_value(f"[{_GREEN}]{d['open_long']}[/]")
        self.query_one("#kpi-short",   MetricCard).set_value(f"[{_RED}]{d['open_short']}[/]")
        self.query_one("#kpi-orders",  MetricCard).set_value(f"[{_ORANGE}]{d['open_orders']}[/]")
        self.query_one("#kpi-markets", MetricCard).set_value(f"[{_BLUE}]{d['markets']}[/]")


# ── panel (accent top border, no desc subtitle) ───────────────────────────────

class Panel(Vertical, can_focus=False):
    DEFAULT_CSS = f"""
    Panel {{
        background: {_BG2};
        border: round {_BORDER};
        margin: 0 1 1 0;
        height: 1fr;
    }}
    Panel .panel-title {{
        background: {_BG3};
        color: {_TEXT};
        text-style: bold;
        padding: 0 2;
        height: 2;
        border-bottom: solid {_BORDER};
    }}
    Panel DataTable {{
        height: 1fr;
        background: {_BG2};
    }}
    """

    def __init__(self, title: str, cols: list[str], tbl_id: str,
                 accent: str = _BLUE, **kw) -> None:
        super().__init__(**kw)
        self._title  = title
        self._cols   = cols
        self._tbl_id = tbl_id
        self._accent = accent

    def compose(self) -> ComposeResult:
        yield Static(f"  {self._title}", classes="panel-title")
        yield DataTable(id=self._tbl_id, zebra_stripes=True, show_cursor=False)

    def on_mount(self) -> None:
        self.styles.border_top = ("solid", self._accent)
        self.query_one(DataTable).add_columns(*self._cols)

    def fill(self, rows: list[list]) -> None:
        tbl = self.query_one(DataTable)
        tbl.clear()
        if not rows:
            tbl.add_row(*[f"[{_SUB}]—[/]"] * len(self._cols))
        else:
            for r in rows:
                tbl.add_row(*r)


# ── ticks panel ───────────────────────────────────────────────────────────────

class TicksPanel(Vertical, can_focus=False):
    COLS = ["Ticker", "Bid", "Ask", "Mid", "Bid Sz", "Ask Sz", "Updated (Rome)"]

    DEFAULT_CSS = f"""
    TicksPanel {{
        background: {_BG2};
        border: round {_BORDER};
        margin: 0 1 1 0;
        height: auto;
        max-height: 10;
    }}
    TicksPanel .panel-title {{
        background: {_BG3};
        color: {_TEXT};
        text-style: bold;
        padding: 0 2;
        height: 2;
        border-bottom: solid {_BORDER};
    }}
    TicksPanel DataTable {{
        height: auto;
        background: {_BG2};
    }}
    """

    def compose(self) -> ComposeResult:
        yield Static("  Live Ticks", classes="panel-title")
        yield DataTable(id="tbl-ticks", zebra_stripes=True, show_cursor=False)

    def on_mount(self) -> None:
        self.styles.border_top = ("solid", _YELLOW)
        self.query_one(DataTable).add_columns(*self.COLS)

    def fill(self, rows: list[sqlite3.Row]) -> None:
        tbl = self.query_one(DataTable)
        tbl.clear()
        if not rows:
            tbl.add_row(*[f"[{_SUB}]—[/]"] * len(self.COLS))
            return
        for r in rows:
            bid, ask, mid = r["yes_bid"], r["yes_ask"], r["yes_mid"]
            mid_str = (
                f"[{_GREEN}]{mid:.4f}[/]" if mid and bid and ask and mid > (bid + ask) / 2
                else (f"[{_RED}]{mid:.4f}[/]" if mid else f"[{_SUB}]—[/]")
            )
            tbl.add_row(
                f"[{_BLUE}]{_ticker(r['ticker'], 14)}[/]",
                f"{bid:.4f}" if bid else f"[{_SUB}]—[/]",
                f"{ask:.4f}" if ask else f"[{_SUB}]—[/]",
                mid_str,
                str(r["bid_size"]) if r["bid_size"] else f"[{_SUB}]—[/]",
                str(r["ask_size"]) if r["ask_size"] else f"[{_SUB}]—[/]",
                _rome_hms(r["ts_utc"]),
            )


# ── Tab 1: Markets (live data) ────────────────────────────────────────────────

class LiveMarketsTab(Vertical, can_focus=False):
    DEFAULT_CSS = f"""
    LiveMarketsTab {{
        background: {_BG};
        padding: 0 1;
    }}
    LiveMarketsTab #lm-body {{
        layout: horizontal;
        height: 1fr;
        background: {_BG};
    }}
    LiveMarketsTab #lm-left {{
        layout: vertical;
        width: 1fr;
        background: {_BG};
    }}
    LiveMarketsTab #lm-right {{
        layout: vertical;
        width: 2fr;
        background: {_BG};
    }}
    """

    def compose(self) -> ComposeResult:
        yield SummaryRow()
        with Horizontal(id="lm-body"):
            with Vertical(id="lm-left"):
                yield Panel(
                    "Positions",
                    ["Ticker", "Net", "Avg Cost", "P&L", "Updated"],
                    "tbl-positions",
                    _GREEN,
                )
                yield Panel(
                    "Open Orders",
                    ["ID", "Ticker", "Side", "Qty", "Price", "Time"],
                    "tbl-open-orders",
                    _ORANGE,
                )
            with Vertical(id="lm-right"):
                yield Panel(
                    "Signals",
                    ["Time", "Ticker", "Mid", "Edge", "Decision"],
                    "tbl-signals",
                    _BLUE,
                )
                yield Panel(
                    "Fills",
                    ["Time", "Ticker", "Side", "Qty", "Price"],
                    "tbl-fills",
                    _PURPLE,
                )
        yield TicksPanel()


# ── Tab 2: Add Ticker ─────────────────────────────────────────────────────────

class AddTickerTab(Vertical):

    def __init__(self, db: "Database", **kw) -> None:
        super().__init__(**kw)
        self._db = db

    DEFAULT_CSS = f"""
    AddTickerTab {{
        background: {_BG};
        padding: 1 2;
    }}
    AddTickerTab .card {{
        background: {_BG2};
        border: round {_BORDER};
        padding: 0 0 1 0;
        margin-bottom: 1;
        height: auto;
    }}
    AddTickerTab .card-title {{
        background: {_BG3};
        color: {_TEXT};
        text-style: bold;
        padding: 0 2;
        height: 2;
        border-bottom: solid {_BORDER};
    }}
    AddTickerTab .card-hint {{
        color: {_SUB};
        padding: 0 2;
        height: 1;
        margin-bottom: 1;
    }}
    AddTickerTab .form-body {{
        padding: 0 2;
        height: auto;
    }}
    AddTickerTab Label {{
        color: {_SUB};
        height: 1;
        margin-top: 1;
    }}
    AddTickerTab .form-row {{
        layout: horizontal;
        height: auto;
    }}
    AddTickerTab .form-row > Vertical {{
        width: 1fr;
        margin-right: 1;
    }}
    AddTickerTab Button {{
        margin-top: 1;
        min-width: 20;
    }}
    AddTickerTab .btn-row {{
        layout: horizontal;
        height: auto;
        align: left middle;
        padding: 0 2;
    }}
    AddTickerTab .btn-hint {{
        color: {_SUB};
        content-align: left middle;
        height: 3;
        padding: 0 1;
        margin-top: 1;
    }}
    AddTickerTab .status-msg {{
        height: 1;
        padding: 0 2;
        margin-top: 1;
    }}
    AddTickerTab .watch-table {{
        background: {_BG2};
        border: round {_BORDER};
        height: 1fr;
        min-height: 10;
    }}
    AddTickerTab .watch-table DataTable {{
        height: 1fr;
        background: {_BG2};
    }}
    """

    def compose(self) -> ComposeResult:
        # ── add form card ──
        with Vertical(classes="card", id="add-card"):
            yield Static("  Add New Market", classes="card-title")
            yield Static(
                "  Paste a YES token_id from Polymarket and set your fair-value benchmark",
                classes="card-hint",
            )
            with Vertical(classes="form-body"):
                with Horizontal(classes="form-row"):
                    with Vertical():
                        yield Label("YES token_id")
                        yield Input(placeholder="0xabc123def456…", id="inp-ticker-id")
                    with Vertical():
                        yield Label("Label  (optional)")
                        yield Input(placeholder="e.g. Trump wins 2026", id="inp-ticker-label")
                with Horizontal(classes="form-row"):
                    with Vertical():
                        yield Label("Benchmark probability  (0 – 1)")
                        yield Input(placeholder="0.60", id="inp-benchmark", value="0.50")
                with Horizontal(classes="btn-row"):
                    yield Button("＋  Add Ticker", id="btn-add-ticker")
                    yield Static(
                        f"  [{_SUB}]Saves to watchlist · appears in the table below[/]",
                        classes="btn-hint",
                    )
            yield Static("", id="add-status", classes="status-msg")

        # ── watched tickers table ──
        with Vertical(classes="watch-table", id="watch-card"):
            yield Static("  Watched Markets", classes="card-title")
            yield Static(
                f"  [{_YELLOW}]Highlight a row and press D to remove[/]",
                classes="card-hint",
            )
            yield DataTable(id="tbl-watched", zebra_stripes=True, show_cursor=True)

    def on_mount(self) -> None:
        self.query_one("#add-card").styles.border_top   = ("solid", _BLUE)
        self.query_one("#watch-card").styles.border_top = ("solid", _GREEN)
        self.query_one("#tbl-watched", DataTable).add_columns(
            "Ticker", "Label", "Benchmark", "Bid", "Ask", "Mid", "Added"
        )
        self.fill(self._db.watched())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "btn-add-ticker":
            return
        ticker_id = self.query_one("#inp-ticker-id",    Input).value.strip()
        label     = self.query_one("#inp-ticker-label", Input).value.strip()
        bench_str = self.query_one("#inp-benchmark",    Input).value.strip()
        status    = self.query_one("#add-status",       Static)

        if not ticker_id:
            status.update(f"[{_RED}]⚠  Token ID is required.[/]")
            return
        try:
            bench = float(bench_str)
            if not 0 < bench < 1:
                raise ValueError
        except ValueError:
            status.update(f"[{_RED}]⚠  Benchmark must be between 0 and 1.[/]")
            return

        self._db.add_watched(ticker_id, label, bench)
        self.query_one("#inp-ticker-id",    Input).value = ""
        self.query_one("#inp-ticker-label", Input).value = ""
        status.update(f"[{_GREEN}]✓  Added {ticker_id[:24]}[/]")
        self.fill(self._db.watched())

    def fill(self, rows: list[sqlite3.Row]) -> None:
        tbl = self.query_one("#tbl-watched", DataTable)
        tbl.clear()
        if not rows:
            tbl.add_row(
                f"[{_SUB}]—[/]",
                f"[{_SUB}]No markets added yet — use the form above[/]",
                *[f"[{_SUB}]—[/]"] * 5,
            )
            return
        for r in rows:
            bid, ask, mid = r["yes_bid"], r["yes_ask"], r["yes_mid"]
            tbl.add_row(
                f"[{_BLUE}]{_ticker(r['ticker'], 16)}[/]",
                r["label"] or f"[{_SUB}]—[/]",
                f"{r['benchmark']:.2f}",
                f"{bid:.4f}" if bid else f"[{_SUB}]—[/]",
                f"{ask:.4f}" if ask else f"[{_SUB}]—[/]",
                f"{mid:.4f}" if mid else f"[{_SUB}]—[/]",
                _rome_hms(r["added_at"]),
            )


# ── Tab 3: Backtest ───────────────────────────────────────────────────────────

class BacktestTab(Vertical):

    def __init__(self, db: "Database", **kw) -> None:
        super().__init__(**kw)
        self._db = db

    DEFAULT_CSS = f"""
    BacktestTab {{
        background: {_BG};
        padding: 1 2;
    }}
    BacktestTab .card {{
        background: {_BG2};
        border: round {_BORDER};
        padding: 0 0 1 0;
        margin-bottom: 1;
        height: auto;
        max-height: 18;
    }}
    BacktestTab .card-title {{
        background: {_BG3};
        color: {_TEXT};
        text-style: bold;
        padding: 0 2;
        height: 2;
        border-bottom: solid {_BORDER};
    }}
    BacktestTab .card-hint {{
        color: {_SUB};
        padding: 0 2;
        height: 1;
    }}
    BacktestTab .form-body {{
        padding: 0 2;
        height: auto;
    }}
    BacktestTab Label {{
        color: {_SUB};
        height: 1;
    }}
    BacktestTab .form-row {{
        layout: horizontal;
        height: 3;
    }}
    BacktestTab .form-row > Vertical {{
        width: 1fr;
        margin-right: 1;
    }}
    BacktestTab Button {{
        background: {_PURPLE};
        min-width: 20;
    }}
    BacktestTab Button:hover {{
        background: #9B3DD6;
    }}
    BacktestTab .btn-row {{
        layout: horizontal;
        height: 3;
        align: left middle;
        padding: 0 2;
    }}
    BacktestTab .btn-hint {{
        color: {_SUB};
        content-align: left middle;
        height: 3;
        padding: 0 1;
    }}
    BacktestTab .status-msg {{
        height: 1;
        padding: 0 2;
    }}
    BacktestTab .kpi-row {{
        layout: horizontal;
        height: 5;
        margin-bottom: 1;
    }}
    BacktestTab .history-card {{
        background: {_BG2};
        border: round {_BORDER};
        height: 1fr;
        min-height: 8;
    }}
    BacktestTab .history-card DataTable {{
        height: 1fr;
        background: {_BG2};
    }}
    """

    def compose(self) -> ComposeResult:
        # ── compact run form ──
        with Vertical(classes="card", id="bt-form-card"):
            yield Static("  Configure & Run Backtest", classes="card-title")
            with Vertical(classes="form-body"):
                with Horizontal(classes="form-row"):
                    with Vertical():
                        yield Label("Ticker")
                        yield Input(placeholder="0xabc123…", id="bt-ticker")
                    with Vertical():
                        yield Label("Tick CSV path")
                        yield Input(placeholder="/path/to/ticks.csv", id="bt-csv")
                    with Vertical():
                        yield Label("Benchmark")
                        yield Input(placeholder="0.60", id="bt-benchmark", value="0.60")
                    with Vertical():
                        yield Label("Size")
                        yield Input(placeholder="100", id="bt-size", value="100")
                    with Vertical():
                        yield Label("Min edge")
                        yield Input(placeholder="0.02", id="bt-min-edge", value="0.02")
                with Horizontal(classes="btn-row"):
                    yield Button("▶  Run Backtest", id="btn-run-bt")
                    yield Static("", id="bt-status", classes="status-msg")

        # ── last-run KPIs ──
        with Horizontal(classes="kpi-row"):
            yield MetricCard("Trades",       id="bt-kpi-trades")
            yield MetricCard("Win Rate",     id="bt-kpi-wr")
            yield MetricCard("Total P&L",    id="bt-kpi-pnl")
            yield MetricCard("Sharpe",       id="bt-kpi-sharpe")
            yield MetricCard("Max Drawdown", id="bt-kpi-dd")

        # ── history table (fills remaining space) ──
        with Vertical(classes="history-card", id="bt-hist-card"):
            yield Static("  Run History", classes="card-title")
            yield DataTable(id="tbl-bt-history", zebra_stripes=True, show_cursor=False)

    def on_mount(self) -> None:
        self.query_one("#bt-form-card").styles.border_top = ("solid", _PURPLE)
        self.query_one("#bt-hist-card").styles.border_top = ("solid", _ORANGE)
        self.query_one("#tbl-bt-history", DataTable).add_columns(
            "Run", "Time (Rome)", "Market", "Trades", "Win%", "P&L", "Sharpe", "Drawdown"
        )
        self.fill_history(self._db.backtest_runs())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "btn-run-bt":
            return
        ticker   = self.query_one("#bt-ticker",    Input).value.strip()
        csv_path = self.query_one("#bt-csv",       Input).value.strip()
        bench_s  = self.query_one("#bt-benchmark", Input).value.strip()
        size_s   = self.query_one("#bt-size",      Input).value.strip()
        edge_s   = self.query_one("#bt-min-edge",  Input).value.strip()
        status   = self.query_one("#bt-status",    Static)

        if not ticker:
            status.update(f"[{_RED}]⚠  Ticker required.[/]"); return
        if not csv_path or not Path(csv_path).exists():
            status.update(f"[{_RED}]⚠  Tick CSV not found: {csv_path}[/]"); return
        try:
            bench = float(bench_s)
            size  = int(size_s)
            edge  = float(edge_s)
        except ValueError:
            status.update(f"[{_RED}]⚠  Invalid number in form.[/]"); return

        status.update(f"[{_ORANGE}]⏳  Running backtest…[/]")
        self._run_worker(ticker, csv_path, bench, size, edge)

    @work(thread=True)
    def _run_worker(
        self, ticker: str, csv_path: str, bench: float, size: int, edge: float
    ) -> None:
        from src.benchmark.csv_benchmark import BenchmarkProvider
        from src.storage.db import Database
        from src.strategy.backtest import BacktestEngine
        from src.strategy.edge_calculator import EdgeFilters

        class _Const(BenchmarkProvider):
            def get_prob(self, ts_utc) -> float:
                return bench

        engine = BacktestEngine(
            benchmark       = _Const(),
            filters         = EdgeFilters(max_spread=0.15),
            ticker          = ticker,
            size            = size,
            min_edge        = edge,
            benchmark_label = f"constant:{bench}",
        )
        try:
            _, run = engine.run(Path(csv_path))
            db_main = Database(self._db._path)
            db_main.init()
            db_main.insert_backtest_run(run)
            self.call_from_thread(self._done, True, "")
        except Exception as exc:
            self.call_from_thread(self._done, False, str(exc))

    def _done(self, ok: bool, err: str) -> None:
        status = self.query_one("#bt-status", Static)
        if ok:
            status.update(f"[{_GREEN}]✓  Complete — results saved.[/]")
            history = self._db.backtest_runs()
            self.fill_history(history)
            if history:
                self.update_kpis(history[0])
        else:
            status.update(f"[{_RED}]⚠  {err}[/]")

    def fill_history(self, rows: list[sqlite3.Row]) -> None:
        tbl = self.query_one("#tbl-bt-history", DataTable)
        tbl.clear()
        if not rows:
            tbl.add_row(*[f"[{_SUB}]—[/]"] * 8)
            return
        for r in rows:
            sharpe = f"{r['sharpe']:.3f}" if r["sharpe"] is not None else f"[{_SUB}]—[/]"
            tbl.add_row(
                str(r["id"]),
                _rome_hms(r["ts_utc"]),
                _ticker(r["market_file"], 18),
                str(r["total_trades"]),
                f"{r['win_rate']:.1%}",
                _pnl(r["total_pnl"]),
                sharpe,
                f"[{_ORANGE}]{r['max_drawdown']:.4f}[/]",
            )

    def update_kpis(self, r: sqlite3.Row) -> None:
        self.query_one("#bt-kpi-trades",  MetricCard).set_value(f"[{_BLUE}]{r['total_trades']}[/]")
        self.query_one("#bt-kpi-wr",      MetricCard).set_value(f"[{_GREEN}]{r['win_rate']:.1%}[/]")
        self.query_one("#bt-kpi-pnl",     MetricCard).set_value(_pnl(r["total_pnl"]))
        sharpe = f"{r['sharpe']:.3f}" if r["sharpe"] is not None else f"[{_SUB}]—[/]"
        self.query_one("#bt-kpi-sharpe",  MetricCard).set_value(sharpe)
        self.query_one("#bt-kpi-dd",      MetricCard).set_value(f"[{_ORANGE}]{r['max_drawdown']:.4f}[/]")


# ── main app ──────────────────────────────────────────────────────────────────

class PredicArbDashboard(App):
    TITLE = "PredicArb"

    BINDINGS = [
        Binding("r", "refresh_now",    "Refresh",       show=True),
        Binding("d", "remove_ticker",  "Remove ticker", show=True),
        Binding("q", "quit",           "Quit",          show=True),
    ]

    CSS = _BASE_CSS

    def __init__(self, db_path: Path, refresh_interval: float = 5.0) -> None:
        super().__init__()
        self._db       = Database(db_path)
        self._db.init()
        self._interval = refresh_interval

    def compose(self) -> ComposeResult:
        yield TopBar()
        with TabbedContent():
            with TabPane("  Markets  ", id="tab-markets"):
                yield LiveMarketsTab()
            with TabPane("  Add Ticker  ", id="tab-add"):
                yield AddTickerTab(self._db)
            with TabPane("  Backtest  ", id="tab-backtest"):
                yield BacktestTab(self._db)
        yield Footer()

    def on_mount(self) -> None:
        self._do_refresh()
        self.set_interval(self._interval, self._do_refresh)
        self.set_interval(1.0, self.query_one(TopBar).tick)

    def action_refresh_now(self) -> None:
        self._do_refresh()

    def action_remove_ticker(self) -> None:
        try:
            tab     = self.query_one(AddTickerTab)
            tbl     = tab.query_one("#tbl-watched", DataTable)
            row_idx = tbl.cursor_row
            rows    = self._db.watched()
            if row_idx < len(rows):
                ticker = rows[row_idx]["ticker"]
                self._db.remove_watched(ticker)
                tab.query_one("#add-status", Static).update(
                    f"[{_ORANGE}]✓  Removed {_ticker(ticker, 20)}[/]"
                )
                tab.fill(self._db.watched())
        except Exception:
            pass

    def _do_refresh(self) -> None:
        try:
            self.query_one(SummaryRow).update(self._db.summary())
        except Exception:
            pass
        self._fill_positions()
        self._fill_open_orders()
        self._fill_signals()
        self._fill_fills()
        try:
            self.query_one(TicksPanel).fill(self._db.ticks_latest())
        except Exception:
            pass
        try:
            self.query_one(AddTickerTab).fill(self._db.watched())
        except Exception:
            pass
        try:
            self.query_one(BacktestTab).fill_history(self._db.backtest_runs())
        except Exception:
            pass

    def _panel(self, tbl_id: str) -> Panel:
        return next(
            p for p in self.query(Panel)
            if any(w.id == tbl_id for w in p.query(DataTable))
        )

    def _fill_positions(self) -> None:
        try:
            rows = []
            for r in self._db.positions():
                rows.append([
                    f"[{_BLUE}]{_ticker(r['ticker'], 12)}[/]",
                    str(r["yes_count"]),
                    f"{r['avg_cost']:.4f}",
                    _pnl(r["realized_pnl"]),
                    _rome_hms(r["ts_utc"]),
                ])
            self._panel("tbl-positions").fill(rows)
        except Exception:
            pass

    def _fill_open_orders(self) -> None:
        try:
            rows = []
            for r in self._db.open_orders():
                rows.append([
                    str(r["id"]),
                    f"[{_BLUE}]{_ticker(r['ticker'], 12)}[/]",
                    _side(r["side"]),
                    str(r["count"]),
                    f"{r['price']:.4f}",
                    _rome_hms(r["ts_utc"]),
                ])
            self._panel("tbl-open-orders").fill(rows)
        except Exception:
            pass

    def _fill_signals(self) -> None:
        try:
            rows = []
            for r in self._db.signals():
                mid = r["market_mid"]
                rows.append([
                    _rome_hms(r["ts_utc"]),
                    f"[{_BLUE}]{_ticker(r['ticker'], 12)}[/]",
                    f"{mid:.3f}" if mid is not None else f"[{_SUB}]—[/]",
                    _edge(r["edge"]),
                    _decision(r["decision"]),
                ])
            self._panel("tbl-signals").fill(rows)
        except Exception:
            pass

    def _fill_fills(self) -> None:
        try:
            rows = []
            for r in self._db.fills():
                rows.append([
                    _rome_hms(r["ts_utc"]),
                    f"[{_BLUE}]{_ticker(r['ticker'], 12)}[/]",
                    _side(r["side"]),
                    str(r["count"]),
                    f"{r['price']:.4f}",
                ])
            self._panel("tbl-fills").fill(rows)
        except Exception:
            pass
