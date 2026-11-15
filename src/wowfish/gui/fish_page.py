"""Fish tab: a status bar (state, what the bot did and does next, Start/Stop), stat tiles,
the frame the icon was matched in, and Scores | Logs sub-tabs (the clip scores of the current
cast, or the event log)."""

from __future__ import annotations

from typing import Any

from wowfish.gui import image, theme
from wowfish.gui.presenter import Presenter, View
from wowfish.gui.score_graph import ScoreGraph

TILES = (
    ("catches", "Catches"),
    ("casts", "Casts"),
    ("misses", "Misses"),
    ("rate", "Catch rate"),
    ("time", "Session"),
)


def tile_values(stats: Any) -> dict[str, str]:
    """The stat tiles' text for an engine Stats event (or None before the first one)."""
    casts = getattr(stats, "casts", 0)
    catches = getattr(stats, "catches", 0)
    elapsed = int(getattr(stats, "elapsed_s", 0.0))
    h, rest = divmod(elapsed, 3600)
    m, s = divmod(rest, 60)
    return {
        "catches": str(catches),
        "casts": str(casts),
        "misses": str(getattr(stats, "misses", 0)),
        "rate": f"{100 * catches / casts:.0f}%" if casts else "-",
        "time": f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}",
    }


class FishPage:
    def __init__(self, parent: Any, presenter: Presenter) -> None:
        from tkinter.scrolledtext import ScrolledText

        import ttkbootstrap as tb

        self.p = presenter
        self.frame = tb.Frame(parent, padding=(4, 14, 4, 4))
        f = self.frame

        # -- status bar: state pill, what happened and what comes next, Start / Stop ------------
        top = tb.Frame(f)
        top.pack(fill="x")
        buttons = tb.Frame(top)
        buttons.pack(side="right", anchor="n")
        self.start = tb.Button(
            buttons, text="Start fishing", command=self._start, bootstyle="primary", width=14
        )
        self.start.pack(side="left", ipady=4)
        self.stop = tb.Button(
            buttons, text="Stop", command=self.p.stop, bootstyle="danger", width=8, state="disabled"
        )
        self.stop.pack(side="left", padx=(8, 0), ipady=4)

        words = tb.Frame(top)
        words.pack(side="left", fill="x", expand=True)
        row = tb.Frame(words)
        row.pack(anchor="w")
        label, bg, fg = theme.state_look("IDLE")
        self.state = tb.Label(row, text=label, style="Pill.TLabel", background=bg, foreground=fg)
        self.state.pack(side="left")
        self.did = tb.Label(row, text="", style="Did.TLabel", wraplength=560, justify="left")
        self.did.pack(side="left", padx=(12, 0))
        self.next = tb.Label(words, text="", style="Next.TLabel", wraplength=720, justify="left")
        self.next.pack(anchor="w", pady=(6, 0))
        self.blockers = tb.Label(words, text="", style="Warn.TLabel", wraplength=720)

        # -- stat tiles -------------------------------------------------------------------------
        tiles = tb.Frame(f)
        tiles.pack(fill="x", pady=(14, 14))
        self.tiles: dict[str, Any] = {}
        for i, (key, name) in enumerate(TILES):
            tiles.columnconfigure(i, weight=1, uniform="tile")
            box = tb.Frame(tiles, style="Card.TFrame", padding=(14, 10))
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            style = "TileGold.TLabel" if key == "catches" else "TileValue.TLabel"
            value = tb.Label(box, text="0", style=style)
            value.pack(anchor="w")
            tb.Label(box, text=name, style="TileName.TLabel").pack(anchor="w")
            self.tiles[key] = value
        # the presenter's one-line summary; kept for anything that still reads it
        self.stats = tb.Label(f, text=presenter.stats)

        # -- what the bot sees, and the scores / log ---------------------------------------------
        mid = tb.Frame(f)
        mid.pack(fill="both", expand=True)
        mid.columnconfigure(1, weight=1)
        mid.rowconfigure(0, weight=1)
        seen, seen_body = theme.card(mid, "What the bot sees", "The frame the bobber was found in")
        seen.grid(row=0, column=0, sticky="nsew")
        self.image = tb.Label(
            seen_body,
            text="Nothing yet: the frame shows here\nonce the bobber is found.",
            style="CardMuted.TLabel",
            anchor="center",
            justify="center",
            width=52,
        )
        self.image.pack(fill="both", expand=True, ipady=40)
        self._photo = None

        right = tb.Frame(mid)
        right.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        tabs = tb.Notebook(right)
        tabs.pack(fill="both", expand=True)
        self.graph = ScoreGraph(tabs)
        self.log = ScrolledText(
            tabs, height=18, wrap="word", background=theme.CARD, foreground=theme.FG,
            insertbackground=theme.FG, relief="flat", borderwidth=0, padx=10, pady=8,
            font=(theme.FONT, 10),
        )  # fmt: skip
        self.log.tag_configure("error", foreground=theme.RED)
        self.log.tag_configure("good", foreground=theme.TEAL)
        self.log.tag_configure("muted", foreground=theme.MUTED)
        self.log.configure(state="disabled")
        tabs.add(self.graph.canvas, text="Splash scores")  # first, so it is the tab shown at start
        tabs.add(self.log, text="Log")

    def _start(self) -> None:
        reasons = self.p.prerequisites(request=True)
        if reasons:
            self.blockers.configure(text="Can't start: " + "; ".join(reasons))
            self.blockers.pack(anchor="w", pady=(4, 0))
            return
        self.blockers.pack_forget()
        self.p.start()

    def render(self, view: View) -> None:
        label, bg, fg = theme.state_look(view.state, view.did)
        self.state.configure(text=label, background=bg, foreground=fg)
        self.did.configure(text=view.did)
        self.next.configure(text=f"Next: {view.next}")
        self.stats.configure(text=view.stats)
        for key, text in tile_values(view.stats_values).items():
            self.tiles[key].configure(text=text)
        self.start.configure(state="disabled" if view.running else "normal")
        self.stop.configure(state="normal" if view.running else "disabled")
        if view.thumbnail_png is not None and view.thumbnail_png is not getattr(
            self, "_last_png", None
        ):
            self._last_png = view.thumbnail_png
            self._photo = image.photo_from_png(view.thumbnail_png)
            self.image.configure(image=self._photo, text="")
        self.graph.show(view.trace, self.p.cfg.run.cast_timeout_s)
        if view.new_lines:
            self.log.configure(state="normal")
            for line in view.new_lines:
                self.log.insert("end", line + "\n", _log_tag(line))
            self.log.see("end")
            self.log.configure(state="disabled")


def _log_tag(line: str) -> str:
    if line.startswith(("Error", "Cannot", "Stopped (error)")):
        return "error"
    if line.startswith(("Caught", "Heard the splash", "Measured")):
        return "good"
    if line.startswith(("casts ", "Best match so far")):
        return "muted"
    return ""
