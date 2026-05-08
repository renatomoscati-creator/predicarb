from typing import Dict, List

import pytest

from src.config import Settings
from src.polymarket.client import PolymarketClient


class DummyResponse:
    def __init__(self, status_code: int, json_data):
        self.status_code = status_code
        self._json_data = json_data
        self.ok = status_code == 200

    def json(self):
        return self._json_data

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class DummySession:
    """
    Simulates requests.Session.get() returning Polymarket CLOB/Gamma payloads.
    """

    def get(self, url: str, params=None, timeout: float = 0, **kwargs) -> DummyResponse:
        self.last_url = url
        self.last_params = params or {}

        if "gamma-api" in url:
            # Gamma markets endpoint — bare array
            return DummyResponse(
                200,
                [
                    {
                        "conditionId": "0xCONDITION1",
                        "question": "Will X happen?",
                        "clobTokenIds": ["0xYES_TOKEN_1", "0xNO_TOKEN_1"],
                        "bestBid": "0.45",
                        "bestAsk": "0.55",
                        "volume24hr": "123",
                    }
                ],
            )

        if "/book" in url:
            # CLOB orderbook endpoint — prices/sizes as strings
            return DummyResponse(
                200,
                {
                    "bids": [{"price": "0.40", "size": "10"}],
                    "asks": [{"price": "0.60", "size": "5"}],
                    "timestamp": "1700000000",
                },
            )

        return DummyResponse(404, {})


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        env="demo",
        api_base_url="https://clob.example.test",
        gamma_api_base_url="https://gamma-api.example.test",
        log_level="INFO",
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
    )


def test_get_markets_uses_gamma_api(monkeypatch, settings: Settings) -> None:
    client = PolymarketClient(settings)
    dummy = DummySession()
    monkeypatch.setattr(client, "_session", dummy)

    markets, latency_ms = client.get_markets(keywords=[], limit=10)

    assert "gamma-api" in dummy.last_url
    assert latency_ms >= 0
    assert len(markets) == 1
    m = markets[0]
    assert m.ticker == "0xYES_TOKEN_1"   # YES token_id used as ticker
    assert m.title == "Will X happen?"
    assert m.yes_bid == pytest.approx(0.45)
    assert m.yes_ask == pytest.approx(0.55)
    assert m.volume_24h == 123


def test_get_markets_active_closed_params(monkeypatch, settings: Settings) -> None:
    client = PolymarketClient(settings)
    dummy = DummySession()
    monkeypatch.setattr(client, "_session", dummy)

    client.get_markets(keywords=[], limit=5)

    assert dummy.last_params.get("active") == "true"
    assert dummy.last_params.get("closed") == "false"
    assert dummy.last_params.get("limit") == 5


def test_get_orderbook_parses_quotes(monkeypatch, settings: Settings) -> None:
    client = PolymarketClient(settings)
    dummy = DummySession()
    monkeypatch.setattr(client, "_session", dummy)

    ob = client.get_orderbook("0xYES_TOKEN_1")

    assert ob.ticker == "0xYES_TOKEN_1"
    assert ob.best_yes_bid is not None
    assert ob.best_yes_bid.price == pytest.approx(0.40)
    assert ob.best_yes_bid.size == 10
    assert ob.best_yes_ask is not None
    assert ob.best_yes_ask.price == pytest.approx(0.60)
    assert ob.best_yes_ask.size == 5
    assert ob.last_update_ts is not None


def test_get_orderbook_uses_token_id_param(monkeypatch, settings: Settings) -> None:
    client = PolymarketClient(settings)
    dummy = DummySession()
    monkeypatch.setattr(client, "_session", dummy)

    client.get_orderbook("0xABC123")

    assert dummy.last_params.get("token_id") == "0xABC123"
