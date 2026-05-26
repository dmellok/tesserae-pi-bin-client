from __future__ import annotations

import json
from typing import Any

from tesserae_pi_bin_client.heartbeat import (
    OFFLINE_WILL_PAYLOAD,
    STATUS_TOPIC,
    Heartbeat,
    Status,
)


class RecordingPublisher:
    def __init__(self) -> None:
        self.publishes: list[tuple[str, bytes, int, bool]] = []

    def publish(
        self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
    ) -> Any:
        self.publishes.append((topic, payload, qos, retain))


def test_publish_now_emits_retained_status() -> None:
    status = Status(panel="inky_4")
    pub = RecordingPublisher()
    hb = Heartbeat(status=status, publisher=pub, interval=999)
    hb.publish_now()
    assert len(pub.publishes) == 1
    topic, payload, qos, retain = pub.publishes[0]
    assert topic == STATUS_TOPIC
    assert qos == 1 and retain is True
    parsed = json.loads(payload.decode())
    assert parsed["state"] == "idle"
    assert parsed["panel"] == "inky_4"
    assert parsed["fw_version"]


def test_publish_offline_emits_will_payload() -> None:
    status = Status(panel="inky_4")
    pub = RecordingPublisher()
    hb = Heartbeat(status=status, publisher=pub, interval=999)
    hb.publish_offline()
    assert len(pub.publishes) == 1
    topic, payload, qos, retain = pub.publishes[0]
    assert topic == STATUS_TOPIC
    assert payload == OFFLINE_WILL_PAYLOAD
    assert retain is True


def test_status_payload_includes_required_fields() -> None:
    status = Status(panel="inky_13_3")
    status.last_digest = "abcd"
    status.last_error = None
    payload = status.payload()
    for key in (
        "state",
        "last_paint_at",
        "last_error",
        "last_digest",
        "uptime_s",
        "fw_version",
        "panel",
    ):
        assert key in payload
    assert payload["panel"] == "inky_13_3"
    assert payload["last_digest"] == "abcd"
