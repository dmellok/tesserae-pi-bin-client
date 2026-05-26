from __future__ import annotations

import json
from typing import Any

import pytest

from tesserae_pi_bin_client.config import (
    Config,
    HttpConfig,
    LoggingConfig,
    MqttConfig,
    PanelConfig,
)
from tesserae_pi_bin_client.heartbeat import Heartbeat, Status
from tesserae_pi_bin_client.mqtt_loop import (
    FRAME_TOPIC,
    FrameDispatcher,
    FrameRequest,
    MessageHandler,
    make_mqtt_loop,
    parse_frame_payload,
)
from tesserae_pi_bin_client.panels import buffer_size


def _config(model: str = "inky_4") -> Config:
    return Config(
        mqtt=MqttConfig(
            host="h", port=1883, username="", password="", client_id="cid", keepalive=60
        ),
        panel=PanelConfig(model=model),
        http=HttpConfig(download_timeout_s=5, max_frame_bytes=10_000_000),
        logging=LoggingConfig(level="INFO"),
    )


class FakePublisher:
    def __init__(self) -> None:
        self.publishes: list[tuple[str, bytes, int, bool]] = []

    def publish(
        self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
    ) -> Any:
        self.publishes.append((topic, payload, qos, retain))
        return None


class CapturingDispatcher:
    """Drop-in stand-in for FrameDispatcher used by MessageHandler tests."""

    def __init__(self) -> None:
        self.submitted: list[FrameRequest] = []

    def submit(self, request: FrameRequest) -> None:
        self.submitted.append(request)


# --- parse_frame_payload --------------------------------------------------------


def test_parse_extracts_url_and_digest() -> None:
    digest = "3f7a91b2c4e5d6f8" * 4  # 64 hex chars
    raw = json.dumps(
        {"url": f"http://server:8000/renders/{digest}.bin"}
    ).encode("utf-8")
    req = parse_frame_payload(raw)
    assert req.url.endswith(f"/renders/{digest}.bin")
    assert req.digest == digest


def test_parse_short_digest_still_extracted() -> None:
    raw = json.dumps({"url": "http://h/renders/3f7a91b2.bin"}).encode()
    req = parse_frame_payload(raw)
    assert req.digest == "3f7a91b2"


def test_parse_rejects_missing_url() -> None:
    raw = json.dumps({"x": 1}).encode()
    with pytest.raises(ValueError, match="url"):
        parse_frame_payload(raw)


def test_parse_rejects_non_object() -> None:
    raw = json.dumps([1, 2, 3]).encode()
    with pytest.raises(ValueError, match="object"):
        parse_frame_payload(raw)


def test_parse_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="JSON"):
        parse_frame_payload(b"\x00not-json\xff")


def test_parse_rejects_non_http_scheme() -> None:
    raw = json.dumps({"url": "ftp://h/renders/abc.bin"}).encode()
    with pytest.raises(ValueError, match="scheme"):
        parse_frame_payload(raw)


# --- MessageHandler -------------------------------------------------------------


def _handler_pair() -> tuple[MessageHandler, CapturingDispatcher, Status, FakePublisher]:
    status = Status(panel="inky_4")
    publisher = FakePublisher()
    heartbeat = Heartbeat(status=status, publisher=publisher)
    dispatcher = CapturingDispatcher()
    handler = MessageHandler(
        dispatcher=dispatcher,  # type: ignore[arg-type]
        status=status,
        heartbeat=heartbeat,
    )
    return handler, dispatcher, status, publisher


def test_handler_dispatches_valid_payload() -> None:
    handler, dispatcher, status, _ = _handler_pair()
    raw = json.dumps({"url": "http://h/renders/abc123.bin"}).encode()
    handler.handle(FRAME_TOPIC, raw)
    assert len(dispatcher.submitted) == 1
    assert status.state == "idle"
    assert status.last_error is None


def test_handler_records_error_on_bad_payload() -> None:
    handler, dispatcher, status, _ = _handler_pair()
    handler.handle(FRAME_TOPIC, b"not json")
    assert dispatcher.submitted == []
    assert status.state == "error"
    assert status.last_error is not None
    assert "bad payload" in status.last_error


def test_handler_ignores_unexpected_topic() -> None:
    handler, dispatcher, status, _ = _handler_pair()
    raw = json.dumps({"url": "http://h/renders/abc.bin"}).encode()
    handler.handle("some/other/topic", raw)
    assert dispatcher.submitted == []
    assert status.state == "idle"


# --- FrameDispatcher.process (synchronous variant for tests) --------------------


def test_dispatcher_paints_valid_buffer() -> None:
    cfg = _config("inky_4")
    expected = buffer_size("inky_4")
    fake_buf = bytes(expected)

    def fake_download(url: str, timeout_s: float, max_bytes: int) -> bytes:
        return fake_buf

    paint_calls: list[tuple[bytes, str]] = []

    def fake_paint(packed: bytes, model: str) -> None:
        paint_calls.append((packed, model))

    status = Status(panel="inky_4")
    publisher = FakePublisher()
    heartbeat = Heartbeat(status=status, publisher=publisher)
    dispatcher = FrameDispatcher(
        config=cfg,
        paint_fn=fake_paint,
        status=status,
        heartbeat=heartbeat,
        download_fn=fake_download,
    )
    dispatcher.process(FrameRequest(url="http://h/renders/abcd.bin", digest="abcd"))
    assert len(paint_calls) == 1
    assert paint_calls[0] == (fake_buf, "inky_4")
    assert status.state == "idle"
    assert status.last_digest == "abcd"
    assert status.last_error is None
    assert status.last_paint_at is not None


