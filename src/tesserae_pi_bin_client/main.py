from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import __version__
from .config import DEFAULT_CONFIG_PATH, Config, load_config
from .heartbeat import Heartbeat, Status, _primary_ip, status_topic
from .mqtt_loop import FrameDispatcher, MessageHandler, frame_topic, make_mqtt_loop
from .paint import auto_panel, detected_model_or, paint
from .panels import PANEL_DIMS, buffer_size
from .unpack import pack

log = logging.getLogger(__name__)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _stripe_test_pattern(model: str) -> bytes:
    """Six vertical stripes in the panel's six display colours.

    Used by --paint-test to verify the SPI path and panel orientation
    without needing an MQTT broker or a Tesserae server.
    """
    width, height = PANEL_DIMS[model]
    stripe_nibbles = [0x0, 0x1, 0x2, 0x3, 0x5, 0x6]  # skip reserved 0x4
    pixels: list[int] = []
    stripe_w = width // len(stripe_nibbles)
    for _y in range(height):
        for x in range(width):
            band = min(x // stripe_w, len(stripe_nibbles) - 1)
            pixels.append(stripe_nibbles[band])
    return pack(pixels, width, height)


def _do_paint_test(config: Config) -> int:
    model = config.panel.model
    expected = buffer_size(model)
    log.info("building stripe pattern for %s (%d bytes)", model, expected)
    buf = _stripe_test_pattern(model)
    panel = auto_panel()
    detected = detected_model_or(panel, model)
    if detected != model:
        log.warning(
            "configured model=%s but detected=%s; painting configured size",
            model,
            detected,
        )
    log.info("pushing %d bytes to panel via _update()", len(buf))
    paint(panel, buf, model)
    log.info("paint-test complete")
    return 0


def _do_run(config: Config) -> int:
    status = Status(panel=config.panel.model)
    panel = auto_panel()
    status.panel = detected_model_or(panel, config.panel.model)
    # PANEL_DIMS[name] is (W, H) in the orientation Tesserae renders into and
    # the orientation inky.show() consumes after applying the per-driver
    # rotation — i.e. what the user actually sees on the panel. Surface those
    # in the heartbeat so the server's one-click Register pre-fills correctly.
    status.panel_w, status.panel_h = PANEL_DIMS[status.panel]
    # Resolved once at startup — neither value changes between heartbeats.
    status.ip = _primary_ip()

    resolved_frame_topic = frame_topic(config.mqtt.device_id)
    resolved_status_topic = status_topic(config.mqtt.device_id)

    def paint_fn(packed: bytes, model: str) -> None:
        paint(panel, packed, model)

    client_holder: dict[str, Any] = {}

    class _ClientPublisher:
        def publish(
            self,
            topic: str,
            payload: bytes,
            qos: int = 0,
            retain: bool = False,
        ) -> Any:
            client = client_holder.get("client")
            if client is None:
                return None
            return client.publish(topic, payload, qos=qos, retain=retain)

    heartbeat = Heartbeat(
        status=status,
        publisher=_ClientPublisher(),
        status_topic=resolved_status_topic,
    )
    dispatcher = FrameDispatcher(
        config=config, paint_fn=paint_fn, status=status, heartbeat=heartbeat
    )
    handler = MessageHandler(
        dispatcher=dispatcher,
        status=status,
        heartbeat=heartbeat,
        frame_topic=resolved_frame_topic,
    )
    client = make_mqtt_loop(
        config=config,
        handler=handler,
        frame_topic=resolved_frame_topic,
        status_topic=resolved_status_topic,
    )
    client_holder["client"] = client

    shutdown = threading.Event()

    def _signal_handler(signum: int, frame: Any) -> None:
        log.info("signal %d received; shutting down", signum)
        shutdown.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    dispatcher.start()
    heartbeat.start()
    log.info(
        "connecting to mqtt %s:%d as %s",
        config.mqtt.host,
        config.mqtt.port,
        config.mqtt.client_id,
    )
    client.connect_async(config.mqtt.host, config.mqtt.port, config.mqtt.keepalive)
    client.loop_start()

    try:
        while not shutdown.is_set():
            time.sleep(0.5)
    finally:
        log.info("publishing offline and disconnecting")
        try:
            heartbeat.publish_offline()
        except Exception:  # noqa: BLE001
            log.exception("failed publishing offline status")
        heartbeat.stop()
        dispatcher.stop()
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:  # noqa: BLE001
            log.exception("error during MQTT shutdown")
    return 0


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tesserae-pi-bin-client",
        description="Subscribe to a Tesserae server and paint 4-bpp .bin frames "
        "onto an Inky Impression.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"config path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--paint-test",
        action="store_true",
        help="paint a six-colour stripe pattern and exit (no MQTT)",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    _setup_logging(config.logging.level)

    if args.paint_test:
        return _do_paint_test(config)
    return _do_run(config)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
