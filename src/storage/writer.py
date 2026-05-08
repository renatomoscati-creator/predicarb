"""
Background tick writer.

Drains a thread-safe queue and persists Tick rows to SQLite without
blocking the async WS loop.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Optional

from src.storage.db import Database
from src.storage.models import Tick

logger = logging.getLogger(__name__)


class TickWriter:
    """
    Starts a daemon thread that reads Tick objects from an internal queue
    and writes them to the database.

    Usage::

        writer = TickWriter(db)
        writer.start()
        writer.put(tick)
        writer.stop()   # blocks until queue is flushed (up to 5s)
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._queue: queue.Queue[Optional[Tick]] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="TickWriter", daemon=True)
        self._stop_event = threading.Event()

    def start(self) -> None:
        self._thread.start()
        logger.debug("TickWriter started.")

    def stop(self) -> None:
        """Signal the writer to stop and wait for it to drain the queue."""
        self._stop_event.set()
        self._queue.put(None)  # unblock _run if it's waiting
        self._thread.join(timeout=5.0)
        logger.debug("TickWriter stopped.")

    def put(self, tick: Tick) -> None:
        self._queue.put(tick)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if item is None:
                break

            try:
                self._db.insert_tick(item)
            except Exception as exc:
                logger.error("TickWriter failed to write tick: %s", exc)
            finally:
                self._queue.task_done()

        # Drain any remaining items after stop signal
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if item is None:
                break
            try:
                self._db.insert_tick(item)
            except Exception as exc:
                logger.error("TickWriter drain error: %s", exc)
