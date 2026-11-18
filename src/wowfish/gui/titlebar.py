"""A title bar and window border that match the dark theme (Windows only).

Windows draws the title bar and the 1 px window border itself, white by default, which looks
like a bright frame around the dark window. DWM lets an app ask for a dark title bar
(Windows 10 20H1+) and pick the caption, caption text and border colours (Windows 11).
Older systems ignore what they don't know, so the window simply keeps the system frame."""

from __future__ import annotations

import sys
from typing import Any

from wowfish.gui import theme

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19  # Windows 10 before 20H1
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def colorref(hex_color: str) -> int:
    """'#rrggbb' -> a Win32 COLORREF (0x00bbggrr)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def frame_colors() -> dict[int, int]:
    """DWM attribute -> COLORREF for the caption, its text, and the border."""
    return {
        DWMWA_CAPTION_COLOR: colorref(theme.BG),
        DWMWA_TEXT_COLOR: colorref(theme.FG),
        DWMWA_BORDER_COLOR: colorref(theme.CARD_EDGE),
    }


def dark_frame(win: Any) -> None:
    """Give a Tk window a dark title bar and a themed border. A no-op off Windows."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        win.update_idletasks()  # the native window must exist before DWM can style it
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        dwm: Any = ctypes.windll.dwmapi

        def put(attr: int, value: int) -> bool:
            v = ctypes.c_int(value)
            return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0

        if not put(DWMWA_USE_IMMERSIVE_DARK_MODE, 1):
            put(DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, 1)
        for attr, color in frame_colors().items():
            put(attr, color)  # Windows 10 refuses these; its dark title bar is close enough
    except Exception:  # cosmetic only: keep the system frame
        pass
