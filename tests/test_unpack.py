from __future__ import annotations

import pytest

from tesserae_pi_bin_client.unpack import (
    BLACK,
    BLUE,
    GREEN,
    PALETTE,
    RED,
    WHITE,
    YELLOW,
    expected_byte_count,
    pack,
    unpack,
)


def test_palette_includes_reserved_nibble_as_black() -> None:
    # Reserved 0x4 is rendered as black so the helper never raises on a stray byte.
    assert PALETTE[0x4] == BLACK


def test_unpack_documented_example() -> None:
    # Reference vector from the spec, corrected: bytes [0x01, 0x23, 0x56, 0x60]
    # (high-nibble = even column) unpack to
    # black, white, yellow, red, blue, green, green, black.
    # The spec wrote the first byte as 0x10 which would invert the first pair;
    # see test_unpack_high_nibble_is_even_column below for the canonical check.
    data = bytes([0x01, 0x23, 0x56, 0x60])
    pixels = unpack(data, width=8, height=1)
    assert pixels == [BLACK, WHITE, YELLOW, RED, BLUE, GREEN, GREEN, BLACK]


def test_unpack_high_nibble_is_even_column() -> None:
    # 0x21 -> high=yellow (col 0), low=white (col 1)
    pixels = unpack(bytes([0x21]), width=2, height=1)
    assert pixels == [YELLOW, WHITE]


def test_unpack_round_trip_through_pack() -> None:
    nibbles = [0x0, 0x1, 0x2, 0x3, 0x5, 0x6, 0x6, 0x0]
    packed = pack(nibbles, width=8, height=1)
    pixels = unpack(packed, width=8, height=1)
    expected = [PALETTE[n] for n in nibbles]
    assert pixels == expected


def test_unpack_rejects_wrong_length() -> None:
    # 13.3" expects 960_000 bytes; anything else must raise.
    with pytest.raises(ValueError, match="does not match"):
        unpack(b"\x00" * 5, width=1600, height=1200)


def test_unpack_rejects_off_by_one_length() -> None:
    with pytest.raises(ValueError):
        unpack(b"\x00" * 3, width=4, height=1)


def test_pack_rejects_wrong_pixel_count() -> None:
    with pytest.raises(ValueError):
        pack([0, 1, 2], width=4, height=1)


def test_pack_rejects_odd_width() -> None:
    with pytest.raises(ValueError, match="even"):
        pack([0, 1, 2], width=3, height=1)


def test_expected_byte_count_13_3() -> None:
    # 1600 * 1200 / 2 = 960_000.
    assert expected_byte_count(1600, 1200) == 960_000


def test_expected_byte_count_7_3() -> None:
    assert expected_byte_count(800, 480) == 192_000


def test_pack_uses_high_nibble_first() -> None:
    packed = pack([0x2, 0x1], width=2, height=1)
    assert packed == bytes([0x21])
