import json
import logging
import time
from typing import Dict, Iterable, List, Literal, Optional, Tuple
from urllib.parse import urlencode

import requests

from src.config import Settings
from src.polymarket.auth import PolymarketSigner
from src.polymarket.models import (
    AccountSummary,
    HealthStatus,
    Market,
    OrderBook,
    OrderResult,
    Quote,
    ts_ms_to_datetime_utc,
)

logger = logging.getLogger(__name__)


class PolymarketClient:
    """
    Thin wrapper around Polymarket APIs.

    - CLOB API (clob.polymarket.com): orderbook, balance, order placement.
    - Gamma API (gamma-api.polymarket.com): market listings.

    Public endpoints work without credentials. Authenticated endpoints
    (balance, order placement) require POLYMARKET_API_KEY, POLYMARKET_API_SECRET,
    POLYMARKET_API_PASSPHRASE, and POLYMARKET_ADDRESS to be set in config/env.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session = requests.Session()
        self._clob_url = settings.api_base_url.rstrip("/")
        self._gamma_url = settings.gamma_api_base_url.rstrip("/")

        self._signer: Optional[PolymarketSigner] = None
        if (
            settings.polymarket_api_key
            and settings.polymarket_api_secret
            and settings.polymarket_api_passphrase
            and settings.polymarket_address
        ):
            try:
                self._signer = PolymarketSigner(
                    api_key=settings.polymarket_api_key,
                    api_secret=settings.polymarket_api_secret,
                    api_passphrase=settings.polymarket_api_passphrase,
                    address=settings.polymarket_address,
                )
                logger.info("Polymarket signer initialised (authenticated mode).")
            except Exception as exc:
                logger.warning("Failed to initialise Polymarket signer: %s — running unauthenticated.", exc)

    # --- Internal helpers -------------------------------------------------

    def _clob(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self._clob_url}{path}"

    def _gamma(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self._gamma_url}{path}"

    def _auth_headers(self, method: str, path: str, params: Optional[Dict[str, object]] = None) -> Dict[str, str]:
        if self._signer is None:
            return {}
        signed_path = path
        if params:
            signed_path = path + "?" + urlencode({k: v for k, v in params.items() if v is not None})
        return self._signer.headers(method, signed_path)

    def _get(
        self,
        url: str,
        path: str,
        params: Optional[Dict[str, object]] = None,
        auth: bool = False,
        timeout: float = 5.0,
    ) -> Tuple[float, requests.Response]:
        headers = self._auth_headers("GET", path, params) if auth else {}
        start = time.perf_counter()
        resp = self._session.get(url, params=params, headers=headers, timeout=timeout)
        latency_ms = (time.perf_counter() - start) * 1000.0
        return latency_ms, resp

    def _post(
        self,
        path: str,
        json: Optional[Dict[str, object]] = None,
        timeout: float = 5.0,
    ) -> Tuple[float, requests.Response]:
        headers = self._auth_headers("POST", path)
        start = time.perf_counter()
        resp = self._session.post(self._clob(path), json=json, headers=headers, timeout=timeout)
        latency_ms = (time.perf_counter() - start) * 1000.0
        return latency_ms, resp

    # --- Public API -------------------------------------------------------

    def get_health(self) -> HealthStatus:
        """Health check via a lightweight CLOB API call."""
        try:
            latency_ms, resp = self._get(
                self._clob("/markets"), "/markets", params={"limit": 1}
            )
            if resp.ok:
                return HealthStatus(ok=True, latency_ms=latency_ms, message="OK")
            return HealthStatus(ok=False, latency_ms=latency_ms, message=f"HTTP {resp.status_code}")
        except Exception as exc:
            logger.exception("Health check failed: %s", exc)
            return HealthStatus(ok=False, latency_ms=0.0, message=str(exc))

    def get_account_summary(self) -> AccountSummary:
        """
        Fetch USDC collateral balance via GET /balance-allowance?asset_type=COLLATERAL.
        Requires L2 authentication.
        Response: {"balance": "string", "allowance": "string"}
        Balance is in USDC (decimal string, e.g. "1000.50"); converted to cents internally.
        """
        path = "/balance-allowance"
        params: Dict[str, object] = {"asset_type": "COLLATERAL"}
        try:
            latency_ms, resp = self._get(
                self._clob(path), path, params=params, auth=True, timeout=5.0
            )
            if resp.status_code == 401:
                logger.info("Balance endpoint returned 401 (unauthenticated).")
                return AccountSummary(balance_cents=None, portfolio_value_cents=None, updated_ts=None)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Failed to fetch account summary: %s", exc)
            return AccountSummary(balance_cents=None, portfolio_value_cents=None, updated_ts=None)

        data = resp.json()
        # "balance" is a USDC decimal string — convert to cents for consistency
        raw_balance = data.get("balance")
        balance_cents = int(float(raw_balance) * 100) if raw_balance is not None else None

        return AccountSummary(
            balance_cents=balance_cents,
            portfolio_value_cents=None,  # Polymarket balance-allowance does not return portfolio value
            updated_ts=None,
        )

    def get_markets(
        self,
        keywords: Iterable[str],
        limit: int = 50,
        event_ticker: Optional[str] = None,   # maps to Gamma `tag` filter (best-effort)
        series_ticker: Optional[str] = None,  # not directly supported; ignored
    ) -> Tuple[List[Market], float]:
        """
        Fetch active markets from the Gamma API.

        Gamma API: GET https://gamma-api.polymarket.com/markets
        Returns a bare JSON array (no wrapper key).
        Key fields: question, conditionId, clobTokenIds ([Yes token ID, No token ID]).
        """
        params: Dict[str, object] = {
            "limit": limit,
            "active": "true",
            "closed": "false",
        }
        if event_ticker:
            params["tag"] = event_ticker

        latency_ms, resp = self._get(
            self._gamma("/markets"), "/markets", params=params, timeout=10.0
        )
        resp.raise_for_status()

        markets_raw = resp.json()  # bare array from Gamma API
        if isinstance(markets_raw, dict):
            # CLOB API wraps in {"data": [...]}
            markets_raw = markets_raw.get("data") or []

        markets: List[Market] = []
        for m in markets_raw:
            # condition_id identifies the market; use Yes token_id for orderbook queries
            condition_id = m.get("conditionId") or m.get("condition_id") or ""
            question = m.get("question") or ""

            # clobTokenIds: [Yes token ID, No token ID] — may be a JSON string
            clob_token_ids_raw = m.get("clobTokenIds") or []
            if isinstance(clob_token_ids_raw, str):
                try:
                    clob_token_ids_raw = json.loads(clob_token_ids_raw)
                except (json.JSONDecodeError, ValueError):
                    clob_token_ids_raw = []
            clob_token_ids = clob_token_ids_raw if isinstance(clob_token_ids_raw, list) else []
            yes_token_id = clob_token_ids[0] if clob_token_ids else None

            # best bid/ask may not be present in Gamma listings; set to None if missing
            yes_bid = m.get("bestBid") or m.get("best_bid")
            yes_ask = m.get("bestAsk") or m.get("best_ask")
            volume_24h = m.get("volume24hr") or m.get("volume_24h")

            markets.append(
                Market(
                    ticker=yes_token_id or condition_id,  # prefer Yes token_id for orderbook compat
                    title=question,
                    yes_bid=float(yes_bid) if yes_bid is not None else None,
                    yes_ask=float(yes_ask) if yes_ask is not None else None,
                    volume_24h=int(float(volume_24h)) if volume_24h is not None else None,
                )
            )

        return markets, latency_ms

    def get_orderbook(self, ticker: str) -> OrderBook:
        """
        Fetch orderbook for a YES outcome token.

        CLOB API: GET /book?token_id=<YES_TOKEN_ID>
        Response: {
          "bids": [{"price": "0.48", "size": "1000"}, ...],
          "asks": [{"price": "0.52", "size": "500"},  ...],
          "timestamp": "1234567890",   # Unix seconds string
          ...
        }
        Prices and sizes are decimal strings.
        """
        path = "/book"
        params: Dict[str, object] = {"token_id": ticker}

        _, resp = self._get(self._clob(path), path, params=params, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()

        bids_raw: List[Dict] = data.get("bids") or []
        asks_raw: List[Dict] = data.get("asks") or []

        yes_bids: List[Quote] = sorted(
            [Quote(price=float(row["price"]), size=int(float(row["size"]))) for row in bids_raw],
            key=lambda q: -q.price,
        )
        yes_asks: List[Quote] = sorted(
            [Quote(price=float(row["price"]), size=int(float(row["size"]))) for row in asks_raw],
            key=lambda q: q.price,
        )

        # timestamp is Unix milliseconds as a string
        ts_raw = data.get("timestamp")
        last_update_ts = (
            ts_ms_to_datetime_utc(int(float(ts_raw)))
            if ts_raw is not None
            else None
        )

        return OrderBook(
            ticker=ticker,
            yes_bids=yes_bids,
            yes_asks=yes_asks,
            last_update_ts=last_update_ts,
        )

    # --- Authenticated trading API ----------------------------------------

    def place_order(
        self,
        token_id: str,
        side: Literal["BUY", "SELL"],
        price: float,
        size: int,
        time_in_force: Literal["GTC", "FOK", "GTD"] = "GTC",
    ) -> OrderResult:
        """
        Place a limit order on the Polymarket CLOB.

        POST /order
        Body: {
            "order": {
                "tokenId":     token_id,
                "side":        "BUY" | "SELL",
                "price":       "<decimal string, e.g. '0.55'>",
                "size":        "<integer string, e.g. '100'>",
                "timeInForce": "GTC" | "FOK" | "GTD",
                "type":        "LIMIT",
            },
            "owner":     "<wallet address>",
            "orderType": "GTC" | "FOK" | "GTD",
        }
        Response on success: {"success": true, "orderID": "<id>", "status": "placed", ...}
        """
        if self._signer is None:
            return OrderResult(
                success=False,
                poly_order_id=None,
                status="error",
                message="Authentication not configured — set POLYMARKET_API_KEY, SECRET, PASSPHRASE, ADDRESS.",
                token_id=token_id,
                side=side,
                price=price,
                size=size,
            )

        body = {
            "order": {
                "tokenId": token_id,
                "side": side,
                "price": str(price),
                "size": str(size),
                "timeInForce": time_in_force,
                "type": "LIMIT",
            },
            "owner": self._signer.address,
            "orderType": time_in_force,
        }

        try:
            latency_ms, resp = self._post("/order", json=body, timeout=10.0)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("place_order failed: %s", exc)
            return OrderResult(
                success=False,
                poly_order_id=None,
                status="error",
                message=str(exc),
                token_id=token_id,
                side=side,
                price=price,
                size=size,
            )

        data = resp.json()
        order_id = data.get("orderID") or data.get("order_id") or data.get("id")
        success = bool(data.get("success", True))
        status = data.get("status", "placed") if success else "rejected"

        logger.info(
            "Order %s: token_id=%s side=%s price=%s size=%s → id=%s",
            status, token_id, side, price, size, order_id,
        )

        return OrderResult(
            success=success,
            poly_order_id=order_id,
            status=status,
            message=data.get("errorMsg") or data.get("message") or "",
            token_id=token_id,
            side=side,
            price=price,
            size=size,
        )

    def cancel_order(self, poly_order_id: str) -> bool:
        """
        Cancel an open order by its Polymarket order ID.

        DELETE /order/{order_id}
        Returns True if cancelled successfully.
        """
        path = f"/order/{poly_order_id}"
        headers = self._signer.headers("DELETE", path) if self._signer else {}
        try:
            start = time.perf_counter()
            resp = self._session.delete(self._clob(path), headers=headers, timeout=5.0)
            resp.raise_for_status()
            logger.info("Cancelled order %s", poly_order_id)
            return True
        except Exception as exc:
            logger.error("cancel_order %s failed: %s", poly_order_id, exc)
            return False

    def get_order_status(self, poly_order_id: str) -> Optional[Dict]:
        """
        Fetch current status of an order.

        GET /order/{order_id}
        Returns raw response dict or None on error.
        """
        path = f"/order/{poly_order_id}"
        try:
            _, resp = self._get(self._clob(path), path, auth=True, timeout=5.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("get_order_status %s failed: %s", poly_order_id, exc)
            return None
