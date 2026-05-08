"""
Unit tests for zq_arb_backtest intraday functions.

Tests _align_zq_to_poly (forward-fill) and _simulate_market_intraday
without any real network calls.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import pytest

from scripts.zq_arb_backtest import (
    SimulatedTrade,
    _align_zq_to_poly,
    _simulate_market_intraday,
)
from scripts.fed_arb_scanner import FedMarket


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(hour: int, minute: int = 0, day: int = 1) -> datetime:
    """Build a UTC datetime for 2026-01-{day} HH:MM."""
    return datetime(2026, 1, day, hour, minute, tzinfo=timezone.utc)


def _make_fm() -> FedMarket:
    return FedMarket(
        token_id="test_token",
        description="Test market",
        fomc_date=date(2026, 6, 18),
    )


# ---------------------------------------------------------------------------
# Tests for _align_zq_to_poly
# ---------------------------------------------------------------------------

class TestAlignZqToPoly:

    def test_exact_match(self):
        """When ZQ and Poly timestamps align exactly, each Poly bar gets its ZQ bar."""
        zq = {_dt(10): 0.40, _dt(11): 0.42, _dt(12): 0.38}
        poly = {_dt(10): 0.55, _dt(11): 0.57, _dt(12): 0.53}
        result = _align_zq_to_poly(zq, poly)
        assert result[_dt(10)] == (0.40, 0.55)
        assert result[_dt(11)] == (0.42, 0.57)
        assert result[_dt(12)] == (0.38, 0.53)

    def test_forward_fill_gaps(self):
        """Poly bars between ZQ bars get the most recent ZQ value (forward-fill)."""
        # ZQ on the hour, Poly every 15 minutes
        zq = {_dt(10): 0.30, _dt(11): 0.35}
        poly = {
            _dt(10):     0.50,
            _dt(10, 15): 0.51,
            _dt(10, 30): 0.52,
            _dt(10, 45): 0.53,
            _dt(11):     0.54,
        }
        result = _align_zq_to_poly(zq, poly)
        # Bars at 10:00, 10:15, 10:30, 10:45 all get ZQ@10:00 = 0.30
        assert result[_dt(10)][0] == 0.30
        assert result[_dt(10, 15)][0] == 0.30
        assert result[_dt(10, 30)][0] == 0.30
        assert result[_dt(10, 45)][0] == 0.30
        # Bar at 11:00 gets ZQ@11:00 = 0.35
        assert result[_dt(11)][0] == 0.35

    def test_poly_bars_before_any_zq_excluded(self):
        """Poly bars before the first ZQ bar should be excluded."""
        zq = {_dt(10): 0.30}
        poly = {_dt(9): 0.50, _dt(10): 0.55, _dt(11): 0.60}
        result = _align_zq_to_poly(zq, poly)
        assert _dt(9) not in result          # No ZQ data yet
        assert _dt(10) in result
        assert _dt(11) in result

    def test_empty_inputs(self):
        """Empty ZQ or Poly returns empty dict."""
        assert _align_zq_to_poly({}, {_dt(10): 0.5}) == {}
        assert _align_zq_to_poly({_dt(10): 0.3}, {}) == {}
        assert _align_zq_to_poly({}, {}) == {}

    def test_forward_fill_across_cme_gap(self):
        """Overnight CME gap: last ZQ price carries forward to next session open."""
        # ZQ at 17:00 (close), then next bar at 18:00 next day (open)
        zq = {_dt(17, day=1): 0.32, _dt(18, day=2): 0.33}
        # Poly trades at midnight and 8am (no CME bars during that time)
        poly = {
            _dt(17, day=1):  0.48,
            _dt(0,  day=2):  0.49,   # overnight — no ZQ
            _dt(8,  day=2):  0.50,   # early morning — no ZQ yet
            _dt(18, day=2):  0.51,   # ZQ just resumed
        }
        result = _align_zq_to_poly(zq, poly)
        # Poly bars at midnight and 8am get the last ZQ bar (17:00 day 1)
        assert result[_dt(0, day=2)][0] == 0.32
        assert result[_dt(8, day=2)][0] == 0.32
        # Poly bar at 18:00 day 2 gets the new ZQ bar
        assert result[_dt(18, day=2)][0] == 0.33


# ---------------------------------------------------------------------------
# Tests for _simulate_market_intraday
# ---------------------------------------------------------------------------

class TestSimulateMarketIntraday:

    def _make_aligned(
        self, entries: list[Tuple[int, float, float]]
    ) -> Dict[datetime, Tuple[float, float]]:
        """entries = [(hour, zq_prob, poly_mid), ...]"""
        return {_dt(h): (zq, poly) for h, zq, poly in entries}

    def test_no_signal_below_min_edge(self):
        """No entry when edge is always below min_edge."""
        aligned = self._make_aligned([
            (10, 0.40, 0.39),   # edge = +0.01 < 0.03
            (11, 0.40, 0.38),   # edge = +0.02 < 0.03
        ])
        trades = _simulate_market_intraday(_make_fm(), aligned, min_edge=0.03, hold_hours=24)
        assert trades == []

    def test_sell_yes_entry_and_exit_on_convergence(self):
        """SELL_YES: edge -0.20 → converges to -0.05 (< half of -0.20)."""
        aligned = self._make_aligned([
            (10, 0.30, 0.50),   # edge = -0.20 → entry (SELL_YES at 0.50)
            (11, 0.30, 0.46),   # edge = -0.16
            (12, 0.30, 0.39),   # edge = -0.09 → converged (< 0.10)
        ])
        trades = _simulate_market_intraday(_make_fm(), aligned, min_edge=0.05, hold_hours=48)
        assert len(trades) == 1
        t = trades[0]
        assert t.direction == "SELL_YES"
        assert t.entry_ts == _dt(10)
        assert t.exit_ts == _dt(12)
        assert t.hours_held == pytest.approx(2.0)
        assert t.converged is True
        assert t.pnl == pytest.approx(0.50 - 0.39)   # entry_mid - exit_mid

    def test_buy_yes_entry_and_time_exit(self):
        """BUY_YES: edge +0.10, never converges → exits at hold_hours limit."""
        aligned = self._make_aligned([
            (10, 0.50, 0.40),   # edge = +0.10 → entry (BUY_YES at 0.40)
            (11, 0.50, 0.42),   # edge = +0.08
            (12, 0.50, 0.44),   # edge = +0.06  ← hold_hours=2 hit at hour 12
        ])
        trades = _simulate_market_intraday(_make_fm(), aligned, min_edge=0.05, hold_hours=2)
        assert len(trades) == 1
        t = trades[0]
        assert t.direction == "BUY_YES"
        assert t.hours_held == pytest.approx(2.0)
        assert t.converged is False
        assert t.pnl == pytest.approx(0.44 - 0.40)   # exit_mid - entry_mid

    def test_entry_ts_and_exit_ts_populated(self):
        """entry_ts and exit_ts are populated as datetimes (not None)."""
        aligned = self._make_aligned([
            (10, 0.30, 0.50),   # SELL_YES entry
            (12, 0.30, 0.39),   # convergence exit
        ])
        trades = _simulate_market_intraday(_make_fm(), aligned, min_edge=0.05, hold_hours=48)
        assert len(trades) == 1
        assert isinstance(trades[0].entry_ts, datetime)
        assert isinstance(trades[0].exit_ts, datetime)
        assert trades[0].entry_ts.tzinfo is not None
        assert trades[0].hours_held is not None

    def test_reversed_sign_exit(self):
        """Position exits when edge reverses beyond min_edge in opposite direction."""
        aligned = self._make_aligned([
            (10, 0.30, 0.50),   # edge = -0.20 → SELL_YES entry
            (11, 0.50, 0.30),   # edge = +0.20 → reversed sign exit
        ])
        trades = _simulate_market_intraday(_make_fm(), aligned, min_edge=0.05, hold_hours=48)
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_ts == _dt(11)
        assert t.converged is False

    def test_no_entry_after_open_position(self):
        """While in a position, a new signal on a different bar does not open a second position."""
        aligned = self._make_aligned([
            (10, 0.30, 0.50),   # edge = -0.20 → SELL_YES entry
            (11, 0.25, 0.50),   # edge = -0.25 → no double-entry
            (12, 0.30, 0.44),   # edge = -0.14 → still open (not converged)
            (14, 0.30, 0.39),   # edge = -0.09 → converged exit (< 0.10 = half of 0.20)
        ])
        trades = _simulate_market_intraday(_make_fm(), aligned, min_edge=0.05, hold_hours=48)
        assert len(trades) == 1

    def test_pnl_sign_sell_yes_win(self):
        """SELL_YES profit = entry_mid - exit_mid (price fell toward ZQ)."""
        aligned = self._make_aligned([
            (10, 0.30, 0.60),   # SELL_YES at 0.60
            (12, 0.30, 0.20),   # convergence exit at 0.20
        ])
        trades = _simulate_market_intraday(_make_fm(), aligned, min_edge=0.05, hold_hours=48)
        assert trades[0].pnl == pytest.approx(0.60 - 0.20)

    def test_pnl_sign_sell_yes_loss(self):
        """SELL_YES loss = negative pnl when price rose further."""
        zq = {_dt(10, day=1): 0.30, _dt(12, day=3): 0.30}
        poly = {_dt(10, day=1): 0.60, _dt(12, day=3): 0.65}
        aligned = _align_zq_to_poly(zq, poly)
        trades = _simulate_market_intraday(_make_fm(), aligned, min_edge=0.05, hold_hours=50)
        assert trades[0].pnl == pytest.approx(0.60 - 0.65)
        assert trades[0].pnl < 0
