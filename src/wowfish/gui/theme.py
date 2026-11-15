"""The window's look: a dark "deep water" theme with the interact gear's gold as its accent,
named ttk styles for cards and stat tiles, and the words and colours for each engine state.

Everything here is plain data or ttk style setup; the pages pick styles by name."""

from __future__ import annotations

from typing import Any

THEME = "wowfish-dark"

# The palette. The ttk theme is generated from the accents below; the raw values are also
# used directly by the Canvas graph and the card styles, which ttk cannot derive.
BG = "#10151c"  # window
CARD = "#18202a"  # raised surface for cards and tiles
CARD_EDGE = "#253040"
FG = "#e6e9ee"
MUTED = "#95a1ae"
GRID = "#2c3644"
GOLD = "#e6b751"  # the interact gear; the app's accent
TEAL = "#3fb68b"
BLUE = "#4aa3df"
ORANGE = "#f08c3a"
RED = "#e5534b"

FONT = "TkDefaultFont"

# Engine state name -> (what the user reads, pill colour).
STATES: dict[str, tuple[str, str]] = {
    "IDLE": ("Ready", GRID),
    "CASTING": ("Casting", BLUE),
    "WAIT_BITE": ("Waiting for a bite", GOLD),
    "INTERACT": ("Looting", TEAL),
    "PAUSED": ("Paused", ORANGE),
    "STOPPED": ("Stopped", GRID),
    "ERROR": ("Error", RED),
}


def state_look(state: str, did: str = "") -> tuple[str, str, str]:
    """(label, pill background, pill text colour) for an engine state name."""
    if state == "STOPPED" and did.startswith(("Stopped (error)", "Error")):
        state = "ERROR"
    label, color = STATES.get(state, (state.replace("_", " ").title(), GRID))
    text = BG if color in (GOLD, TEAL, ORANGE, BLUE) else FG
    return label, color, text


def register() -> None:
    """Register the theme family; call before the Window is created."""
    import ttkbootstrap as tb

    tb.Theme(
        name="wowfish",
        primary=GOLD,
        success=TEAL,
        info=BLUE,
        warning=ORANGE,
        danger=RED,
        dark={"background": BG, "foreground": FG},
    ).register()


def apply_styles(style: Any) -> None:
    """Named styles the pages use. Call once the Window (and so the theme) exists."""
    base = (FONT, 10)
    style.configure(".", font=base)
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(16, 7), font=(FONT, 10, "bold"))
    style.configure("Card.TFrame", background=CARD, bordercolor=CARD_EDGE, relief="solid")
    style.configure("CardBody.TFrame", background=CARD, borderwidth=0, relief="flat")
    style.configure("Card.TLabel", background=CARD, foreground=FG, font=base)
    style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED, font=(FONT, 9))
    style.configure("CardTitle.TLabel", background=CARD, foreground=FG, font=(FONT, 11, "bold"))
    style.configure("CardAccent.TLabel", background=CARD, foreground=GOLD, font=(FONT, 9, "bold"))
    style.configure("Badge.TLabel", background=CARD, foreground=GOLD, font=(FONT, 11, "bold"))
    style.configure("TileValue.TLabel", background=CARD, foreground=FG, font=(FONT, 20, "bold"))
    style.configure("TileGold.TLabel", background=CARD, foreground=GOLD, font=(FONT, 20, "bold"))
    style.configure("TileName.TLabel", background=CARD, foreground=MUTED, font=(FONT, 9))
    style.configure("Brand.TLabel", background=BG, foreground=GOLD, font=(FONT, 16, "bold"))
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=(FONT, 9))
    style.configure("Did.TLabel", background=BG, foreground=FG, font=(FONT, 14, "bold"))
    style.configure("Next.TLabel", background=BG, foreground=MUTED, font=(FONT, 10))
    style.configure("Warn.TLabel", background=BG, foreground=ORANGE, font=(FONT, 9))
    style.configure("Pill.TLabel", font=(FONT, 10, "bold"), padding=(12, 4))


def card(parent: Any, title: str, subtitle: str = "", padding: int = 14) -> tuple[Any, Any]:
    """A raised card with a bold title (and optional muted subtitle); returns (card, body)."""
    import ttkbootstrap as tb

    outer = tb.Frame(parent, style="Card.TFrame", padding=padding)
    tb.Label(outer, text=title, style="CardTitle.TLabel").pack(anchor="w")
    if subtitle:
        tb.Label(outer, text=subtitle, style="CardMuted.TLabel", wraplength=520).pack(
            anchor="w", pady=(2, 0)
        )
    body = tb.Frame(outer, style="CardBody.TFrame")
    body.pack(fill="both", expand=True, pady=(10, 0))
    return outer, body
