"""
ZqFedWatch — Fed rate cut probability benchmark from 30-Day Fed Funds Futures (ZQ).

Uses the CME FedWatch methodology:
  implied_EFFR(month) = 100 - ZQ_price(month)

For a meeting on day D of month M with N calendar days:
  pre_meeting_rate  ≈ implied_EFFR(M-1)        [prior-month contract]
  post_meeting_rate = (implied_EFFR(M) * N - pre * (D-1)) / (N - D + 1)

From pre and post we derive the expected change, then convert to a probability
distribution over 25bps increments (the CME FedWatch rounding convention).

Data source: Yahoo Finance delayed quotes (free, no auth).
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_TIMEOUT = 10.0
_STEP = 0.25       # 25 bps

# CME ZQ contract month codes
_MONTH_CODES = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}


def _zq_ticker(year: int, month: int) -> str:
    code = _MONTH_CODES[month]
    yy = str(year)[-2:]
    return f"ZQ{code}{yy}.CBT"


def _fetch_zq_price(year: int, month: int, timeout: float = _TIMEOUT) -> Optional[float]:
    """Return the current market price for the ZQ contract for (year, month)."""
    ticker = _zq_ticker(year, month)
    try:
        resp = requests.get(
            f"{_YAHOO_BASE}/{ticker}",
            params={"interval": "1d", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=timeout,
        )
        resp.raise_for_status()
        result = resp.json().get("chart", {}).get("result")
        if not result:
            return None
        price = result[0].get("meta", {}).get("regularMarketPrice")
        return float(price) if price is not None else None
    except Exception as exc:
        logger.warning("ZQ fetch failed for %s: %s", ticker, exc)
        return None


def _implied_effr(zq_price: float) -> float:
    """Convert ZQ price to implied monthly-average EFFR."""
    return 100.0 - zq_price


def _prior_month(year: int, month: int) -> Tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


@dataclass
class ZqMeetingSnapshot:
    meeting_date: date
    pre_meeting_rate: float    # implied EFFR before the meeting (from prior ZQ)
    post_meeting_rate: float   # implied EFFR after the meeting
    expected_change_bps: float # post - pre, in basis points
    probs: Dict[int, float]    # bps_change → probability (e.g. {0: 0.75, -25: 0.25})
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ticker_pre: str = ""
    ticker_post: str = ""

    def prob_of_change_bps(self, bps: int) -> float:
        return self.probs.get(bps, 0.0)

    def prob_cut_n(self, n: int) -> float:
        """P(exactly n cuts of 25bps each)."""
        return self.probs.get(-n * 25, 0.0)

    def prob_at_least_n_cuts(self, n: int) -> float:
        return sum(p for bps, p in self.probs.items() if bps <= -n * 25)


def _build_prob_distribution(expected_change_bps: float) -> Dict[int, float]:
    """
    Given expected_change_bps, distribute probability across adjacent 25bps outcomes.

    CME FedWatch convention: only 25bps increments are considered.
    The probability is linearly interpolated between the two nearest outcomes.
    """
    step = int(_STEP * 100)   # 25
    # Round down to nearest step (most negative / most hikes)
    lower = int(expected_change_bps // step) * step
    upper = lower + step

    if lower == upper:
        return {lower: 1.0}

    # Interpolate
    frac = (expected_change_bps - lower) / step
    frac = max(0.0, min(1.0, frac))
    return {lower: 1.0 - frac, upper: frac}


_LATE_MEETING_DAY_THRESHOLD = 20  # if meeting is on or after this day, use next month for post


def _next_month(year: int, month: int) -> Tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def fetch_meeting_snapshot(
    meeting_date: date,
    timeout: float = _TIMEOUT,
) -> ZqMeetingSnapshot:
    """
    Fetch ZQ futures prices and compute implied rate change probabilities
    for the FOMC meeting on *meeting_date*.

    For meetings early in the month (day < threshold): uses prior-month ZQ as
    pre-meeting rate and meeting-month ZQ to derive post via day-weighting.

    For late-month meetings (day >= threshold, e.g. July 29-30, Oct 28-29):
    the day-weighted formula becomes numerically unstable (only 1-2 post-meeting
    days in the contract month). We instead treat the NEXT month's ZQ as the
    post-meeting rate directly, since the entire next month will be at the new rate.
    """
    D = meeting_date.day
    M = meeting_date.month
    Y = meeting_date.year
    N = calendar.monthrange(Y, M)[1]   # days in meeting month

    py, pm = _prior_month(Y, M)
    ny, nm = _next_month(Y, M)

    late_meeting = D >= _LATE_MEETING_DAY_THRESHOLD

    if late_meeting:
        # Pre: prior month ZQ (or meeting month if prior expired)
        price_pre_candidate = _fetch_zq_price(py, pm, timeout=timeout)
        price_pre_fallback = _fetch_zq_price(Y, M, timeout=timeout)
        price_pre = price_pre_candidate if price_pre_candidate is not None else price_pre_fallback
        ticker_pre = _zq_ticker(py, pm) if price_pre_candidate is not None else _zq_ticker(Y, M)
        # Post: next month ZQ (entire next month reflects post-meeting rate)
        price_post = _fetch_zq_price(ny, nm, timeout=timeout)
        ticker_post = _zq_ticker(ny, nm)
    else:
        price_pre = _fetch_zq_price(py, pm, timeout=timeout)
        ticker_pre = _zq_ticker(py, pm)
        price_post = _fetch_zq_price(Y, M, timeout=timeout)
        ticker_post = _zq_ticker(Y, M)

    if price_pre is None or price_post is None:
        raise ValueError(
            f"Could not fetch ZQ prices: pre={ticker_pre} ({price_pre}), "
            f"post={ticker_post} ({price_post})"
        )

    effr_pre = _implied_effr(price_pre)
    effr_post_month = _implied_effr(price_post)

    if late_meeting:
        # Next month's ZQ is entirely at the post-meeting rate
        effr_post = effr_post_month
    else:
        # Day-weighted: effr_month = (D-1)/N * effr_pre + (N-D+1)/N * effr_post
        days_before = D - 1
        days_after = N - D + 1
        effr_post = (effr_post_month * N - effr_pre * days_before) / days_after

    expected_change_bps = (effr_post - effr_pre) * 100  # percentage → bps

    probs = _build_prob_distribution(expected_change_bps)

    logger.info(
        "ZqFedWatch %s: pre=%.4f%% post=%.4f%% Δ=%.1fbps probs=%s",
        meeting_date,
        effr_pre,
        effr_post,
        expected_change_bps,
        {k: f"{v:.3f}" for k, v in probs.items()},
    )

    return ZqMeetingSnapshot(
        meeting_date=meeting_date,
        pre_meeting_rate=effr_pre,
        post_meeting_rate=effr_post,
        expected_change_bps=expected_change_bps,
        probs=probs,
        ticker_pre=ticker_pre,
        ticker_post=ticker_post,
    )


def fetch_rate_path(
    meetings: List[date],
    timeout: float = _TIMEOUT,
) -> List[ZqMeetingSnapshot]:
    """Fetch snapshots for a list of FOMC meeting dates."""
    snapshots = []
    for d in meetings:
        try:
            snap = fetch_meeting_snapshot(d, timeout=timeout)
            snapshots.append(snap)
        except Exception as exc:
            logger.warning("Failed to compute snapshot for %s: %s", d, exc)
    return snapshots
