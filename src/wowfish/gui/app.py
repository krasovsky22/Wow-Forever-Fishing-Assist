"""The ttkbootstrap window: a Setup tab and a Fish tab over one Presenter."""

from __future__ import annotations

import gc

from wowfish import __version__
from wowfish.gui import theme
from wowfish.gui.fish_page import FishPage
from wowfish.gui.overlay import MiniBar
from wowfish.gui.presenter import Presenter
from wowfish.gui.setup_page import SetupPage

POLL_MS = 50


class App:
    def __init__(self, presenter: Presenter | None = None) -> None:
        import ttkbootstrap as tb

        self.p = presenter or Presenter()
        theme.register()
        self.root = tb.Window(
            themename=theme.THEME, title=f"wowfish {__version__}", minsize=(1000, 760)
        )
        theme.apply_styles(tb.Style())
        bar = tb.Frame(self.root, padding=(16, 12, 16, 0))
        bar.pack(fill="x")
        tb.Label(bar, text="wowfish", style="Brand.TLabel").pack(side="left")
        tb.Label(bar, text=f"  v{__version__}", style="Muted.TLabel").pack(side="left", pady=(6, 0))
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
        self._after = self.root.after(POLL_MS, self._tick)

    def tick(self) -> None:
        view = self.p.poll()
        self.fish.render(view)
        self.setup.render(view)
        if view.running and not self._was_running:
            self._fishing_started()
        elif self._was_running and not view.running:
            self._fishing_stopped()
        self._was_running = view.running
        if self.bar is not None:
            self.bar.render(view.state, view.did, view.stats_values)

    def _fishing_started(self) -> None:
        """The game gets the screen; a mini bar over it keeps Stop and the numbers in reach."""
        self.bar = MiniBar(
            self.root, on_stop=self.p.stop, on_open=self._show_main, on_moved=self.p.focus_game
        )
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


def run() -> None:
    App().run()
