from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Literal, Optional


@dataclass
class Quote:
    price: float   # decimal in [0, 1]
    size: int      # number of shares (contracts)


@dataclass
class OrderBook:
    ticker: str             # YES outcome token_id
    yes_bids: List[Quote]
    yes_asks: List[Quote]
    last_update_ts: Optional[datetime]

    @property
    def best_yes_bid(self) -> Optional[Quote]:
        return self.yes_bids[0] if self.yes_bids else None

    @property
    def best_yes_ask(self) -> Optional[Quote]:
        return self.yes_asks[0] if self.yes_asks else None


@dataclass
class Market:
    ticker: str             # YES outcome token_id (use for orderbook/orders)
    title: str              # market question
    yes_bid: Optional[float]
    yes_ask: Optional[float]
    volume_24h: Optional[int]


@dataclass
class HealthStatus:
    ok: bool
    latency_ms: float
    message: str


@dataclass
class AccountSummary:
    balance_cents: Optional[int]          # USDC collateral balance in cents
    portfolio_value_cents: Optional[int]  # not returned by balance-allowance endpoint
    updated_ts: Optional[datetime]


@dataclass
class OrderResult:
    """Result of a place_order call."""
    success: bool
    poly_order_id: Optional[str]       # assigned by Polymarket on success
    status: str                        # 'placed' | 'rejected' | 'error'
    message: str                       # human-readable status detail
    token_id: str
    side: str                          # 'BUY' | 'SELL'
    price: float
    size: int


def ts_ms_to_datetime_utc(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
