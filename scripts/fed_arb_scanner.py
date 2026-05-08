"""
Fed Arb Scanner — timing-mismatch detector between CME ZQ futures and Polymarket.

Strategy: CME 30-Day Fed Funds Futures (ZQ) are priced by institutional money and
reprice within seconds after economic data releases (CPI, NFP, PCE, FOMC minutes).
Polymarket typically lags by minutes to hours. This scanner detects that gap and
flags trading opportunities before prices converge.

Benchmark: ZQ-implied probability (CME FedWatch methodology)
  implied_EFFR(month) = 100 - ZQ_price
  For meeting day D in month M: post_rate derived via day-weighted formula
  P(≥1 cut at meeting) = probability mass below the cut threshold

Usage:
    python3 scripts/fed_arb_scanner.py                    # single scan
    python3 scripts/fed_arb_scanner.py --interval 30      # continuous, 30s refresh
    python3 scripts/fed_arb_scanner.py --min-edge 0.03    # custom threshold
    python3 scripts/fed_arb_scanner.py --json             # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from src.benchmark.zq_benchmark import ZqMeetingSnapshot, fetch_meeting_snapshot


# ---------------------------------------------------------------------------
# Constants — update when Fed changes rates
# ---------------------------------------------------------------------------
CURRENT_UPPER_BOUND = 3.75   # % (current Fed Funds target upper bound)
CLOB_BASE = "https://clob.polymarket.com"
_CACHE_TTL = 60              # seconds between ZQ re-fetches


# ---------------------------------------------------------------------------
# Polymarket Fed market registry
# ---------------------------------------------------------------------------

@dataclass
class FedMarket:
    token_id: str
    description: str
    # Which FOMC meeting this market expires at and how many cuts it tracks
    fomc_date: date          # meeting date for ZQ calculation
    n_cuts: int = 1          # P(≥n_cuts) from ZQ
    # Human-readable ZQ contract pair used (informational)
    zq_note: str = ""


FED_MARKETS: List[FedMarket] = [
    FedMarket(
        token_id="72902726884699850978994485999351956746728455432234001140689757304247177712068",
        description="Cut by March 2026 meeting",
        fomc_date=date(2026, 3, 19),
        zq_note="ZQH26 (Mar) — pre contract expired, limited accuracy",
    ),
    FedMarket(
        token_id="103665283657652818155183704290908892828145905842320815814218026501095230008126",
        description="Cut by April 2026 meeting",
        fomc_date=date(2026, 5, 7),   # use May as proxy (no Apr ZQ)
        zq_note="ZQJ26/K26 (Apr/May)",
    ),
    FedMarket(
        token_id="36209573008978970585450419316941249216001556298548579804182609641681769351835",
        description="Cut by June 2026 meeting",
        fomc_date=date(2026, 6, 18),
        zq_note="ZQK26/M26 (May/Jun)",
    ),
    FedMarket(
        token_id="55196773789328782968626753039325529600928079596441853086940968216494613088200",
        description="Cut by July 2026 meeting",
        fomc_date=date(2026, 7, 30),
        zq_note="ZQM26/Q26 (Jun/Aug, late-month)",
    ),
    FedMarket(
        token_id="3080129411996805379742751525600597838226998464163037042731747436895624822756",
        description="Cut by September 2026 meeting",
        fomc_date=date(2026, 9, 17),
        zq_note="ZQQ26/U26 (Aug/Sep)",
    ),
    FedMarket(
        token_id="44988145651657165705599548149732848615827306030030294113026805446816695818477",
        description="Cut by October 2026 meeting",
        fomc_date=date(2026, 10, 29),
        zq_note="ZQU26/X26 (Sep/Nov, late-month)",
    ),
    FedMarket(
        token_id="85002355202646770038788297383084634166875614093071220064343011133051368772502",
        description="Cut by December 2026 meeting",
        fomc_date=date(2026, 12, 10),
        zq_note="ZQX26/Z26 (Nov/Dec)",
    ),
]


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _fetch_poly_prices(token_id: str, timeout: float = 8.0) -> Tuple[Optional[float], Optional[float]]:
    """
    Return (bid, ask) from CLOB /price endpoint.
    side=buy  → best bid (what buyers will pay)
    side=sell → best ask (what sellers will accept)
    """
    try:
        r_bid = requests.get(f"{CLOB_BASE}/price", params={"token_id": token_id, "side": "buy"}, timeout=timeout)
        r_ask = requests.get(f"{CLOB_BASE}/price", params={"token_id": token_id, "side": "sell"}, timeout=timeout)
        bid = float(r_bid.json()["price"]) if r_bid.ok else None
        ask = float(r_ask.json()["price"]) if r_ask.ok else None
        return bid, ask
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# ZQ snapshot cache
# ---------------------------------------------------------------------------

_zq_cache: Dict[date, Tuple[datetime, ZqMeetingSnapshot]] = {}


def _get_zq(fomc_date: date) -> Optional[ZqMeetingSnapshot]:
    now = datetime.now(timezone.utc)
    if fomc_date in _zq_cache:
        cached_at, snap = _zq_cache[fomc_date]
        if (now - cached_at).total_seconds() < _CACHE_TTL:
            return snap
    try:
        snap = fetch_meeting_snapshot(fomc_date)
        _zq_cache[fomc_date] = (now, snap)
        return snap
    except Exception as exc:
        # Keep stale if available
        if fomc_date in _zq_cache:
            _, snap = _zq_cache[fomc_date]
            return snap
        print(f"  [ZQ {fomc_date}] {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    market: FedMarket
    poly_bid: Optional[float]
    poly_ask: Optional[float]
    poly_mid: Optional[float]
    zq_prob: Optional[float]
    zq_pre_rate: Optional[float]
    zq_post_rate: Optional[float]
    zq_expected_change_bps: Optional[float]
    edge: Optional[float]           # zq_prob - poly_mid (+ = poly underpriced → BUY)
    direction: str                  # "BUY" | "SELL" | "-"


def scan_once(min_edge: float = 0.03) -> List[ScanResult]:
    results = []
    for fm in FED_MARKETS:
        bid, ask = _fetch_poly_prices(fm.token_id)
        mid = (bid + ask) / 2 if bid is not None and ask is not None else None

        snap = _get_zq(fm.fomc_date)
        if snap:
            zq_prob = snap.prob_at_least_n_cuts(fm.n_cuts)
            pre = snap.pre_meeting_rate
            post = snap.post_meeting_rate
            chg = snap.expected_change_bps
        else:
            zq_prob = pre = post = chg = None

        edge = (zq_prob - mid) if zq_prob is not None and mid is not None else None

        if edge is not None and abs(edge) >= min_edge:
            direction = "BUY " if edge > 0 else "SELL"
        else:
            direction = "-"

        results.append(ScanResult(
            market=fm,
            poly_bid=bid, poly_ask=ask, poly_mid=mid,
            zq_prob=zq_prob,
            zq_pre_rate=pre, zq_post_rate=post, zq_expected_change_bps=chg,
            edge=edge,
            direction=direction,
        ))

    results.sort(key=lambda r: abs(r.edge or 0), reverse=True)
    return results


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _pct(v: Optional[float]) -> str:
    return f"{v*100:+.1f}%" if v is not None else "   n/a"

def _prob(v: Optional[float]) -> str:
    return f"{v:.3f}" if v is not None else "  n/a "

def _bps(v: Optional[float]) -> str:
    return f"{v:+.1f}bps" if v is not None else "    n/a"


def print_results(results: List[ScanResult], min_edge: float) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'='*108}")
    print(f"  Fed Rate Cut Arb  |  {now}  |  ZQ vs Polymarket  |  min_edge={min_edge:.3f}")
    print(f"  Current Fed Funds upper bound: {CURRENT_UPPER_BOUND:.2f}%  |  Source: CME 30-Day Fed Funds Futures via Yahoo Finance")
    print(f"{'='*108}")
    print(
        f"  {'Market':<32} {'Poly bid':>9} {'Poly ask':>9} {'Poly mid':>9} "
        f"{'ZQ prob':>8} {'ZQ Δbps':>9} {'Edge':>8} {'Signal':>7}"
    )
    print(f"  {'-'*103}")

    for r in results:
        bid_s  = f"{r.poly_bid:.3f}"  if r.poly_bid  is not None else "   n/a"
        ask_s  = f"{r.poly_ask:.3f}"  if r.poly_ask  is not None else "   n/a"
        mid_s  = f"{r.poly_mid:.3f}"  if r.poly_mid  is not None else "   n/a"
        zq_s   = _prob(r.zq_prob)
        chg_s  = _bps(r.zq_expected_change_bps)
        edge_s = _pct(r.edge)
        flag   = "  ◄ EDGE" if r.direction != "-" else ""
        print(
            f"  {r.market.description:<32} {bid_s:>9} {ask_s:>9} {mid_s:>9} "
            f"{zq_s:>8} {chg_s:>9} {edge_s:>8} {r.direction:>7}{flag}"
        )

    n_sig = sum(1 for r in results if r.direction != "-")
    print(f"\n  Signals: {n_sig}/{len(results)} above |edge| ≥ {min_edge:.3f}")
    print(
        f"\n  Legend:  Edge = ZQ_prob − Poly_mid"
        f"\n           BUY  = ZQ > Poly (Polymarket underpriced — buy YES before it catches up)"
        f"\n           SELL = ZQ < Poly (Polymarket overpriced — sell YES / buy NO)"
        f"\n  Note:    Yahoo Finance ZQ data has ~15 min delay; use real-time CME feed for"
        f"\n           fast timing trades (e.g. on CPI/NFP release days)"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="CME ZQ vs Polymarket Fed rate arb scanner")
    parser.add_argument("--min-edge", type=float, default=0.03,
                        help="Minimum |edge| to flag (default: 0.03)")
    parser.add_argument("--interval", type=int, default=0,
                        help="Continuous poll interval in seconds; 0 = run once")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    while True:
        try:
            results = scan_once(min_edge=args.min_edge)
        except Exception as exc:
            print(f"Scan error: {exc}", file=sys.stderr)
            results = []

        if args.json:
            print(json.dumps([{
                "description": r.market.description,
                "token_id": r.market.token_id,
                "fomc_date": r.market.fomc_date.isoformat(),
                "poly_bid": r.poly_bid,
                "poly_ask": r.poly_ask,
                "poly_mid": r.poly_mid,
                "zq_prob": r.zq_prob,
                "zq_pre_rate_pct": r.zq_pre_rate,
                "zq_post_rate_pct": r.zq_post_rate,
                "zq_expected_change_bps": r.zq_expected_change_bps,
                "edge": r.edge,
                "direction": r.direction,
                "zq_note": r.market.zq_note,
            } for r in results], indent=2))
        else:
            print_results(results, args.min_edge)

        if args.interval <= 0:
            break
        print(f"\n  Next scan in {args.interval}s  (Ctrl-C to stop)...")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
