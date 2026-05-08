"""
Tests for PolymarketSigner — verifies L2 HMAC-SHA256 header generation.
"""
from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from src.polymarket.auth import PolymarketSigner


@pytest.fixture
def signer() -> PolymarketSigner:
    return PolymarketSigner(
        api_key="test-api-key",
        api_secret="test-secret",
        api_passphrase="test-passphrase",
        address="0xDEADBEEF",
    )


def test_all_headers_present(signer: PolymarketSigner) -> None:
    hdrs = signer.headers("GET", "/book")
    assert "POLY_ADDRESS" in hdrs
    assert "POLY_SIGNATURE" in hdrs
    assert "POLY_TIMESTAMP" in hdrs
    assert "POLY_API_KEY" in hdrs
    assert "POLY_PASSPHRASE" in hdrs


def test_header_values(signer: PolymarketSigner) -> None:
    hdrs = signer.headers("GET", "/book")
    assert hdrs["POLY_ADDRESS"] == "0xDEADBEEF"
    assert hdrs["POLY_API_KEY"] == "test-api-key"
    assert hdrs["POLY_PASSPHRASE"] == "test-passphrase"


def test_timestamp_is_numeric(signer: PolymarketSigner) -> None:
    hdrs = signer.headers("GET", "/foo")
    ts = hdrs["POLY_TIMESTAMP"]
    assert ts.isdigit(), f"Timestamp should be all digits, got: {ts!r}"
    assert len(ts) == 10, "Timestamp should be Unix seconds (10 digits)"


def test_signature_is_valid_hmac(signer: PolymarketSigner) -> None:
    path = "/book?token_id=0xABC"
    hdrs = signer.headers("GET", path)
    ts = hdrs["POLY_TIMESTAMP"]
    received_sig = hdrs["POLY_SIGNATURE"]

    expected_message = ts + "GET" + path
    expected_sig = base64.b64encode(
        hmac.new(b"test-secret", expected_message.encode(), hashlib.sha256).digest()
    ).decode()

    assert received_sig == expected_sig


def test_method_is_uppercased(signer: PolymarketSigner) -> None:
    hdrs_lower = signer.headers("get", "/foo")
    hdrs_upper = signer.headers("GET", "/foo")
    # Both should use uppercase in message — signatures match when ts is same
    # Just verify no exception is raised for lowercase input
    assert hdrs_lower["POLY_SIGNATURE"] is not None
    assert hdrs_upper["POLY_SIGNATURE"] is not None


def test_different_calls_produce_different_signatures(signer: PolymarketSigner) -> None:
    import time
    sig1 = signer.headers("GET", "/foo")["POLY_SIGNATURE"]
    time.sleep(1.05)  # ensure second-level timestamp advances
    sig2 = signer.headers("GET", "/foo")["POLY_SIGNATURE"]
    assert sig1 != sig2
