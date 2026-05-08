"""Benchmark source registry.

Maps string source keys to factory functions. Each factory receives **kwargs
from the CLI namespace and returns a BenchmarkProvider-compatible object.

Extension example::

    from src.benchmark.registry import registry

    def my_factory(**kwargs):
        return MyBenchmark(kwargs["my_param"])

    registry.register("my-source", my_factory)
"""
from __future__ import annotations

from typing import Any, Callable


class BenchmarkRegistry:
    """Maps benchmark source keys to factory functions.

    Each factory receives **kwargs from the CLI namespace and returns
    a BenchmarkProvider-compatible object.
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., Any]] = {}

    def register(self, key: str, factory: Callable[..., Any]) -> None:
        """Register a factory under the given key."""
        self._factories[key] = factory

    def get(self, key: str, **kwargs: Any) -> Any:
        """Instantiate benchmark for the given key, passing kwargs to the factory.

        Raises:
            KeyError: if *key* is not registered.
        """
        if key not in self._factories:
            raise KeyError(
                f"Unknown benchmark source: {key!r}. "
                f"Registered sources: {sorted(self._factories)}"
            )
        return self._factories[key](**kwargs)

    def keys(self) -> list[str]:
        """Return sorted list of registered source keys."""
        return sorted(self._factories)


# ---------------------------------------------------------------------------
# Built-in factory functions
# ---------------------------------------------------------------------------

def _constant_factory(benchmark: float | None = None, **_: Any) -> Any:
    from src.benchmark.csv_benchmark import ConstantBenchmark

    if benchmark is None:
        raise ValueError(
            "--benchmark is required when --benchmark-source is 'constant' (default)"
        )
    return ConstantBenchmark(benchmark)


def _zq_factory(
    zq_meeting_date: str | None = None,
    benchmark_ttl: float = 60.0,
    **_: Any,
) -> Any:
    from datetime import date
    from src.benchmark.live_benchmark import ZqLiveBenchmark

    if not zq_meeting_date:
        raise ValueError(
            "--benchmark-source zq requires --zq-meeting-date (YYYY-MM-DD)"
        )
    return ZqLiveBenchmark(
        meeting_date=date.fromisoformat(zq_meeting_date),
        ttl_seconds=benchmark_ttl,
    )


# ---------------------------------------------------------------------------
# Module-level singleton with built-in sources
# ---------------------------------------------------------------------------

registry = BenchmarkRegistry()
registry.register("constant", _constant_factory)
registry.register("zq", _zq_factory)
