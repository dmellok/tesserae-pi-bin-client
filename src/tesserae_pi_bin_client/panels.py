from __future__ import annotations

PANEL_DIMS: dict[str, tuple[int, int]] = {
    "inky_4": (640, 400),
    "inky_5_7": (600, 448),
    "inky_7_3": (800, 480),
    "inky_13_3": (1600, 1200),
}


def buffer_size(model: str) -> int:
    width, height = PANEL_DIMS[model]
    return width * height // 2


def is_valid_model(model: str) -> bool:
    return model in PANEL_DIMS
