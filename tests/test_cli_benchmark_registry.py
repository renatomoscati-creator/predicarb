"""Tests for _build_benchmark() registry dispatch in src/cli.py."""
from __future__ import annotations

import argparse
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.cli import _build_benchmark


def _ns(**kwargs) -> argparse.Namespace:
    """Helper: build a Namespace with sensible defaults plus overrides."""
    defaults = {
        "benchmark_source": "constant",
        "benchmark": None,
        "zq_meeting_date": None,
        "benchmark_ttl": 60.0,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# Test 1: constant source with valid benchmark returns correct probability
# ---------------------------------------------------------------------------

def test_constant_source_returns_correct_prob():
    args = _ns(benchmark_source="constant", benchmark=0.3)
    provider = _build_benchmark(args)
    assert provider.get_prob(datetime.utcnow()) == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Test 2: constant source with benchmark=None raises ValueError
# ---------------------------------------------------------------------------

def test_constant_source_none_benchmark_raises():
    args = _ns(benchmark_source="constant", benchmark=None)
    with pytest.raises(ValueError):
        _build_benchmark(args)


# ---------------------------------------------------------------------------
# Test 3: zq source returns CachedLiveBenchmark (with mocked network)
# ---------------------------------------------------------------------------

def test_zq_source_returns_cached_live_benchmark():
    from src.benchmark.live_benchmark import CachedLiveBenchmark

    mock_snapshot = MagicMock(return_value={"prob": 0.75})
    with patch("src.benchmark.zq_benchmark.fetch_meeting_snapshot", mock_snapshot):
        args = _ns(
            benchmark_source="zq",
            zq_meeting_date="2026-06-18",
            benchmark_ttl=5.0,
            benchmark=None,
        )
        provider = _build_benchmark(args)
    assert isinstance(provider, CachedLiveBenchmark)


# ---------------------------------------------------------------------------
# Test 4: unknown source raises KeyError
# ---------------------------------------------------------------------------

def test_unknown_source_raises_key_error():
    args = _ns(benchmark_source="no-such-source", benchmark=None)
    with pytest.raises(KeyError):
        _build_benchmark(args)
