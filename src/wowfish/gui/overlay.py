"""The mini bar shown over the game while the bot fishes: state, a few numbers, Stop, and a
button back to the main window.

It is a small borderless window that stays on top. On Windows it never takes keyboard focus
(clicking Stop leaves the game focused) and is left out of screen captures, so the bot never
sees it. Elsewhere it is kept small, and Stop ends the run anyway. Drag it anywhere; the game
gets focus back when the drag ends.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable
from typing import Any

from wowfish.gui import theme
from wowfish.gui.fish_page import tile_values

BAR_BG = "#0b0f15"
BAR_EDGE = "#2c3644"
ALPHA = 0.93
TOP_MARGIN = 8  # px from the top edge of the screen

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080  # no taskbar button
WS_EX_NOACTIVATE = 0x08000000  # clicks do not take focus from the game
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Windows 10 2004+: invisible to screen grabs


class MiniBar:
    def __init__(
        self,
        root: Any,
        *,
        on_stop: Callable[[], None],
        on_open: Callable[[], None],
        on_moved: Callable[[], None] | None = None,
        logo: Any = None,
    ) -> None:
        import tkinter as tk

        self._on_moved = on_moved or (lambda: None)
        self.top = top = tk.Toplevel(root, background=BAR_EDGE)
        top.withdraw()
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        with contextlib.suppress(tk.TclError):  # no compositor on some X11 setups
            top.attributes("-alpha", ALPHA)

        body = tk.Frame(top, background=BAR_BG, padx=10, pady=6)
        body.pack(padx=1, pady=1)
        font = (theme.FONT, 9)
        bold = (theme.FONT, 10, "bold")

        if logo is not None:
            self.logo = tk.Label(body, image=logo, bg=BAR_BG)
            self.logo.pack(side="left", padx=(0, 8))
        self.dot = tk.Canvas(body, width=10, height=10, background=BAR_BG, highlightthickness=0)
        self._dot = self.dot.create_oval(1, 1, 9, 9, fill=theme.GRID, outline="")
        self.dot.pack(side="left", padx=(0, 6))
        self.state = tk.Label(body, text="Starting", font=bold, fg=theme.FG, bg=BAR_BG)
        self.state.pack(side="left")

        self.stats = tk.Label(body, text="", font=font, fg=theme.MUTED, bg=BAR_BG)
        self.stats.pack(side="left", padx=(12, 12))

        def button(text: str, command: Callable[[], None], color: str) -> tk.Label:
            # Labels, not Buttons: a Tk button takes focus on click on some platforms.
            b = tk.Label(
                body, text=text, font=(theme.FONT, 9, "bold"), fg=color, bg="#161d27",
                padx=10, pady=2, cursor="hand2",
            )  # fmt: skip
            b.bind("<ButtonRelease-1>", lambda _e: command())
            b.bind("<Enter>", lambda _e: b.configure(bg="#212b38"))
            b.bind("<Leave>", lambda _e: b.configure(bg="#161d27"))
            return b

        self.stop_button = button("Stop", on_stop, theme.RED)
        self.stop_button.pack(side="left")
        self.open_button = button("Open", on_open, theme.FG)
        self.open_button.pack(side="left", padx=(6, 0))

        for w in (
            body,
            self.dot,
            self.state,
            self.stats,
            *([self.logo] if logo else []),
        ):  # drag by anything but the buttons
            w.bind("<ButtonPress-1>", self._grab)
            w.bind("<B1-Motion>", self._drag)
            w.bind("<ButtonRelease-1>", lambda _e: self._on_moved())
        self._grab_at = (0, 0)
        self._dragged = False  # once the player has placed it, it stays where they put it

        self._center()
        top.deiconify()
        top.update_idletasks()
        _windows_tweaks(top)

    def render(self, state: str, did: str, stats: Any) -> None:
        label, color, _ = theme.state_look(state, did)
        self.dot.itemconfigure(self._dot, fill=color)
        self.state.configure(text=label)
        v = tile_values(stats)
        self.stats.configure(
            text=f"{v['catches']} caught  ·  {v['casts']} casts  ·  {v['rate']}  ·  {v['time']}"
        )
        if not self._dragged:
            self._center()  # the text changes width; stay centred

    def close(self) -> None:
        self.top.destroy()

    def _center(self) -> None:
        self.top.update_idletasks()
        x = (self.top.winfo_screenwidth() - self.top.winfo_reqwidth()) // 2
        self.top.geometry(f"+{max(0, x)}+{TOP_MARGIN}")

    def _grab(self, e: Any) -> None:
        self._grab_at = (e.x_root - self.top.winfo_x(), e.y_root - self.top.winfo_y())

    def _drag(self, e: Any) -> None:
        dx, dy = self._grab_at
        self._dragged = True
        self.top.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")


def _windows_tweaks(top: Any) -> None:
    """No focus on click, no taskbar button, and not in screen captures (Windows only)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32: Any = ctypes.windll.user32
        hwnd = user32.GetParent(top.winfo_id()) or top.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    except Exception:  # an older Windows: the bar still works, it just takes focus on click
        pass
