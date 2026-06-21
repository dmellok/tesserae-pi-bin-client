"""MQTT transport — retained-frame subscribe + retained heartbeat publish.

This is the original wake loop, lifted verbatim out of main._do_run() into
its own module so main.py can dispatch on config.transport_mode. Behaviour
is unchanged from the pre-REST-port codebase: same threads, same callbacks,
same shutdown semantics.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from ..config import Config
from ..heartbeat import Heartbeat, Status, status_topic
from ..mqtt_loop import (
    FrameDispatcher,
    MessageHandler,
    PaintFn,
    frame_topic,
    make_mqtt_loop,
)

log = logging.getLogger(__name__)


def run(
    config: Config,
    status: Status,
    paint_fn: PaintFn,
    shutdown: threading.Event,
    config_path: Path,  # noqa: ARG001 — accepted for parity with rest.run()
) -> int:
    """Run the MQTT wake loop until `shutdown` is set."""
    resolved_frame_topic = frame_topic(config.mqtt.device_id)
    resolved_status_topic = status_topic(config.mqtt.device_id)

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