def test_dispatcher_rejects_wrong_sized_download() -> None:
    cfg = _config("inky_4")

    def short_download(url: str, timeout_s: float, max_bytes: int) -> bytes:
        return b"\x00" * 10  # nowhere near 128_000

    paint_calls: list[tuple[bytes, str]] = []

    def fake_paint(packed: bytes, model: str) -> None:
        paint_calls.append((packed, model))

    status = Status(panel="inky_4")
    heartbeat = Heartbeat(status=status, publisher=FakePublisher())
    dispatcher = FrameDispatcher(
        config=cfg,
        paint_fn=fake_paint,
        status=status,
        heartbeat=heartbeat,
        download_fn=short_download,
    )
    dispatcher.process(FrameRequest(url="http://h/renders/xx.bin", digest="xx"))
    # Critically: paint must NOT have been called for a short buffer.
    assert paint_calls == []
    assert status.state == "error"
    assert status.last_error is not None


def test_dispatcher_skips_duplicate_digest() -> None:
    cfg = _config("inky_4")
    expected = buffer_size("inky_4")

    download_calls = [0]

    def counting_download(url: str, timeout_s: float, max_bytes: int) -> bytes:
        download_calls[0] += 1
        return bytes(expected)

    def fake_paint(packed: bytes, model: str) -> None:
        pass

    status = Status(panel="inky_4")
    heartbeat = Heartbeat(status=status, publisher=FakePublisher())
    dispatcher = FrameDispatcher(
        config=cfg,
        paint_fn=fake_paint,
        status=status,
        heartbeat=heartbeat,
        download_fn=counting_download,
    )
    req = FrameRequest(url="http://h/renders/aaaa.bin", digest="aaaa")
    dispatcher.process(req)
    assert download_calls[0] == 1
    # Second submit with same digest should be skipped before download.
    dispatcher.submit(req)
    assert download_calls[0] == 1


def test_dispatcher_records_download_failure() -> None:
    cfg = _config("inky_4")

    def boom_download(url: str, timeout_s: float, max_bytes: int) -> bytes:
        raise TimeoutError("server unreachable")

    status = Status(panel="inky_4")
    heartbeat = Heartbeat(status=status, publisher=FakePublisher())
    dispatcher = FrameDispatcher(
        config=cfg,
        paint_fn=lambda p, m: None,
        status=status,
        heartbeat=heartbeat,
        download_fn=boom_download,
    )
    dispatcher.process(FrameRequest(url="http://h/renders/xx.bin", digest="xx"))
    assert status.state == "error"
    assert status.last_error is not None
    assert "TimeoutError" in status.last_error


# --- make_mqtt_loop wiring ------------------------------------------------------


class FakeMqttClient:
    def __init__(self, client_id: str) -> None:
        self.client_id = client_id
        self.will: tuple[str, bytes, int, bool] | None = None
        self.username: tuple[str, str | None] | None = None
        self.backoff: tuple[float, float] | None = None
        self.subscribed: list[tuple[str, int]] = []
        self.on_connect: Any = None
        self.on_disconnect: Any = None
        self.on_message: Any = None

    def will_set(
        self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
    ) -> Any:
        self.will = (topic, payload, qos, retain)

    def username_pw_set(self, username: str, password: str | None = None) -> Any:
        self.username = (username, password)

    def reconnect_delay_set(self, min_delay: float, max_delay: float) -> Any:
        self.backoff = (min_delay, max_delay)

    def subscribe(self, topic: str, qos: int = 0) -> Any:
        self.subscribed.append((topic, qos))


def test_make_mqtt_loop_sets_lwt_and_backoff() -> None:
    cfg = _config("inky_4")
    status = Status(panel="inky_4")
    heartbeat = Heartbeat(status=status, publisher=FakePublisher())
    handler = MessageHandler(
        dispatcher=CapturingDispatcher(),  # type: ignore[arg-type]
        status=status,
        heartbeat=heartbeat,
    )
    fake_client = FakeMqttClient("cid")
    client = make_mqtt_loop(cfg, handler, client_factory=lambda cid: fake_client)
    assert client is fake_client
    assert fake_client.will is not None
    topic, payload, qos, retain = fake_client.will
    assert topic == "tesserae/pi/status"
    assert qos == 1 and retain is True
    assert json.loads(payload.decode())["state"] == "offline"
    assert fake_client.backoff == (1.0, 60.0)
    # on_connect should subscribe to the frame topic at QoS 1.
    fake_client.on_connect(fake_client, None, None, 0, None)
    assert (FRAME_TOPIC, 1) in fake_client.subscribed
