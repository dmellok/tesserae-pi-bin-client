from __future__ import annotations

RGB = tuple[int, int, int]

BLACK: RGB = (0, 0, 0)
WHITE: RGB = (255, 255, 255)
YELLOW: RGB = (255, 243, 56)
RED: RGB = (191, 0, 0)
BLUE: RGB = (0, 0, 255)
GREEN: RGB = (0, 154, 23)

# Waveshare E6 firmware palette. 0x4 is reserved by the firmware and is
# never produced by Tesserae; we render it as black so this helper never
# raises on a stray byte during diagnostics.
PALETTE: dict[int, RGB] = {
    0x0: BLACK,
    0x1: WHITE,
    0x2: YELLOW,
    0x3: RED,
    0x4: BLACK,
    0x5: BLUE,
    0x6: GREEN,
}


def expected_byte_count(width: int, height: int) -> int:
    if width % 2 != 0:
        raise ValueError(f"panel width must be even, got {width}")
    return width * height // 2


def unpack(data: bytes, width: int, height: int) -> list[RGB]:
    """Decode a packed 4-bpp buffer into a flat row-major list of RGB pixels.

    High nibble is the even column, low nibble the odd column. Used by the
    --paint-test CLI and the test suite; the production paint path does not
    unpack — it pushes the packed bytes straight to the panel.
    """
    expected = expected_byte_count(width, height)
    if len(data) != expected:
        raise ValueError(
            f"packed buffer length {len(data)} does not match {width}x{height} "
            f"(expected {expected} bytes)"
        )
    pixels: list[RGB] = []
    for byte in data:
        pixels.append(PALETTE[(byte >> 4) & 0x0F])
        pixels.append(PALETTE[byte & 0x0F])
    return pixels


def pack(pixels: list[int], width: int, height: int) -> bytes:
    """Pack a flat row-major list of nibble values (0..6) into the 4-bpp buffer.

    Inverse of unpack(); used by the --paint-test CLI to build a synthetic
    test pattern in code.
    """
    expected_pixels = width * height
    if len(pixels) != expected_pixels:
        raise ValueError(
            f"pixel count {len(pixels)} does not match {width}x{height} "
            f"(expected {expected_pixels})"
        )
    if width % 2 != 0:
        raise ValueError(f"panel width must be even, got {width}")
    out = bytearray(expected_pixels // 2)
    for i in range(0, expected_pixels, 2):
        hi = pixels[i] & 0x0F
        lo = pixels[i + 1] & 0x0F
        out[i // 2] = (hi << 4) | lo
    return bytes(out)
