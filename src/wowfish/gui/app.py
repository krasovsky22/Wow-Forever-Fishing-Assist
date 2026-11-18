"""The ttkbootstrap window: a Setup tab and a Fish tab over one Presenter."""

from __future__ import annotations

import gc

from wowfish import __version__
from wowfish.gui import theme
from wowfish.gui.fish_page import FishPage
from wowfish.gui.overlay import MiniBar
from wowfish.gui.presenter import Presenter
from wowfish.gui.setup_page import SetupPage
from wowfish.gui.updater import Updater

APP_NAME = "Wow Forever - Fishing Assist"

POLL_MS = 50


class App:
    def __init__(self, presenter: Presenter | None = None) -> None:
        import ttkbootstrap as tb

        self.p = presenter or Presenter()
        _windows_app_id()
        theme.register()
        self.root = tb.Window(
            themename=theme.THEME, title=f"{APP_NAME} {__version__}", minsize=(1000, 760)
        )
        theme.apply_styles(tb.Style())
        self._set_icon()
        bar = tb.Frame(self.root, padding=(16, 12, 16, 0))
        bar.pack(fill="x")
        if self._icons:
            tb.Label(bar, image=self._icons[1], background=theme.BG).pack(side="left", padx=(0, 10))
        tb.Label(bar, text=APP_NAME, style="Brand.TLabel").pack(side="left")
        tb.Label(bar, text=f"  v{__version__}", style="Muted.TLabel").pack(side="left", pady=(6, 0))
        self.reset_button = tb.Button(
            bar, text="Reset settings", command=self._reset_settings, bootstyle="secondary"
        )
        self.reset_button.pack(side="right", pady=(4, 0))
        self.update_button = tb.Button(
            bar, text="Check for updates", command=self._check_updates, bootstyle="secondary"
        )
        self.update_button.pack(side="right", padx=(0, 8), pady=(4, 0))
        nb = tb.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=12, pady=(8, 12))
        self.setup = SetupPage(nb, self.p)
        self.fish = FishPage(nb, self.p)
        nb.add(self.setup.frame, text="Setup")
        nb.add(self.fish.frame, text="Fish")
        if not self.p.prerequisites():
            nb.select(1)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.bar: MiniBar | None = None
        self._was_running = False
        self.updater = Updater(self.root, self.p, on_quit=self.close)
        self.updater.start()
        self._after = self.root.after(POLL_MS, self._tick)

    def _check_updates(self) -> None:
        self.updater.check(asked=True)

    def _set_icon(self) -> None:
        """The logo in the title bar and taskbar, for this window and the ones it opens.
        Keeps the images (256, 32, 16 px) for the header and the mini bar too."""
        import tkinter as tk

        from wowfish.assets import app_icon_path

        try:
            self._icons = [tk.PhotoImage(file=str(app_icon_path(s))) for s in (256, 32, 16)]
            self.root.iconphoto(True, *self._icons)
        except (tk.TclError, OSError):  # a missing or unreadable icon is only cosmetic
            self._icons = []

    def tick(self) -> None:
        view = self.p.poll()
        self.fish.render(view)
        self.setup.render(view)
        if view.running and not self._was_running:
            self._fishing_started()
        elif self._was_running and not view.running:
            self._fishing_stopped()
        self._was_running = view.running
        self.reset_button.configure(state="disabled" if view.running else "normal")
        self.updater.poll()
        if self.root is None:  # the updater closed the app to restart it
            return
        busy = view.running or self.updater.busy
        self.update_button.configure(state="disabled" if busy else "normal")
        if self.bar is not None:
            self.bar.render(view.state, view.did, view.stats_values)

    def _reset_settings(self) -> None:
        """Ask, then put every setting back to its default; the old file is kept as a .bak."""
        from tkinter import messagebox

        if self.p.running:
            messagebox.showinfo("Reset settings", "Stop fishing first.", parent=self.root)
            return
        sure = messagebox.askyesno(
            "Reset settings",
            "Put every setting back to its default?\n\nKeys, time limits, the audio device, "
            "the bite threshold and the icon calibration all go back to their defaults. Your "
            "current settings are kept as a backup (config.toml.bak).",
            icon="warning",
            parent=self.root,
        )
        if not sure:
            return
        try:
            backup = self.p.reset_config()
        except (OSError, RuntimeError) as e:
            messagebox.showerror("Reset settings", f"Could not reset: {e}", parent=self.root)
            return
        self.setup.load_values()
        kept = f"\n\nYour old settings are in {backup}." if backup else ""
        messagebox.showinfo("Reset settings", "Settings are back to their defaults." + kept,
                            parent=self.root)  # fmt: skip

    def _fishing_started(self) -> None:
        """The game gets the screen; a mini bar over it keeps Stop and the numbers in reach.
        With gui.mini_bar off the main window simply stays as it is (Start already focused
        the game)."""
        if not self.p.cfg.gui.mini_bar:
            return
        self.bar = MiniBar(
            self.root, on_stop=self.p.stop, on_open=self._show_main, on_moved=self.p.focus_game,
            logo=self._icons[2] if self._icons else None,
        )  # fmt: skip
        self.p.focus_game()  # again: on some platforms the new bar took focus from the game

    def _fishing_stopped(self) -> None:
        if self.bar is not None:
            self.bar.close()
            self.bar = None
        self._show_main()

    def _show_main(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tick(self) -> None:
        try:
            self.tick()
        finally:
            if self.root is not None:  # None once the app closed itself (an update restart)
                self._after = self.root.after(POLL_MS, self._tick)

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self.close()

    def close(self) -> None:
        """Stop everything and free the Tk interpreter on this thread.

        Tcl panics (an Illegal-instruction trap) if its interpreter is deleted from another
        thread, which is what happens when a later garbage collection on a worker thread frees
        a window that was merely destroyed. So drop every reference and collect here.
        """
        if self.root is None:
            return
        if self._after is not None:
            self.root.after_cancel(self._after)
            self._after = None
        self.p.shutdown()
        if self.bar is not None:
            self.bar.close()
            self.bar = None
        self.root.destroy()
        self.setup = self.fish = None  # type: ignore[assignment]
        self.root = None  # type: ignore[assignment]
        gc.collect()


def _windows_app_id() -> None:
    """Give the process its own taskbar identity on Windows, so the taskbar shows the window's
    logo rather than the Python interpreter's icon. Must run before the first window."""
    import sys

    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WowFishingAssist.wowfish")
    except Exception:  # cosmetic only
        pass


def run() -> None:
    App().run()
