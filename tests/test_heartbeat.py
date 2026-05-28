from __future__ import annotations

import json
from typing import Any

from tesserae_pi_bin_client.heartbeat import (
    OFFLINE_WILL_PAYLOAD,
    STATUS_TOPIC_LEGACY,
    Heartbeat,
    Status,
    status_topic,
)


class RecordingPublisher:
    def __init__(self) -> None:
        self.publishes: list[tuple[str, bytes, int, bool]] = []

    def publish(
        self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
    ) -> Any:
        self.publishes.append((topic, payload, qos, retain))


def test_status_topic_builds_per_device_topic() -> None:
    assert status_topic("pi") == STATUS_TOPIC_LEGACY
    assert status_topic("pi_kitchen") == "tesserae/pi_kitchen/status"


def test_publish_now_emits_retained_status_on_legacy_prefix() -> None:
    status = Status(panel="inky_4")
    pub = RecordingPublisher()
    hb = Heartbeat(
        status=status, publisher=pub, status_topic=STATUS_TOPIC_LEGACY, interval=999
    )
    hb.publish_now()
    assert len(pub.publishes) == 1
    topic, payload, qos, retain = pub.publishes[0]
    assert topic == STATUS_TOPIC_LEGACY
    assert qos == 1 and retain is True
    parsed = json.loads(payload.decode())
    assert parsed["state"] == "idle"
    assert parsed["panel"] == "inky_4"
    assert parsed["fw_version"]


def test_publish_now_emits_retained_status_on_custom_prefix() -> None:
    status = Status(panel="inky_4")
    pub = RecordingPublisher()
    custom = status_topic("pi_kitchen")
    hb = Heartbeat(status=status, publisher=pub, status_topic=custom, interval=999)
    hb.publish_now()
    assert len(pub.publishes) == 1
    topic, _payload, _qos, _retain = pub.publishes[0]
    assert topic == "tesserae/pi_kitchen/status"


def test_publish_offline_emits_will_payload() -> None:
    status = Status(panel="inky_4")
    pub = RecordingPublisher()
    hb = Heartbeat(
        status=status, publisher=pub, status_topic=STATUS_TOPIC_LEGACY, interval=999
    )
    hb.publish_offline()
    assert len(pub.publishes) == 1
    topic, payload, qos, retain = pub.publishes[0]
    assert topic == STATUS_TOPIC_LEGACY
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
