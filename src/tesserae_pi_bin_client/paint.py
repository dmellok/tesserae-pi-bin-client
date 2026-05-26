# PRIVATE-API DEPENDENCY — read before editing.
#
# Tesserae already quantises and packs each frame server-side into the panel's
# native 4-bpp buffer. The downloaded .bin IS the packed pixel buffer; running
# the public `inky.set_image(pil)` would PIL-load and re-quantise — wasted CPU
# on every refresh (~1 s on the 13.3" panel for no visual change).
#
# To skip that, we call `panel._update(list_of_packed_bytes)` directly. This is
# inky's private SPI-push routine; `panel.show()` would otherwise unpack/repack
# nibble pairs from its own (rows, cols) numpy buffer and call _update for us.
#
# Because we bypass show(), the server must bake h_flip / v_flip / rotation
# into the .bin — we no longer apply those transforms in the client.
#
# `inky` is PINNED in pyproject.toml. Bumping it requires re-checking that
# _update() still takes a flat list of packed bytes and dispatches the right
# Spectra-6 init/refresh sequence. Validate on real hardware before shipping.

from __future__ import annotations

import logging
from typing import Any, Protocol

from .panels import PANEL_DIMS, buffer_size

log = logging.getLogger(__name__)


class Panel(Protocol):
    def _update(self, buf: list[int]) -> None: ...

    def setup(self) -> None: ...


def auto_panel() -> Any:
    """Detect the attached Impression via HAT EEPROM and return an inky instance."""
    from inky.auto import auto

    return auto()


def paint(panel: Panel, packed: bytes, model: str) -> None:
    """Push a packed 4-bpp buffer to the panel via the private _update path."""
    expected = buffer_size(model)
    if len(packed) != expected:
        width, height = PANEL_DIMS[model]
        raise ValueError(
            f"frame is {len(packed)} bytes; expected {expected} for {model} "
            f"({width}x{height})"
        )
    panel._update(list(packed))


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
