"""
Unit tests for PositionManager.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.storage.db import Database
from src.strategy.position_manager import PositionManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.sqlite")
    d.init()
    return d


def _pm(db: Database, max_long=None, max_short=None, max_loss=None) -> PositionManager:
    return PositionManager(
        db=db,
        ticker="0xTOKEN",
        max_long=max_long,
        max_short=max_short,
        max_loss=max_loss,
    )


# ------------------------------------------------------------------
# Initialisation
# ------------------------------------------------------------------


def test_initial_position_is_flat(db):
    pm = _pm(db)
    assert pm.yes_count == 0
    assert pm.avg_cost == 0.0
    assert pm.realized_pnl == 0.0


def test_position_persisted_on_init(db):
    _pm(db)
    pos = db.get_position("0xTOKEN")
    assert pos is not None
    assert pos.yes_count == 0


def test_reload_existing_position(db):
    pm = _pm(db)
    pm.record_fill("BUY_YES", 0.50, 100)

    # Re-create from DB
    pm2 = _pm(db)
    assert pm2.yes_count == 100
    assert pm2.avg_cost == pytest.approx(0.50)


# ------------------------------------------------------------------
# can_trade — no limits
# ------------------------------------------------------------------


def test_can_trade_no_limits(db):
    pm = _pm(db)
    ok, reason = pm.can_trade("BUY_YES", 1000)
    assert ok is True
    assert reason == "ok"


# ------------------------------------------------------------------
# can_trade — max_long
# ------------------------------------------------------------------


def test_can_trade_max_long_allows_exact_fill(db):
    pm = _pm(db, max_long=100)
    ok, _ = pm.can_trade("BUY_YES", 100)
    assert ok is True


def test_can_trade_max_long_blocks_excess(db):
    pm = _pm(db, max_long=100)
    ok, reason = pm.can_trade("BUY_YES", 101)
    assert ok is False
    assert "max_long_breach" in reason


def test_can_trade_max_long_blocks_after_partial_fill(db):
    pm = _pm(db, max_long=100)
    pm.record_fill("BUY_YES", 0.50, 80)
    ok, reason = pm.can_trade("BUY_YES", 30)  # 80+30=110 > 100
    assert ok is False
    assert "max_long_breach" in reason


def test_can_trade_sell_not_affected_by_max_long(db):
    pm = _pm(db, max_long=100)
    pm.record_fill("BUY_YES", 0.50, 100)
    ok, _ = pm.can_trade("SELL_YES", 100)
    assert ok is True


# ------------------------------------------------------------------
# can_trade — max_short (default = no shorting)
# ------------------------------------------------------------------


def test_can_trade_no_shorting_default(db):
    pm = _pm(db, max_short=0)
    ok, reason = pm.can_trade("SELL_YES", 1)
    assert ok is False
    assert "max_short_breach" in reason


def test_can_trade_sell_allowed_when_long(db):
    pm = _pm(db, max_short=0)
    pm.record_fill("BUY_YES", 0.50, 100)
    ok, _ = pm.can_trade("SELL_YES", 100)  # closes long, doesn't go short
    assert ok is True


def test_can_trade_sell_blocked_when_would_go_short(db):
    pm = _pm(db, max_short=0)
    pm.record_fill("BUY_YES", 0.50, 50)
    ok, reason = pm.can_trade("SELL_YES", 51)  # 50-51=-1 < 0
    assert ok is False
    assert "max_short_breach" in reason


def test_can_trade_max_short_allows_limited_short(db):
    pm = _pm(db, max_short=50)
    ok, _ = pm.can_trade("SELL_YES", 50)
    assert ok is True


def test_can_trade_max_short_blocks_excess_short(db):
    pm = _pm(db, max_short=50)
    ok, reason = pm.can_trade("SELL_YES", 51)
    assert ok is False
    assert "max_short_breach" in reason


# ------------------------------------------------------------------
# can_trade — max_loss
# ------------------------------------------------------------------


def test_can_trade_max_loss_halts_on_breach(db):
    pm = _pm(db, max_loss=10.0)
    # Simulate a loss by manually updating realized_pnl via fill accounting
    pm.record_fill("BUY_YES", 0.80, 100)   # buy at 0.80
    pm.record_fill("SELL_YES", 0.70, 100)  # sell at 0.70 → realised pnl = -10
    ok, reason = pm.can_trade("BUY_YES", 1)
    assert ok is False
    assert "max_loss_breach" in reason


def test_can_trade_max_loss_allows_when_not_breached(db):
    pm = _pm(db, max_loss=10.0)
    pm.record_fill("BUY_YES", 0.50, 100)
    pm.record_fill("SELL_YES", 0.60, 100)  # realised pnl = +10 → no breach
    ok, _ = pm.can_trade("BUY_YES", 1)
    assert ok is True


# ------------------------------------------------------------------
# record_fill — long accounting
# ------------------------------------------------------------------


def test_buy_from_flat(db):
    pm = _pm(db)
    pm.record_fill("BUY_YES", 0.50, 100)
    assert pm.yes_count == 100
    assert pm.avg_cost == pytest.approx(0.50)
    assert pm.realized_pnl == pytest.approx(0.0)


def test_buy_adds_to_long_weighted_avg(db):
    pm = _pm(db)
    pm.record_fill("BUY_YES", 0.40, 100)
    pm.record_fill("BUY_YES", 0.60, 100)
    assert pm.yes_count == 200
    assert pm.avg_cost == pytest.approx(0.50)


def test_sell_closes_long_realises_pnl(db):
    pm = _pm(db, max_short=0)
    pm.record_fill("BUY_YES", 0.50, 100)
    pm.record_fill("SELL_YES", 0.60, 100)
    assert pm.yes_count == 0
    assert pm.avg_cost == pytest.approx(0.0)
    assert pm.realized_pnl == pytest.approx(10.0)  # (0.60-0.50)*100


def test_partial_sell_closes_partial_long(db):
    pm = _pm(db, max_short=0)
    pm.record_fill("BUY_YES", 0.50, 100)
    pm.record_fill("SELL_YES", 0.60, 40)
    assert pm.yes_count == 60
    assert pm.realized_pnl == pytest.approx(4.0)  # (0.60-0.50)*40


def test_sell_more_than_long_opens_short(db):
    pm = _pm(db, max_short=50)
    pm.record_fill("BUY_YES", 0.50, 50)
    pm.record_fill("SELL_YES", 0.70, 80)  # close 50 + short 30
    assert pm.yes_count == -30
    assert pm.avg_cost == pytest.approx(0.70)   # short entry at 0.70
    assert pm.realized_pnl == pytest.approx(10.0)  # (0.70-0.50)*50


# ------------------------------------------------------------------
# record_fill — short accounting
# ------------------------------------------------------------------


def test_sell_from_flat_opens_short(db):
    pm = _pm(db, max_short=100)
    pm.record_fill("SELL_YES", 0.60, 50)
    assert pm.yes_count == -50
    assert pm.avg_cost == pytest.approx(0.60)
    assert pm.realized_pnl == pytest.approx(0.0)


def test_buy_closes_short_realises_pnl(db):
    pm = _pm(db, max_short=100)
    pm.record_fill("SELL_YES", 0.60, 50)   # short at 0.60
    pm.record_fill("BUY_YES", 0.50, 50)   # cover at 0.50 → profit 0.10*50=5
    assert pm.yes_count == 0
    assert pm.avg_cost == pytest.approx(0.0)
    assert pm.realized_pnl == pytest.approx(5.0)


def test_short_add_updates_weighted_avg(db):
    pm = _pm(db, max_short=200)
    pm.record_fill("SELL_YES", 0.60, 100)
    pm.record_fill("SELL_YES", 0.70, 100)
    assert pm.yes_count == -200
    assert pm.avg_cost == pytest.approx(0.65)


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------


def test_position_saved_to_db_after_fill(db):
    pm = _pm(db)
    pm.record_fill("BUY_YES", 0.55, 200)

    pos = db.get_position("0xTOKEN")
    assert pos is not None
    assert pos.yes_count == 200
    assert pos.avg_cost == pytest.approx(0.55)


def test_refresh_reloads_from_db(db):
    pm1 = _pm(db)
    pm1.record_fill("BUY_YES", 0.55, 100)

    pm2 = _pm(db)
    assert pm2.yes_count == 100
    pm2.record_fill("BUY_YES", 0.65, 100)

    pm1.refresh()
    assert pm1.yes_count == 200
