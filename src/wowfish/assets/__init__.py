"""Files shipped inside the package: interact-icon sprites and the game's splash clips."""

from __future__ import annotations

import sys
from pathlib import Path

ICON_SIZES: tuple[int, ...] = (29, 42, 53, 79, 103)  # the gold gear sprites
# The round soft-target badge some clients show over the bobber instead of the gear: a dark
# disc with an anvil inside a yellow ring, cut from an in-game screenshot.
BADGE_SIZES: tuple[int, ...] = (52,)
ICON_STYLES: dict[str, tuple[int, ...]] = {"gear": ICON_SIZES, "badge": BADGE_SIZES}
# The splash sounds the game plays when a fish bites; any of them counts as the bite.
CLIP_NAMES: tuple[str, ...] = ("fishingbobber_1", "fishingbobber_2", "fishingbobber_3", "fishbite")


def asset_dir() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "wowfish" / "assets"
    return Path(__file__).resolve().parent


def icon_path(native_size: int) -> Path:
    if native_size in BADGE_SIZES:
        return asset_dir() / "interact_icon" / f"badge_{native_size}.png"
    return asset_dir() / "interact_icon" / f"interact_gold_{native_size}.png"


def style_of(native_size: int) -> str | None:
    """The icon style ("gear" or "badge") a saved native sprite size belongs to, if any."""
    return next((s for s, sizes in ICON_STYLES.items() if native_size in sizes), None)


def clip_path(name: str) -> Path:
    return asset_dir() / "clips" / f"{name}.wav"


def app_icon_path(size: int = 256) -> Path:
    """The app's logo as a PNG: 256 px (icon.png), or 32 or 16 px drawn for small sizes."""
    return asset_dir() / ("icon.png" if size == 256 else f"icon-{size}.png")


def default_config_path() -> Path:
    return asset_dir() / "default_config"
