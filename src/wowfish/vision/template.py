"""Interact-icon templates, with their alpha channel as the match mask: the gold gear sprites,
and the round anvil badge some clients show over the bobber instead."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from wowfish.assets import ICON_STYLES, icon_path


@dataclass(frozen=True)
class IconTemplate:
    native_size: int  # sprite the pixels came from: 29 | 42 | 53 | 79 | 103
    size: int  # side in pixels after resizing
    bgr: np.ndarray  # (size, size, 3) uint8
    mask: np.ndarray  # (size, size) uint8, 255 where the sprite is fully opaque
    style: str = "gear"  # "gear" | "badge"


def load_icon_templates(style: str = "gear") -> tuple[IconTemplate, ...]:
    """The templates of one icon style, or of every style for "auto"."""
    styles = list(ICON_STYLES) if style == "auto" else [style]
    out: list[IconTemplate] = []
    for st, n in [(name, size) for name in styles for size in ICON_STYLES[name]]:
        rgba = cv2.imread(str(icon_path(n)), cv2.IMREAD_UNCHANGED)
        if rgba is None or rgba.ndim != 3 or rgba.shape[2] != 4:
            raise FileNotFoundError(f"missing or non-RGBA icon asset: {icon_path(n)}")
        bgr = np.ascontiguousarray(rgba[:, :, :3])
        # Fully opaque pixels only: the sprite carries a soft dark drop shadow (alpha 1-254,
        # ~40% of its box) that the game blends into the scene. Over bright water that shadow is
        # barely visible, and matching it capped a real gear at ~0.57 so a UI ring won.
        mask = (rgba[:, :, 3] == 255).astype(np.uint8) * 255
        out.append(IconTemplate(n, n, bgr, mask, st))
    return tuple(out)


def resize_template(t: IconTemplate, size: int) -> IconTemplate:
    if size == t.size:
        return t
    interp = cv2.INTER_AREA if size < t.size else cv2.INTER_CUBIC
    bgr = cv2.resize(t.bgr, (size, size), interpolation=interp)
    mask = cv2.resize(t.mask, (size, size), interpolation=cv2.INTER_NEAREST)
    return IconTemplate(t.native_size, size, bgr, mask, t.style)


def styles_in(templates: Sequence[IconTemplate]) -> list[str]:
    return list(dict.fromkeys(t.style for t in templates))


def template_for_size(
    templates: Sequence[IconTemplate], size: int, style: str | None = None
) -> IconTemplate:
    """Smallest native sprite at least `size` wide (downscaling keeps detail), else the largest,
    of `style` (default: the first style in `templates`)."""
    style = style or styles_in(templates)[0]
    by_size = sorted((t for t in templates if t.style == style), key=lambda t: t.native_size)
    chosen = next((t for t in by_size if t.native_size >= size), by_size[-1])
    return resize_template(chosen, size)
