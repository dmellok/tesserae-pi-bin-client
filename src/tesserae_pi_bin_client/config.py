from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .panels import is_valid_model

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "tesserae-pi-bin-client" / "config.toml"

DEFAULT_TOML = """\
[mqtt]
host = "192.168.1.10"
port = 1883
username = ""
password = ""
client_id = "pi-impression-1"
keepalive = 60

[panel]
model = "inky_13_3"  # inky_4 | inky_5_7 | inky_7_3 | inky_13_3

[http]
download_timeout_s = 30
max_frame_bytes = 16000000

[logging]
level = "INFO"
"""


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int
    username: str
    password: str
    client_id: str
    keepalive: int


@dataclass(frozen=True)
class PanelConfig:
    model: str


@dataclass(frozen=True)
class HttpConfig:
    download_timeout_s: int
    max_frame_bytes: int


@dataclass(frozen=True)
class LoggingConfig:
    level: str


@dataclass(frozen=True)
class Config:
    mqtt: MqttConfig
    panel: PanelConfig
    http: HttpConfig
    logging: LoggingConfig


def _require(section: dict[str, Any], key: str, kind: type, where: str) -> Any:
    if key not in section:
        raise ValueError(f"missing [{where}].{key}")
    value = section[key]
    if not isinstance(value, kind):
        raise ValueError(
            f"[{where}].{key} must be {kind.__name__}, got {type(value).__name__}"
        )
    return value


def _parse(raw: dict[str, Any]) -> Config:
    mqtt_section = raw.get("mqtt", {})
    if not isinstance(mqtt_section, dict):
        raise ValueError("[mqtt] must be a table")
    mqtt = MqttConfig(
        host=_require(mqtt_section, "host", str, "mqtt"),
        port=_require(mqtt_section, "port", int, "mqtt"),
        username=mqtt_section.get("username", ""),
        password=mqtt_section.get("password", ""),
        client_id=_require(mqtt_section, "client_id", str, "mqtt"),
        keepalive=_require(mqtt_section, "keepalive", int, "mqtt"),
    )
    if not 1 <= mqtt.port <= 65535:
        raise ValueError(f"[mqtt].port out of range: {mqtt.port}")
    if mqtt.keepalive <= 0:
        raise ValueError(f"[mqtt].keepalive must be positive, got {mqtt.keepalive}")

    panel_section = raw.get("panel", {})
    if not isinstance(panel_section, dict):
        raise ValueError("[panel] must be a table")
    panel = PanelConfig(model=_require(panel_section, "model", str, "panel"))
    if not is_valid_model(panel.model):
        raise ValueError(f"[panel].model unknown: {panel.model!r}")

    http_section = raw.get("http", {})
    if not isinstance(http_section, dict):
        raise ValueError("[http] must be a table")
    http = HttpConfig(
        download_timeout_s=_require(http_section, "download_timeout_s", int, "http"),
        max_frame_bytes=_require(http_section, "max_frame_bytes", int, "http"),
    )
    if http.download_timeout_s <= 0:
        raise ValueError("[http].download_timeout_s must be positive")
    if http.max_frame_bytes <= 0:
        raise ValueError("[http].max_frame_bytes must be positive")

    log_section = raw.get("logging", {})
    if not isinstance(log_section, dict):
        raise ValueError("[logging] must be a table")
    logging_cfg = LoggingConfig(level=log_section.get("level", "INFO"))
    if logging_cfg.level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"[logging].level unknown: {logging_cfg.level!r}")

    return Config(mqtt=mqtt, panel=panel, http=http, logging=logging_cfg)


def parse_toml(text: str) -> Config:
    return _parse(tomllib.loads(text))


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_TOML, encoding="utf-8")
    return parse_toml(path.read_text(encoding="utf-8"))
