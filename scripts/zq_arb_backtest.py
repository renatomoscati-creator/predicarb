"""
ZQ Arb Backtest — evaluates the historical ZQ vs Polymarket edge strategy.

Methodology:
  1. Fetch historical ZQ futures prices (Yahoo Finance) → compute ZQ-implied probs
  2. Fetch historical Polymarket mid-prices (CLOB prices-history endpoint)
  3. Align on timestamp, compute edge = ZQ_prob − Poly_mid
  4. Simulate: enter when |edge| ≥ min_edge, exit when edge converges or hold limit elapsed
  5. Report win rate, avg convergence time, and P&L distribution

Trade logic:
  SELL_YES — ZQ < Poly (Polymarket overpriced): enter at mid, exit when Poly drops to ZQ level
  BUY_YES  — ZQ > Poly (Polymarket underpriced): enter at mid, exit when Poly rises to ZQ level
  P&L per contract = |exit_mid − entry_mid| (positive if convergence happened)

Fidelity modes:
  --fidelity 1440  Daily bars (default) — hold period in days (--hold-days)
  --fidelity 60    Hourly bars          — hold period in hours (--hold-hours)
  --fidelity 1     Minute bars          — hold period in hours (--hold-hours); ~5d ZQ history

Usage:
    python3 scripts/zq_arb_backtest.py                           # daily mode, defaults
    python3 scripts/zq_arb_backtest.py --fidelity 60 --hold-hours 48   # hourly mode
    python3 scripts/zq_arb_backtest.py --fidelity 1  --hold-hours 12   # minute mode
    python3 scripts/zq_arb_backtest.py --min-edge 0.05                 # custom threshold
    python3 scripts/zq_arb_backtest.py --json                          # machine-readable
"""
from __future__ import annotations

import argparse
import bisect
import calendar
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from src.benchmark.zq_benchmark import (
    _LATE_MEETING_DAY_THRESHOLD,
    _MONTH_CODES,
    _build_prob_distribution,
    _implied_effr,
    _next_month,
    _prior_month,
    _zq_ticker,
)
from scripts.fed_arb_scanner import FED_MARKETS, FedMarket

_YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_CLOB_BASE = "https://clob.polymarket.com"
_TIMEOUT = 12.0

# Maps CLOB fidelity (minutes) to Yahoo Finance interval + range params
_FIDELITY_TO_ZQ_INTERVAL = {1440: "1d",  60: "1h",  1: "5m"}
_FIDELITY_TO_ZQ_RANGE    = {1440: "1y",  60: "60d", 1: "5d"}


# ---------------------------------------------------------------------------
# Yahoo Finance — historical ZQ prices
# ---------------------------------------------------------------------------

