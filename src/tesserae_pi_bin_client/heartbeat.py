from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from . import __version__

# Default-prefix topic kept around for tests that want to assert against
# the back-compat shape. Production callers MUST derive their topic from
# config via status_topic(device_id) — main.py wires this through.
STATUS_TOPIC_LEGACY = "tesserae/pi/status"
OFFLINE_WILL_PAYLOAD = json.dumps({"state": "offline"}).encode("utf-8")
HEARTBEAT_INTERVAL_S = 60.0


def status_topic(device_id: str) -> str:
    """Build the per-device retained-status topic."""
    return f"tesserae/{device_id}/status"


class Publisher(Protocol):
    def publish(
        self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
    ) -> Any: ...


@dataclass
class Status:
    state: str = "idle"
    last_paint_at: float | None = None
    last_error: str | None = None
    last_digest: str | None = None
    panel: str = "unknown"
    started_at: float = field(default_factory=time.time)

    def payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "last_paint_at": self.last_paint_at,
            "last_error": self.last_error,
            "last_digest": self.last_digest,
            "uptime_s": time.time() - self.started_at,
            "fw_version": __version__,
            "panel": self.panel,
        }

    def to_json(self) -> bytes:
        return json.dumps(self.payload()).encode("utf-8")


def _ignore_status(s: Status) -> None:  # pragma: no cover - default no-op
    del s


class Heartbeat:
    """Background thread that re-publishes Status retained every interval.

    External callers (mqtt_loop / dispatcher) mutate the Status object and
    call kick() to flush immediately after a state change. The thread loop
    flushes on its own at least every HEARTBEAT_INTERVAL_S so the broker
    sees a fresh retained message even during long idle periods.
    """

    def __init__(
        self,
        status: Status,
        publisher: Publisher,
        status_topic: str,
        interval: float = HEARTBEAT_INTERVAL_S,
    ) -> None:
        self._status = status
        self._publisher = publisher
        self._interval = interval
        self._topic = status_topic
        self._stop_event = threading.Event()
        self._kick_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="tesserae-heartbeat", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._kick_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
            self._thread = None

    def publish_now(self) -> None:
        with self._lock:
            self._publisher.publish(
                self._topic, self._status.to_json(), qos=1, retain=True
            )

    def publish_offline(self) -> None:
        with self._lock:
            self._publisher.publish(
                self._topic, OFFLINE_WILL_PAYLOAD, qos=1, retain=True
            )

    def kick(self) -> None:
        self._kick_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.publish_now()
            self._kick_event.wait(timeout=self._interval)
            self._kick_event.clear()


def status_summary(status: Status) -> str:
    """Compact rendering for logs."""
    payload = status.payload()
    return (
        f"state={payload['state']} "
        f"digest={payload['last_digest']} "
        f"err={payload['last_error']} "
        f"uptime={payload['uptime_s']:.0f}s"
    )


# Re-export dataclass helpers for tests
__all__ = [
    "STATUS_TOPIC_LEGACY",
    "OFFLINE_WILL_PAYLOAD",
    "HEARTBEAT_INTERVAL_S",
    "Status",
    "Heartbeat",
    "Publisher",
    "status_summary",
    "status_topic",
    "asdict",
]
