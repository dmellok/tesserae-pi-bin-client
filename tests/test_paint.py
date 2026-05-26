from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from tesserae_pi_bin_client.paint import paint, unpack_to_buf
from tesserae_pi_bin_client.panels import PANEL_DIMS, buffer_size
from tesserae_pi_bin_client.unpack import pack


class FakePanel:
    """Stand-in for the real inky instance.

    paint.py assigns a numpy array to `panel.buf` and then calls `panel.show()`
    (which on a real panel would rotate/flip/repack and SPI-push). Ours just
    records the call shape.
    """

    def __init__(self) -> None:
        self.buf: Any = None
        self.show_calls = 0

    def show(self) -> None:
        self.show_calls += 1


def test_valid_buffer_assigns_buf_and_calls_show() -> None:
    panel = FakePanel()
    width, height = PANEL_DIMS["inky_4"]
    packed = bytes([0x12] * buffer_size("inky_4"))
    paint(panel, packed, "inky_4")
    assert panel.show_calls == 1
    assert isinstance(panel.buf, np.ndarray)
    assert panel.buf.shape == (height, width)
    assert panel.buf.dtype == np.uint8


def test_wrong_size_raises_before_touching_panel() -> None:
    panel = FakePanel()
    too_small = bytes([0x12] * (buffer_size("inky_4") - 1))
    with pytest.raises(ValueError, match="expected"):
        paint(panel, too_small, "inky_4")
    # Critically, we must not have touched the panel — the driver would
    # corrupt the display if we pushed a short buffer.
    assert panel.buf is None
    assert panel.show_calls == 0


def test_wrong_size_for_larger_panel_raises() -> None:
    panel = FakePanel()
    wrong = bytes([0x00] * buffer_size("inky_4"))  # too small for 13.3
    with pytest.raises(ValueError):
        paint(panel, wrong, "inky_13_3")
    assert panel.buf is None


def test_buffer_size_13_3_matches_spec() -> None:
    # 1600 * 1200 / 2 = 960_000.
    assert buffer_size("inky_13_3") == 960_000


def test_buffer_size_7_3_matches_spec() -> None:
    assert buffer_size("inky_7_3") == 192_000


def test_unpack_to_buf_high_nibble_is_even_column() -> None:
    # One byte = two horizontally-adjacent pixels.
    # 0x21 -> col 0 = 0x2 (yellow index), col 1 = 0x1 (white index).
    buf = unpack_to_buf(bytes([0x21]), width=2, height=1)
    assert buf.shape == (1, 2)
    assert int(buf[0, 0]) == 0x2
    assert int(buf[0, 1]) == 0x1


def test_unpack_to_buf_preserves_known_pattern() -> None:
    # Build a known packed buffer via the pack() helper, unpack it, and
    # verify the per-pixel palette indices land in the right grid cells.
    nibbles = [0x0, 0x1, 0x2, 0x3, 0x5, 0x6, 0x6, 0x0]
    packed = pack(nibbles, width=8, height=1)
    buf = unpack_to_buf(packed, width=8, height=1)
    assert buf.shape == (1, 8)
    assert buf[0].tolist() == nibbles


def test_unpack_to_buf_rows_are_independent() -> None:
    # 4x2 image: row 0 = [1, 2, 3, 4], row 1 = [5, 6, 0, 1]
    # (packed: byte 0 = 0x12, byte 1 = 0x34, byte 2 = 0x56, byte 3 = 0x01)
    packed = bytes([0x12, 0x34, 0x56, 0x01])
    buf = unpack_to_buf(packed, width=4, height=2)
    assert buf.shape == (2, 4)
    assert buf[0].tolist() == [0x1, 0x2, 0x3, 0x4]
    assert buf[1].tolist() == [0x5, 0x6, 0x0, 0x1]
