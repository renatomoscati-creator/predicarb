"""Tests for BenchmarkRegistry — plan 03-01."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.benchmark.registry import BenchmarkRegistry, registry
from src.benchmark.live_benchmark import CachedLiveBenchmark


ANY_TS = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)


class TestRegistryConstant:
    def test_constant_returns_correct_prob(self):
        result = registry.get("constant", benchmark=0.42)
        assert result.get_prob(ANY_TS) == pytest.approx(0.42)

    def test_constant_ignores_unrelated_kwargs(self):
        # Should not raise even with extra keys present
        result = registry.get("constant", benchmark=0.99, zq_meeting_date="2026-06-18")
        assert result.get_prob(ANY_TS) == pytest.approx(0.99)


class TestRegistryZq:
    def test_zq_returns_cached_live_benchmark_instance(self):
        mock_snap = MagicMock()
        mock_snap.prob_at_least_n_cuts.return_value = 0.73

        with patch("src.benchmark.zq_benchmark.fetch_meeting_snapshot", return_value=mock_snap):
            result = registry.get(
                "zq",
                zq_meeting_date="2026-06-18",
                benchmark_ttl=5,
            )

        assert isinstance(result, CachedLiveBenchmark)

    def test_zq_missing_meeting_date_raises_value_error(self):
        with pytest.raises(ValueError, match="zq-meeting-date"):
            registry.get("zq")


class TestRegistryUnknownKey:
    def test_unknown_key_raises_key_error(self):
        with pytest.raises(KeyError) as exc_info:
            registry.get("no-such")
        assert "no-such" in str(exc_info.value)


class TestRegistryCustomRegister:
    def test_register_and_get_custom_factory(self):
        local_registry = BenchmarkRegistry()
        local_registry.register("ping", lambda **kw: "pong")
        assert local_registry.get("ping") == "pong"

    def test_custom_factory_receives_kwargs(self):
        local_registry = BenchmarkRegistry()
        local_registry.register("echo", lambda **kw: kw.get("value"))
        assert local_registry.get("echo", value=42) == 42


class TestRegistryKeys:
    def test_builtin_keys_present(self):
        assert "constant" in registry.keys()
        assert "zq" in registry.keys()

    def test_keys_returns_sorted_list(self):
        keys = registry.keys()
        assert keys == sorted(keys)
