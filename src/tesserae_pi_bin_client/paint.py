# PARTIAL-BYPASS NOTE — read before editing.
#
# Tesserae quantises and packs each frame into a 4-bpp buffer server-side.
# To skip the expensive PIL+palette re-quantise that `inky.set_image()` would
# otherwise do per refresh, we unpack our packed bytes into the (rows, cols)
# uint8 array of palette indices that `inky.show()` expects, assign it to
# `panel.buf` directly, and call `show()`.
#
# Why not bypass show() entirely? The Inky Impression 13.3" driver
# (inky_el133uf1.py in inky 2.4.0) has two physical e-ink controllers and a
# panel-specific orientation transform — its show() rotates the buffer 90°,
# splits at column 600, and calls `_update(buf_a, buf_b)`. The smaller-panel
# drivers (inky_e673.py et al.) have a single `_update(buf)`. So there is no
# uniform "push packed bytes" entry point. Letting show() handle its own
# per-driver packing keeps us portable across all four Impression sizes.
#
# `inky` is PINNED in pyproject.toml. If you bump it, re-run --paint-test on
# real hardware — Pimoroni occasionally renames internals across versions.
# (We saw this with inky 2.4.0: _update changed shape on the 13.3" driver.)
#
# What the server is responsible for: producing the .bin in the panel's
# "natural" image orientation — the same orientation a PIL image would be in
# if you were going to call set_image(pil). Inky.show() then applies the
# driver-specific h_flip/v_flip/rotation defaults.

from __future__ import annotations

import logging
from typing import Any, Protocol

import numpy as np

from .panels import PANEL_DIMS, buffer_size

log = logging.getLogger(__name__)


class Panel(Protocol):
    buf: Any

    def show(self) -> None: ...


def auto_panel() -> Any:
    """Detect the attached Impression via HAT EEPROM and return an inky instance."""
    from inky.auto import auto

    return auto()


def unpack_to_buf(packed: bytes, width: int, height: int) -> np.ndarray:
    """Turn our 4-bpp packed buffer into the (rows, cols) uint8 array inky wants.

    High nibble = even column (x=0, 2, 4, ...), low nibble = odd column.
    Output shape is (height, width), one byte per pixel holding the palette
    index (0..6).
    """
    flat = np.frombuffer(packed, dtype=np.uint8)
    if flat.size != width * height // 2:
        raise ValueError(
            f"packed length {flat.size} does not match {width}x{height}"
        )
    buf = np.empty((height, width), dtype=np.uint8)
    buf[:, 0::2] = (flat >> 4).reshape(height, width // 2)
    buf[:, 1::2] = (flat & 0x0F).reshape(height, width // 2)
    return buf


def paint(panel: Panel, packed: bytes, model: str) -> None:
    """Push a packed 4-bpp buffer to the panel via panel.buf + panel.show()."""
    expected = buffer_size(model)
    if len(packed) != expected:
        width, height = PANEL_DIMS[model]
        raise ValueError(
            f"frame is {len(packed)} bytes; expected {expected} for {model} "
            f"({width}x{height})"
        )
    width, height = PANEL_DIMS[model]
    panel.buf = unpack_to_buf(packed, width, height)
    panel.show()


def detected_model_or(panel: Any, fallback: str) -> str:
    """Best-effort: pull (cols, rows) off the panel and match a known dim.

    The HAT EEPROM doesn't expose the marketing name, so we infer from
    resolution. Falls back to the configured model if no match.
    """
    cols = getattr(panel, "cols", None)
    rows = getattr(panel, "rows", None)
    if cols is None or rows is None:
        return fallback
    for name, (w, h) in PANEL_DIMS.items():
        if (cols, rows) == (w, h) or (cols, rows) == (h, w):
            return name
    return fallback
