from __future__ import annotations

from typing import Any

import pytest

from tesserae_pi_bin_client.paint import paint
from tesserae_pi_bin_client.panels import buffer_size


class FakePanel:
    """Stand-in for the real inky instance.

    paint.py talks to the panel through `_update(list[int])`. The real
    method pushes the bytes over SPI; ours records the call.
    """

    def __init__(self) -> None:
        self.setup_calls = 0
        self.update_calls: list[list[int]] = []

    def setup(self) -> None:
        self.setup_calls += 1

    def _update(self, buf: list[int]) -> None:
        self.update_calls.append(buf)


def test_valid_buffer_pushed_to_update() -> None:
    panel = FakePanel()
    packed = bytes([0x12] * buffer_size("inky_4"))
    paint(panel, packed, "inky_4")
    assert len(panel.update_calls) == 1
    assert panel.update_calls[0] == list(packed)


def test_wrong_size_raises_before_touching_panel() -> None:
    panel = FakePanel()
    too_small = bytes([0x12] * (buffer_size("inky_4") - 1))
    with pytest.raises(ValueError, match="expected"):
        paint(panel, too_small, "inky_4")
    # Critically, we must not have touched the panel — the firmware would
    # corrupt the display if we pushed a short buffer.
    assert panel.update_calls == []
    assert panel.setup_calls == 0


def test_wrong_size_for_larger_panel_raises() -> None:
    panel = FakePanel()
    wrong = bytes([0x00] * buffer_size("inky_4"))  # too small for 13.3
    with pytest.raises(ValueError):
        paint(panel, wrong, "inky_13_3")
    assert panel.update_calls == []


def test_buffer_size_13_3_matches_spec() -> None:
    # 1600 * 1200 / 2 = 960_000.
    assert buffer_size("inky_13_3") == 960_000


def test_buffer_size_7_3_matches_spec() -> None:
    assert buffer_size("inky_7_3") == 192_000


def test_paint_pushes_exact_byte_values() -> None:
    panel: Any = FakePanel()
    width_height = buffer_size("inky_5_7")
    # Build a buffer whose values are sequential mod 256, then assert the
    # round-trip preserves them — confirms we pass bytes through unmolested.
    payload = bytes(i % 256 for i in range(width_height))
    paint(panel, payload, "inky_5_7")
    pushed = panel.update_calls[0]
    assert pushed == list(payload)
