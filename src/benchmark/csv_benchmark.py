from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Protocol


class BenchmarkProvider(Protocol):
    def get_prob(self, ts_utc: datetime) -> float:
        ...


@dataclass
class BenchmarkPoint:
    ts_utc: datetime
    prob: float


class CsvBenchmark(BenchmarkProvider):
    """
    Simple benchmark provider backed by a CSV time series.

    CSV format: ts_utc,prob
      - ts_utc: ISO-8601 UTC timestamp, e.g. 2025-03-01T14:30:00Z
      - prob:   float in [0, 1]
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.points: List[BenchmarkPoint] = []
        self._load()

    @staticmethod
    def _parse_ts(value: str) -> datetime:
        value = value.strip()
        # Support both Z and explicit offset; normalize to UTC
        if value.endswith("Z"):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(value)
        return dt.astimezone(timezone.utc)

    def _load(self) -> None:
        with self.path.open("r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts_raw = row.get("ts_utc")
                prob_raw = row.get("prob")
                if not ts_raw or prob_raw is None:
                    continue
                ts = self._parse_ts(ts_raw)
                prob = float(prob_raw)
                if 0.0 <= prob <= 1.0:
                    self.points.append(BenchmarkPoint(ts_utc=ts, prob=prob))
        self.points.sort(key=lambda p: p.ts_utc)

    def get_prob(self, ts_utc: datetime) -> float:
        """
        Return the last probability at or before `ts_utc`.
        If no point is available yet, fall back to the earliest value.
        """
        if not self.points:
            raise ValueError(f"No benchmark data loaded from {self.path}")

        ts_utc = ts_utc.astimezone(timezone.utc)

        last_prob = self.points[0].prob
        for pt in self.points:
            if pt.ts_utc <= ts_utc:
                last_prob = pt.prob
            else:
                break
        return last_prob


class ConstantBenchmark:
    """Benchmark that always returns the same probability regardless of timestamp."""

    def __init__(self, prob: float) -> None:
        self._prob = prob

    def get_prob(self, ts_utc: datetime) -> float:  # noqa: ARG002
        return self._prob