def _fetch_zq_history(
    year: int, month: int, history_days: int = 365
) -> Dict[date, float]:
    """
    Fetch daily closing prices for one ZQ contract from Yahoo Finance.
    Returns {date: closing_price}. Missing days (weekends/holidays) are omitted.
    """
    ticker = _zq_ticker(year, month)
    if history_days <= 90:
        range_str = "3mo"
    elif history_days <= 180:
        range_str = "6mo"
    else:
        range_str = "1y"
    try:
        resp = requests.get(
            f"{_YAHOO_BASE}/{ticker}",
            params={"interval": "1d", "range": range_str},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json().get("chart", {}).get("result")
        if not result:
            return {}
        r = result[0]
        timestamps = r.get("timestamp", [])
        closes = r.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        out: Dict[date, float] = {}
        for ts, price in zip(timestamps, closes):
            if price is None:
                continue
            d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
            out[d] = float(price)
        return out
    except Exception as exc:
        print(f"  [ZQ history] {ticker}: {exc}", file=sys.stderr)
        return {}


def _compute_daily_probs(
    fomc_date: date, history_days: int = 365
) -> Dict[date, float]:
    """
    For each historical day, compute the ZQ-implied P(≥1 cut at fomc_date).
    Uses the same contract selection logic as fetch_meeting_snapshot.
    Returns {date: probability}.
    """
    D = fomc_date.day
    M = fomc_date.month
    Y = fomc_date.year
    N = calendar.monthrange(Y, M)[1]

    py, pm = _prior_month(Y, M)
    ny, nm = _next_month(Y, M)
    late_meeting = D >= _LATE_MEETING_DAY_THRESHOLD

    if late_meeting:
        # pre: prior month (or meeting month as fallback); post: next month
        hist_pre_primary = _fetch_zq_history(py, pm, history_days)
        hist_pre_fallback = _fetch_zq_history(Y, M, history_days)
        hist_post = _fetch_zq_history(ny, nm, history_days)
    else:
        hist_pre_primary = _fetch_zq_history(py, pm, history_days)
        hist_pre_fallback = {}
        hist_post = _fetch_zq_history(Y, M, history_days)

    # Merge primary + fallback for pre (primary wins)
    hist_pre = {**hist_pre_fallback, **hist_pre_primary}

    all_dates = set(hist_pre.keys()) & set(hist_post.keys())

    result: Dict[date, float] = {}
    for d in sorted(all_dates):
        if d >= fomc_date:
            continue   # don't use data on/after the meeting (market has resolved partially)
        price_pre = hist_pre[d]
        price_post = hist_post[d]
        effr_pre = _implied_effr(price_pre)
        effr_post_month = _implied_effr(price_post)

        if late_meeting:
            effr_post = effr_post_month
        else:
            days_before = D - 1
            days_after = N - D + 1
            if days_after <= 0:
                continue
            effr_post = (effr_post_month * N - effr_pre * days_before) / days_after

        expected_change_bps = (effr_post - effr_pre) * 100
        probs = _build_prob_distribution(expected_change_bps)
        prob_at_least_1_cut = sum(p for bps, p in probs.items() if bps <= -25)
        result[d] = prob_at_least_1_cut

    return result


def _fetch_zq_history_intraday(
    year: int, month: int, interval: str = "1h", history_days: int = 60
) -> Dict[datetime, float]:
    """
    Fetch intraday ZQ prices from Yahoo Finance at the given interval ("1h", "5m", etc.).
    Returns {datetime_utc: closing_price}. Timestamps are UTC datetimes (not dates).
    Note: Yahoo Finance has ~15min delay on futures data.
    """
    ticker = _zq_ticker(year, month)
    _interval_to_range = {"1d": "1y", "1h": "60d", "5m": "5d"}
    range_str = _interval_to_range.get(interval, "60d")
    try:
        resp = requests.get(
            f"{_YAHOO_BASE}/{ticker}",
            params={"interval": interval, "range": range_str},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json().get("chart", {}).get("result")
        if not result:
            return {}
        r = result[0]
        timestamps = r.get("timestamp", [])
        closes = r.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        out: Dict[datetime, float] = {}
        for ts, price in zip(timestamps, closes):
            if price is None:
                continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            out[dt] = float(price)
        return out
    except Exception as exc:
        print(f"  [ZQ intraday] {ticker} {interval}: {exc}", file=sys.stderr)
        return {}


def _compute_intraday_probs(
    fomc_date: date, zq_interval: str = "1h", history_days: int = 60
) -> Dict[datetime, float]:
    """
    Like _compute_daily_probs but at intraday granularity.
    Returns {datetime_utc: zq_implied_probability}.
    Uses forward-fill across CME trading hours — gaps (overnight, weekends) carry
    the last known price.
    """
    D = fomc_date.day
    M = fomc_date.month
    Y = fomc_date.year
    N = calendar.monthrange(Y, M)[1]

    py, pm = _prior_month(Y, M)
    ny, nm = _next_month(Y, M)
    late_meeting = D >= _LATE_MEETING_DAY_THRESHOLD

    if late_meeting:
        hist_pre_primary = _fetch_zq_history_intraday(py, pm, zq_interval, history_days)
        hist_pre_fallback = _fetch_zq_history_intraday(Y, M, zq_interval, history_days)
        hist_post = _fetch_zq_history_intraday(ny, nm, zq_interval, history_days)
    else:
        hist_pre_primary = _fetch_zq_history_intraday(py, pm, zq_interval, history_days)
        hist_pre_fallback = {}
        hist_post = _fetch_zq_history_intraday(Y, M, zq_interval, history_days)

    hist_pre = {**hist_pre_fallback, **hist_pre_primary}

    all_ts = set(hist_pre.keys()) & set(hist_post.keys())

    result: Dict[datetime, float] = {}
    fomc_dt = datetime(fomc_date.year, fomc_date.month, fomc_date.day, tzinfo=timezone.utc)
    for dt in sorted(all_ts):
        if dt >= fomc_dt:
            continue
        price_pre = hist_pre[dt]
        price_post = hist_post[dt]
        effr_pre = _implied_effr(price_pre)
        effr_post_month = _implied_effr(price_post)

        if late_meeting:
            effr_post = effr_post_month
        else:
            days_before = D - 1
            days_after = N - D + 1
            if days_after <= 0:
                continue
            effr_post = (effr_post_month * N - effr_pre * days_before) / days_after

        expected_change_bps = (effr_post - effr_pre) * 100
        probs = _build_prob_distribution(expected_change_bps)
        prob_at_least_1_cut = sum(p for bps, p in probs.items() if bps <= -25)
        result[dt] = prob_at_least_1_cut

    return result


# ---------------------------------------------------------------------------
# Polymarket CLOB — historical mid-prices
# ---------------------------------------------------------------------------

_CLOB_MAX_WINDOW_DAYS = 14   # CLOB /prices-history rejects windows > ~14 days


def _fetch_poly_history(token_id: str, history_days: int = 180) -> Dict[date, float]:
    """
    Fetch daily Polymarket mid-prices from the CLOB prices-history endpoint.
    The CLOB caps each request to ~14 days; we paginate backwards in chunks.
    Returns {date: mid_price}.
    """
    out: Dict[date, float] = {}
    end_ts = int(datetime.now(timezone.utc).timestamp())
    total_fetched = 0

    while total_fetched < history_days:
        chunk = min(_CLOB_MAX_WINDOW_DAYS, history_days - total_fetched)
        start_ts = end_ts - chunk * 86400
        try:
            resp = requests.get(
                f"{_CLOB_BASE}/prices-history",
                params={
                    "market": token_id,
                    "startTs": start_ts,
                    "endTs": end_ts,
                    "fidelity": 1440,
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            for point in resp.json().get("history", []):
                t_raw = point.get("t")
                p_raw = point.get("p")
                if t_raw is None or p_raw is None:
                    continue
                t_val = int(float(t_raw))
                if t_val > 1e11:
                    t_val //= 1000
                d = datetime.fromtimestamp(t_val, tz=timezone.utc).date()
                out[d] = float(p_raw)
        except Exception as exc:
            print(f"  [Poly history chunk] {token_id[:12]}…: {exc}", file=sys.stderr)

        end_ts = start_ts
        total_fetched += chunk

    return out


def _fetch_poly_history_intraday(
    token_id: str, history_days: int = 60, fidelity: int = 60
) -> Dict[datetime, float]:
    """
    Fetch intraday Polymarket mid-prices at the given fidelity (minutes).
    Returns {datetime_utc: mid_price}. Same 14-day pagination as the daily version.
    """
    out: Dict[datetime, float] = {}
    end_ts = int(datetime.now(timezone.utc).timestamp())
    total_fetched = 0

    while total_fetched < history_days:
        chunk = min(_CLOB_MAX_WINDOW_DAYS, history_days - total_fetched)
        start_ts = end_ts - chunk * 86400
        try:
            resp = requests.get(
                f"{_CLOB_BASE}/prices-history",
                params={
                    "market": token_id,
                    "startTs": start_ts,
                    "endTs": end_ts,
                    "fidelity": fidelity,
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            for point in resp.json().get("history", []):
                t_raw = point.get("t")
                p_raw = point.get("p")
                if t_raw is None or p_raw is None:
                    continue
                t_val = int(float(t_raw))
                if t_val > 1e11:
                    t_val //= 1000
                dt = datetime.fromtimestamp(t_val, tz=timezone.utc)
                out[dt] = float(p_raw)
        except Exception as exc:
            print(f"  [Poly intraday chunk] {token_id[:12]}…: {exc}", file=sys.stderr)

        end_ts = start_ts
        total_fetched += chunk

    return out


def _align_zq_to_poly(
    zq: Dict[datetime, float],
    poly: Dict[datetime, float],
) -> Dict[datetime, Tuple[float, float]]:
    """
    Forward-fill ZQ probability onto Polymarket timestamps.

    For each Polymarket bar, finds the most recent ZQ bar at or before that
    timestamp using bisect. This naturally handles the ~15-min Yahoo Finance delay
    and CME session gaps (overnight, weekends).

    Returns {poly_ts: (zq_prob, poly_mid)}.
    """
    if not zq or not poly:
        return {}

    zq_ts_sorted = sorted(zq.keys())
    zq_vals = [zq[ts] for ts in zq_ts_sorted]

    result: Dict[datetime, Tuple[float, float]] = {}
    for poly_ts in sorted(poly.keys()):
        # Find insertion point — go one left to get most recent ZQ at or before poly_ts
        idx = bisect.bisect_right(zq_ts_sorted, poly_ts) - 1
        if idx < 0:
            continue   # No ZQ data before this Poly bar
        result[poly_ts] = (zq_vals[idx], poly[poly_ts])

    return result


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

@dataclass
class SimulatedTrade:
    market_desc: str
    entry_date: date
    exit_date: date
    direction: str          # "BUY_YES" | "SELL_YES"
    entry_mid: float
    exit_mid: float
    entry_zq_prob: float
    exit_zq_prob: float
    entry_edge: float       # zq_prob - poly_mid at entry
    exit_edge: float        # edge at exit
    days_held: int
    pnl: float              # per unit (0–1 scale)
    converged: bool         # True if edge shrank to < half of entry edge
    # Intraday fields (None in daily mode)
    entry_ts: Optional[datetime] = None
    exit_ts: Optional[datetime] = None
    hours_held: Optional[float] = None


def _simulate_market(
    fm: FedMarket,
    zq_probs: Dict[date, float],
    poly_mids: Dict[date, float],
    min_edge: float,
    hold_days: int,
) -> List[SimulatedTrade]:
    """
    Simulate entries and exits for one Fed market.

    Entry rule : |edge| ≥ min_edge (no prior open position)
    Exit rules (first hit):
      1. Edge falls below min_edge/2 in the opposite direction → converged
      2. Edge reverses sign beyond min_edge → stop-out / re-entry candidate
      3. hold_days elapsed → time-based exit
    """
    trades: List[SimulatedTrade] = []
    all_dates = sorted(set(zq_probs.keys()) & set(poly_mids.keys()))

    in_position = False
    entry_date: Optional[date] = None
    entry_mid: Optional[float] = None
    entry_zq: Optional[float] = None
    entry_edge: Optional[float] = None
    direction: Optional[str] = None

    for d in all_dates:
        zq = zq_probs[d]
        mid = poly_mids[d]
        edge = zq - mid

        if in_position:
            assert entry_date and entry_mid is not None and entry_zq is not None
            days_held = (d - entry_date).days

            # Check exit conditions
            converged = abs(edge) < abs(entry_edge) / 2   # type: ignore[operator]
            reversed_sign = (entry_edge > 0 and edge < -min_edge) or \
                            (entry_edge < 0 and edge > min_edge)
            time_exit = days_held >= hold_days

            if converged or reversed_sign or time_exit:
                if direction == "BUY_YES":
                    pnl = mid - entry_mid
                else:
                    pnl = entry_mid - mid

                trades.append(SimulatedTrade(
                    market_desc=fm.description,
                    entry_date=entry_date,
                    exit_date=d,
                    direction=direction,  # type: ignore[arg-type]
                    entry_mid=entry_mid,
                    exit_mid=mid,
                    entry_zq_prob=entry_zq,
                    exit_zq_prob=zq,
                    entry_edge=entry_edge,  # type: ignore[arg-type]
                    exit_edge=edge,
                    days_held=days_held,
                    pnl=pnl,
                    converged=converged,
                ))
                in_position = False

        if not in_position and abs(edge) >= min_edge:
            in_position = True
            entry_date = d
            entry_mid = mid
            entry_zq = zq
            entry_edge = edge
            direction = "SELL_YES" if edge < 0 else "BUY_YES"

    return trades


def _simulate_market_intraday(
    fm: FedMarket,
    aligned: Dict[datetime, Tuple[float, float]],
    min_edge: float,
    hold_hours: float,
) -> List[SimulatedTrade]:
    """
    Intraday version of _simulate_market. Takes pre-aligned {ts: (zq_prob, poly_mid)}
    data and simulates entries/exits at hourly or minute granularity.
    Uses hours_held instead of days_held for exit timing.
    """
    trades: List[SimulatedTrade] = []
    all_ts = sorted(aligned.keys())

    in_position = False
    entry_ts: Optional[datetime] = None
    entry_mid: Optional[float] = None
    entry_zq: Optional[float] = None
    entry_edge: Optional[float] = None
    direction: Optional[str] = None

    for ts in all_ts:
        zq, mid = aligned[ts]
        edge = zq - mid

        if in_position:
            assert entry_ts and entry_mid is not None and entry_zq is not None
            hours_held = (ts - entry_ts).total_seconds() / 3600

            converged = abs(edge) < abs(entry_edge) / 2   # type: ignore[operator]
            reversed_sign = (entry_edge > 0 and edge < -min_edge) or \
                            (entry_edge < 0 and edge > min_edge)
            time_exit = hours_held >= hold_hours

            if converged or reversed_sign or time_exit:
                if direction == "BUY_YES":
                    pnl = mid - entry_mid
                else:
                    pnl = entry_mid - mid

                trades.append(SimulatedTrade(
                    market_desc=fm.description,
                    entry_date=entry_ts.date(),
                    exit_date=ts.date(),
                    direction=direction,  # type: ignore[arg-type]
                    entry_mid=entry_mid,
                    exit_mid=mid,
                    entry_zq_prob=entry_zq,
                    exit_zq_prob=zq,
                    entry_edge=entry_edge,  # type: ignore[arg-type]
                    exit_edge=edge,
                    days_held=int(hours_held // 24),
                    pnl=pnl,
                    converged=converged,
                    entry_ts=entry_ts,
                    exit_ts=ts,
                    hours_held=round(hours_held, 1),
                ))
                in_position = False

        if not in_position and abs(edge) >= min_edge:
            in_position = True
            entry_ts = ts
            entry_mid = mid
            entry_zq = zq
            entry_edge = edge
            direction = "SELL_YES" if edge < 0 else "BUY_YES"

    return trades


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _fmt_pnl(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v*100:.1f}¢"


def _fmt_pct(v: float) -> str:
    return f"{v*100:.1f}%"


def print_results(
    all_trades: List[SimulatedTrade],
    market_summaries: List[dict],
    min_edge: float,
    hold_days: int,
    history_days: int,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*112}")
    print(f"  ZQ Arb Backtest  |  {now}  |  min_edge={min_edge:.3f}  hold_days={hold_days}  history={history_days}d")
    print(f"{'='*112}")

    # Per-market summary
    print(f"\n  {'Market':<34} {'Days':<6} {'Signals':<9} {'Converged':<11} "
          f"{'Avg days':<10} {'Win%':<7} {'Avg P&L':<10} {'Total P&L'}")
    print(f"  {'-'*107}")

    for s in market_summaries:
        print(
            f"  {s['desc']:<34} {s['data_days']:<6} {s['n_signals']:<9} "
            f"{s['n_converged']}/{s['n_signals']:<9} "
            f"{s['avg_days']:<10} {s['win_pct']:<7} "
            f"{s['avg_pnl']:<10} {s['total_pnl']}"
        )

    if not all_trades:
        print("\n  No signals found in the historical window.")
        return

    # Aggregate stats
    total = len(all_trades)
    wins = sum(1 for t in all_trades if t.pnl > 0)
    converged = sum(1 for t in all_trades if t.converged)
    avg_days = sum(t.days_held for t in all_trades) / total
    total_pnl = sum(t.pnl for t in all_trades)
    avg_pnl = total_pnl / total

    print(f"\n  {'─'*107}")
    print(f"  TOTAL: {total} trades | Win rate {wins/total:.0%} | Converged {converged/total:.0%} "
          f"| Avg hold {avg_days:.1f}d | Avg P&L {_fmt_pnl(avg_pnl)} | Total {_fmt_pnl(total_pnl)}")

    # Trade detail
    print(f"\n  {'Date':<12} {'Market':<33} {'Dir':<9} {'Entry':>7} {'Exit':>7} "
          f"{'ZQ@entry':>9} {'ZQ@exit':>8} {'Edge':>7} {'Days':>5} {'P&L':>8} {'Conv'}")
    print(f"  {'-'*120}")
    for t in sorted(all_trades, key=lambda x: x.entry_date):
        conv_flag = "yes" if t.converged else "no"
        print(
            f"  {t.entry_date}  {t.market_desc:<33} {t.direction:<9} "
            f"{_fmt_pct(t.entry_mid):>7} {_fmt_pct(t.exit_mid):>7} "
            f"{_fmt_pct(t.entry_zq_prob):>9} {_fmt_pct(t.exit_zq_prob):>8} "
            f"{t.entry_edge:>+7.3f} {t.days_held:>5} {_fmt_pnl(t.pnl):>8} {conv_flag}"
        )

    print(f"\n  Note: P&L per unit (0–1 contract scale); uses daily mid-price (no spread)")
    print(f"        'Converged' = edge shrank below half of entry edge")


def print_results_intraday(
    all_trades: List[SimulatedTrade],
    market_summaries: List[dict],
    min_edge: float,
    hold_hours: float,
    history_days: int,
    fidelity: int,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fid_label = f"{fidelity}min" if fidelity < 1440 else "daily"
    print(f"\n{'='*116}")
    print(f"  ZQ Arb Backtest (intraday)  |  {now}  |  "
          f"min_edge={min_edge:.3f}  hold_hours={hold_hours:.0f}h  "
          f"fidelity={fid_label}  history={history_days}d")
    print(f"{'='*116}")

    print(f"\n  {'Market':<34} {'Hrs':<6} {'Signals':<9} {'Converged':<11} "
          f"{'Avg hold':<10} {'Win%':<7} {'Avg P&L':<10} {'Total P&L'}")
    print(f"  {'-'*107}")

    for s in market_summaries:
        print(
            f"  {s['desc']:<34} {s['data_hours']:<6} {s['n_signals']:<9} "
            f"{s['n_converged']}/{s['n_signals']:<9} "
            f"{s['avg_hold']:<10} {s['win_pct']:<7} "
            f"{s['avg_pnl']:<10} {s['total_pnl']}"
        )

    if not all_trades:
        print("\n  No signals found in the historical window.")
        return

    total = len(all_trades)
    wins = sum(1 for t in all_trades if t.pnl > 0)
    converged = sum(1 for t in all_trades if t.converged)
    avg_h = sum(t.hours_held or 0 for t in all_trades) / total
    total_pnl = sum(t.pnl for t in all_trades)
    avg_pnl = total_pnl / total

    print(f"\n  {'─'*107}")
    print(f"  TOTAL: {total} trades | Win rate {wins/total:.0%} | Converged {converged/total:.0%} "
          f"| Avg hold {avg_h:.1f}h | Avg P&L {_fmt_pnl(avg_pnl)} | Total {_fmt_pnl(total_pnl)}")

    print(f"\n  {'Timestamp':<20} {'Market':<33} {'Dir':<9} {'Entry':>7} {'Exit':>7} "
          f"{'ZQ@entry':>9} {'ZQ@exit':>8} {'Edge':>7} {'Hours':>6} {'P&L':>8} {'Conv'}")
    print(f"  {'-'*124}")
    for t in sorted(all_trades, key=lambda x: x.entry_ts or x.entry_date):
        ts_label = t.entry_ts.strftime("%Y-%m-%d %H:%M") if t.entry_ts else str(t.entry_date)
        conv_flag = "yes" if t.converged else "no"
        print(
            f"  {ts_label:<20}  {t.market_desc:<33} {t.direction:<9} "
            f"{_fmt_pct(t.entry_mid):>7} {_fmt_pct(t.exit_mid):>7} "
            f"{_fmt_pct(t.entry_zq_prob):>9} {_fmt_pct(t.exit_zq_prob):>8} "
            f"{t.entry_edge:>+7.3f} {t.hours_held or 0:>6.1f} {_fmt_pnl(t.pnl):>8} {conv_flag}"
        )

    print(f"\n  Note: P&L per unit (0–1 contract scale); uses {fid_label} mid-price (no spread)")
    print(f"        'Converged' = edge shrank below half of entry edge")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="ZQ vs Polymarket arb historical backtest")
    parser.add_argument("--min-edge", type=float, default=0.03,
                        help="Minimum |edge| to enter a position (default: 0.03)")
    parser.add_argument("--hold-days", type=int, default=10,
                        help="Max hold period in days — daily mode only (default: 10)")
    parser.add_argument("--hold-hours", type=float, default=None,
                        help="Max hold period in hours — intraday mode (overrides hold-days * 24)")
    parser.add_argument("--history", type=int, default=180,
                        help="Days of history to fetch (default: 180)")
    parser.add_argument("--fidelity", type=int, choices=[1440, 60, 1], default=1440,
                        help="Bar granularity in minutes: 1440=daily, 60=hourly, 1=minute (default: 1440)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    all_trades: List[SimulatedTrade] = []
    market_summaries = []
    intraday_mode = args.fidelity != 1440

    hold_hours = args.hold_hours or (args.hold_days * 24)
    zq_interval = _FIDELITY_TO_ZQ_INTERVAL[args.fidelity]
    fid_label = f"{args.fidelity}min" if intraday_mode else "daily"

    print(
        f"Fetching {args.history}d of ZQ ({zq_interval}) + Polymarket ({fid_label}) "
        f"history for {len(FED_MARKETS)} markets…",
        file=sys.stderr,
    )

    for i, fm in enumerate(FED_MARKETS):
        print(f"  [{i+1}/{len(FED_MARKETS)}] {fm.description}…", file=sys.stderr)

        if intraday_mode:
            zq_intraday = _compute_intraday_probs(fm.fomc_date, zq_interval, args.history)
            poly_intraday = _fetch_poly_history_intraday(fm.token_id, args.history, args.fidelity)
            aligned = _align_zq_to_poly(zq_intraday, poly_intraday)
            trades = _simulate_market_intraday(fm, aligned, args.min_edge, hold_hours)
            data_pts = len(aligned)
            n = len(trades)
            wins = sum(1 for t in trades if t.pnl > 0)
            conv = sum(1 for t in trades if t.converged)
            avg_hold_h = (sum(t.hours_held or 0 for t in trades) / n) if n else 0.0
            total_pnl = sum(t.pnl for t in trades)
            avg_pnl = total_pnl / n if n else 0.0
            market_summaries.append({
                "desc": fm.description,
                "fomc_date": fm.fomc_date.isoformat(),
                "data_hours": data_pts,
                "n_signals": n,
                "n_converged": conv,
                "avg_hold": f"{avg_hold_h:.1f}h" if n else "  -",
                "win_pct": f"{wins/n:.0%}" if n else " -",
                "avg_pnl": _fmt_pnl(avg_pnl) if n else "    -",
                "total_pnl": _fmt_pnl(total_pnl) if n else "    -",
            })
        else:
            zq_probs = _compute_daily_probs(fm.fomc_date, history_days=args.history)
            poly_mids = _fetch_poly_history(fm.token_id, history_days=args.history)
            trades = _simulate_market(fm, zq_probs, poly_mids, args.min_edge, args.hold_days)
            data_pts = len(set(zq_probs.keys()) & set(poly_mids.keys()))
            n = len(trades)
            wins = sum(1 for t in trades if t.pnl > 0)
            conv = sum(1 for t in trades if t.converged)
            avg_days = (sum(t.days_held for t in trades) / n) if n else 0.0
            total_pnl = sum(t.pnl for t in trades)
            avg_pnl = total_pnl / n if n else 0.0
            market_summaries.append({
                "desc": fm.description,
                "fomc_date": fm.fomc_date.isoformat(),
                "data_days": data_pts,
                "n_signals": n,
                "n_converged": conv,
                "avg_days": f"{avg_days:.1f}d" if n else "  -",
                "win_pct": f"{wins/n:.0%}" if n else " -",
                "avg_pnl": _fmt_pnl(avg_pnl) if n else "    -",
                "total_pnl": _fmt_pnl(total_pnl) if n else "    -",
            })

        all_trades.extend(trades)

    if args.json:
        trades_out = []
        for t in sorted(all_trades, key=lambda x: x.entry_ts or datetime(x.entry_date.year, x.entry_date.month, x.entry_date.day, tzinfo=timezone.utc)):
            trades_out.append({
                "market": t.market_desc,
                "entry_ts": t.entry_ts.isoformat() if t.entry_ts else t.entry_date.isoformat(),
                "exit_ts": t.exit_ts.isoformat() if t.exit_ts else t.exit_date.isoformat(),
                "direction": t.direction,
                "entry_mid": round(t.entry_mid, 4),
                "exit_mid": round(t.exit_mid, 4),
                "entry_zq_prob": round(t.entry_zq_prob, 4),
                "exit_zq_prob": round(t.exit_zq_prob, 4),
                "entry_edge": round(t.entry_edge, 4),
                "exit_edge": round(t.exit_edge, 4),
                "hours_held": t.hours_held,
                "days_held": t.days_held,
                "pnl": round(t.pnl, 4),
                "converged": t.converged,
            })
        print(json.dumps({
            "params": {
                "min_edge": args.min_edge,
                "hold_days": args.hold_days,
                "hold_hours": hold_hours,
                "history_days": args.history,
                "fidelity": args.fidelity,
            },
            "markets": market_summaries,
            "trades": trades_out,
        }, indent=2))
    elif intraday_mode:
        print_results_intraday(
            all_trades, market_summaries, args.min_edge, hold_hours, args.history, args.fidelity
        )
    else:
        print_results(all_trades, market_summaries, args.min_edge, args.hold_days, args.history)


if __name__ == "__main__":
    main()
