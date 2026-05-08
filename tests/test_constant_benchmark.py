from datetime import datetime, timezone

from src.benchmark.csv_benchmark import ConstantBenchmark


def test_constant_benchmark_returns_fixed_prob():
    cb = ConstantBenchmark(0.35)
    ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert cb.get_prob(ts) == 0.35


def test_constant_benchmark_any_prob():
    for p in (0.0, 0.5, 1.0):
        cb = ConstantBenchmark(p)
        ts = datetime(2025, 6, 1, tzinfo=timezone.utc)
        assert cb.get_prob(ts) == p
