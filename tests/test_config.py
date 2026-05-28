from __future__ import annotations

from pathlib import Path

import pytest

from tesserae_pi_bin_client.config import (
    DEFAULT_TOML,
    Config,
    load_config,
    parse_toml,
    render_config_toml,
)


def test_default_toml_parses() -> None:
    cfg = parse_toml(DEFAULT_TOML)
    assert isinstance(cfg, Config)
    assert cfg.mqtt.host == "192.168.1.10"
    assert cfg.mqtt.port == 1883
    assert cfg.mqtt.device_id == "pi"
    assert cfg.panel.model == "inky_13_3"
    assert cfg.http.download_timeout_s == 30
    assert cfg.http.max_frame_bytes == 16_000_000
    assert cfg.logging.level == "INFO"


def test_load_config_creates_default_on_first_run(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"
    assert not path.exists()
    cfg = load_config(path)
    assert path.exists()
    assert cfg.panel.model == "inky_13_3"


def test_unknown_panel_rejected() -> None:
    bad = DEFAULT_TOML.replace('model = "inky_13_3"', 'model = "inky_nope"')
    with pytest.raises(ValueError, match="unknown"):
        parse_toml(bad)


def test_bad_port_rejected() -> None:
    bad = DEFAULT_TOML.replace("port = 1883", "port = 0")
    with pytest.raises(ValueError, match="out of range"):
        parse_toml(bad)


def test_negative_keepalive_rejected() -> None:
    bad = DEFAULT_TOML.replace("keepalive = 60", "keepalive = -1")
    with pytest.raises(ValueError, match="keepalive"):
        parse_toml(bad)


def test_missing_required_field_rejected() -> None:
    bad = DEFAULT_TOML.replace('client_id = "pi-impression-1"\n', "")
    with pytest.raises(ValueError, match="client_id"):
        parse_toml(bad)


def test_unknown_log_level_rejected() -> None:
    bad = DEFAULT_TOML.replace('level = "INFO"', 'level = "VERBOSE"')
    with pytest.raises(ValueError, match="level"):
        parse_toml(bad)


def test_overrides_take_effect() -> None:
    bad = DEFAULT_TOML.replace(
        'host = "192.168.1.10"', 'host = "broker.local"'
    ).replace("port = 1883", "port = 8883")
    cfg = parse_toml(bad)
    assert cfg.mqtt.host == "broker.local"
    assert cfg.mqtt.port == 8883


# --- render_config_toml -------------------------------------------------------


def test_render_no_args_matches_default() -> None:
    # DEFAULT_TOML is just render_config_toml(); they must agree.
    assert render_config_toml() == DEFAULT_TOML


def test_render_overrides_round_trip() -> None:
    body = render_config_toml(
        mqtt_host="broker.lan",
        mqtt_port=8883,
        mqtt_username="alice",
        mqtt_password="hunter2",
        mqtt_client_id="kitchen-display",
        panel_model="inky_7_3",
    )
    cfg = parse_toml(body)
    assert cfg.mqtt.host == "broker.lan"
    assert cfg.mqtt.port == 8883
    assert cfg.mqtt.username == "alice"
    assert cfg.mqtt.password == "hunter2"
    assert cfg.mqtt.client_id == "kitchen-display"
    assert cfg.panel.model == "inky_7_3"


def test_render_escapes_quote_in_string_value() -> None:
    # A password containing a double quote must still produce valid TOML.
    body = render_config_toml(mqtt_password='abc"def')
    cfg = parse_toml(body)
    assert cfg.mqtt.password == 'abc"def'


def test_render_escapes_backslash_in_string_value() -> None:
    body = render_config_toml(mqtt_password=r"a\b")
    cfg = parse_toml(body)
    assert cfg.mqtt.password == r"a\b"


# --- device_id ----------------------------------------------------------------


def test_render_custom_device_id_round_trips() -> None:
    body = render_config_toml(device_id="pi_kitchen")
    cfg = parse_toml(body)
    assert cfg.mqtt.device_id == "pi_kitchen"


def test_missing_device_id_defaults_to_pi() -> None:
    # Existing installs predate the device_id field — the parser must accept
    # a config that omits it and fall back to "pi" so the back-compat topic
    # prefix is preserved.
    body = "\n".join(
        line for line in DEFAULT_TOML.splitlines() if "device_id" not in line
    ) + "\n"
    cfg = parse_toml(body)
    assert cfg.mqtt.device_id == "pi"


def test_invalid_device_id_rejected() -> None:
    bad = DEFAULT_TOML.replace('device_id = "pi"', 'device_id = "Pi-Kitchen"')
    with pytest.raises(ValueError, match="device_id"):
        parse_toml(bad)


def test_too_short_device_id_rejected() -> None:
    bad = DEFAULT_TOML.replace('device_id = "pi"', 'device_id = "a"')
    with pytest.raises(ValueError, match="device_id"):
        parse_toml(bad)


def test_device_id_starting_with_digit_rejected() -> None:
    bad = DEFAULT_TOML.replace('device_id = "pi"', 'device_id = "1pi"')
    with pytest.raises(ValueError, match="device_id"):
        parse_toml(bad)
