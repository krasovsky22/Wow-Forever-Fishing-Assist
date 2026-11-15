"""Setup tab: prerequisites, keys, audio device with a live meter, calibration, bite check."""

from __future__ import annotations

from typing import Any

from wowfish.config import CAST_TIMEOUT_MAX_S, CAST_TIMEOUT_MIN_S
from wowfish.gui import image, theme
from wowfish.gui.presenter import (
    CALIBRATE_WATCH_S,
    CalibrateDone,
    Presenter,
    VerifyDone,
    View,
    WorkerFailed,
)

STEPS = (
    ("Stand at the water", "with Fishing on an action bar key (the cast key)."),
    ("Bind Interact With Target", "Options > Keybindings > Targeting, to the interact key."),
    ("Turn on Auto Loot", "Options > Controls."),
    ("Show the bobber's gear icon", "type /console SoftTargetIconGameObject 1 once."),
    ("Quiet the game", "set Ambience and Music to 0 so the splash is easy to hear."),
    ("Optional: a quit macro", "put /quit on a key and enter it as the quit key; the bot "
     "presses it when the time limit runs out."),
)  # fmt: skip
CHECKLIST_FOOTER = (
    "Then press Start fishing on the Fish tab. The first cast of every run measures the gear "
    "icon, so the bite check and calibration are only needed when fishing misbehaves."
)
RECAST_HINT = "Recast after: 1-2 s more than a cast lasts in the game."
VERIFY_HINT = "Click, then fish by hand until a fish bites (records for 60 s)."
CALIBRATE_HINT = (
    f"Click, then cast in the game: it watches for the gold gear for up to {CALIBRATE_WATCH_S:g} s."
)
NOTE_WRAP = 460


class SetupPage:
    def __init__(self, parent: Any, presenter: Presenter) -> None:
        import ttkbootstrap as tb

        self.p = presenter
        self.frame = tb.Frame(parent, padding=(4, 14, 4, 4))
        cols = tb.Frame(self.frame)
        cols.pack(fill="both", expand=True)
        cols.columnconfigure(0, weight=4, uniform="col")
        cols.columnconfigure(1, weight=5, uniform="col")
        left, right = tb.Frame(cols), tb.Frame(cols)
        left.grid(row=0, column=0, sticky="nsew")
        right.grid(row=0, column=1, sticky="nsew", padx=(14, 0))

        def note(parent: Any, text: str = "") -> Any:
            return tb.Label(
                parent, text=text, style="CardMuted.TLabel", wraplength=NOTE_WRAP, justify="left"
            )

        # -- before you start ---------------------------------------------------------------
        steps, body = theme.card(left, "Before you start", "In the game, once.")
        steps.pack(fill="x")
        body.columnconfigure(1, weight=1)
        for i, (title, detail) in enumerate(STEPS):
            tb.Label(body, text=str(i + 1), style="Badge.TLabel", width=2).grid(
                row=i, column=0, sticky="nw", pady=2
            )
            tb.Label(
                body, text=f"{title}: {detail}", style="Card.TLabel", wraplength=340,
                justify="left",
            ).grid(row=i, column=1, sticky="w", pady=2)  # fmt: skip
        note(body, CHECKLIST_FOOTER).grid(row=len(STEPS), column=0, columnspan=2, sticky="w",
                                          pady=(8, 0))  # fmt: skip
        for w in body.grid_slaves(row=len(STEPS)):
            w.configure(wraplength=360)

        # -- keys and timing ------------------------------------------------------------------
        keys_card, body = theme.card(
            left, "Keys and timing", "Key names: a letter, digit, F1-F12, or space."
        )
        keys_card.pack(fill="x", pady=(14, 0))
        self.cast = self._field(body, 0, 0, "Cast", presenter.cfg.game.cast_key)
        self.interact = self._field(body, 0, 1, "Interact", presenter.cfg.game.interact_key)
        self.quit = self._field(body, 0, 2, "Quit (optional)", presenter.cfg.game.quit_key)
        self.minutes = self._field(
            body, 2, 0, "Stop after (min, 0 = never)", str(presenter.cfg.game.session_minutes),
            spin=(0, 1440, 15),
        )  # fmt: skip
        self.recast = self._field(
            body, 2, 1, "Recast after (s)", f"{presenter.cfg.run.cast_timeout_s:g}",
            spin=(CAST_TIMEOUT_MIN_S, CAST_TIMEOUT_MAX_S, 1),
        )  # fmt: skip
        for w in (self.cast, self.interact, self.quit, self.minutes, self.recast):
            w.bind("<Return>", lambda _e: self._save_all())
        tb.Button(body, text="Save", command=self._save_all, bootstyle="primary", width=8).grid(
            row=3, column=2, sticky="e"
        )
        self.keys_note = self.limit_note = self.recast_note = note(body, RECAST_HINT)
        self.keys_note.configure(wraplength=360)
        self.keys_note.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # -- audio ----------------------------------------------------------------------------
        audio, body = theme.card(right, "Audio", "What the bot listens to for the splash.")
        audio.pack(fill="x")
        body.columnconfigure(0, weight=1)
        self.devices = presenter.audio_devices()
        self.device = tb.Combobox(body, values=[d.name for d in self.devices], state="readonly")
        current = presenter.cfg.audio.device
        pick = next(
            (i for i, d in enumerate(self.devices) if current and current in (d.id, d.name)), None
        )
        if pick is None:
            default = presenter.default_device()
            pick = self.devices.index(default) if default in self.devices else None
        if pick is not None:
            self.device.current(pick)
        self.device.grid(row=0, column=0, sticky="ew")
        tb.Button(
            body, text="Use and listen", command=self._save_device, bootstyle="secondary"
        ).grid(row=0, column=1, padx=(10, 0))
        meter_row = tb.Frame(body, style="CardBody.TFrame")
        meter_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        tb.Label(meter_row, text="Level", style="Card.TLabel").pack(side="left")
        self.meter = tb.Progressbar(meter_row, maximum=100, value=0, bootstyle="info", length=200)
        self.meter.pack(side="left", padx=10)
        self.level_text = tb.Label(meter_row, text="", style="CardMuted.TLabel")
        self.level_text.pack(side="left")
        self._meter_error = ""

        tb.Separator(body).grid(row=2, column=0, columnspan=2, sticky="ew", pady=12)
        bite_row = tb.Frame(body, style="CardBody.TFrame")
        bite_row.grid(row=3, column=0, columnspan=2, sticky="ew")
        tb.Label(bite_row, text="Bite threshold", style="Card.TLabel").pack(side="left")
        self.bite = tb.Entry(bite_row, width=6)
        self.bite.insert(0, f"{presenter.cfg.audio.bite_threshold:.2f}")
        self.bite.pack(side="left", padx=8)
        self.bite.bind("<Return>", lambda _e: self._save_bite())
        tb.Button(bite_row, text="Save", command=self._save_bite, bootstyle="primary").pack(
            side="left"
        )
        self.bite_note = note(body, "A splash must score at least this to count.")
        self.bite_note.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        verify_row = tb.Frame(body, style="CardBody.TFrame")
        verify_row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.verify_button = tb.Button(
            verify_row, text="Check the bite (60 s)", command=self._verify, bootstyle="info"
        )
        self.verify_button.pack(side="left")
        self._meter_was_on = False
        self.verify_note = tb.Label(
            verify_row, text=VERIFY_HINT, style="CardMuted.TLabel", wraplength=300,
            justify="left",
        )  # fmt: skip
        self.verify_note.pack(side="left", padx=(10, 0))

        # -- calibration ----------------------------------------------------------------------
        cal, body = theme.card(right, "Gear icon", "What the bot looks for over the bobber.")
        cal.pack(fill="both", expand=True, pady=(14, 0))
        buttons = tb.Frame(body, style="CardBody.TFrame")
        buttons.pack(fill="x")
        self.cal_button = tb.Button(
            buttons, text="Calibrate", command=self._calibrate, bootstyle="info"
        )
        self.cal_button.pack(side="left")
        self.save_cal = tb.Button(
            buttons, text="Save calibration", command=self._save_cal, bootstyle="primary",
            state="disabled",
        )  # fmt: skip
        self.save_cal.pack(side="left", padx=(10, 0))
        size = presenter.cfg.vision.icon_size_px
        current = f"Saved: {size} px. " if size > 0 else "Not calibrated yet. "
        self.cal_note = note(body, current + CALIBRATE_HINT)
        self.cal_note.pack(anchor="w", pady=(8, 0))
        self.cal_image = tb.Label(body, style="Card.TLabel")
        self.cal_image.pack(anchor="w", pady=(8, 0))
        self._photo = None

    @staticmethod
    def _field(
        parent: Any, row: int, column: int, label: str, value: str,
        spin: tuple[float, float, float] | None = None,
    ) -> Any:  # fmt: skip
        """A muted label over an entry (or a spinbox), in a grid cell of a card."""
        import ttkbootstrap as tb

        pad = (0 if column == 0 else 12, 0)
        tb.Label(parent, text=label, style="CardMuted.TLabel").grid(
            row=row, column=column, sticky="w", padx=pad, pady=(8 if row else 0, 2)
        )
        if spin is None:
            entry = tb.Entry(parent, width=10)
            entry.insert(0, value)
        else:
            lo, hi, step = spin
            entry = tb.Spinbox(parent, from_=lo, to=hi, increment=step, width=8)
            entry.set(value)
        entry.grid(row=row + 1, column=column, sticky="w", padx=pad)
        return entry

    # -- actions ----------------------------------------------------------------------------

    def _save_keys(self) -> None:
        try:
            self.p.save_keys(self.cast.get(), self.interact.get(), self.quit.get())
            self.keys_note.configure(text="Saved.")
        except ValueError as e:
            self.keys_note.configure(text=str(e))

    def _save_all(self) -> None:
        """Save the keys, then the time limit and recast time; one note says how it went."""
        try:
            self.p.save_keys(self.cast.get(), self.interact.get(), self.quit.get())
        except ValueError as e:
            self.keys_note.configure(text=f"keys: {e}")
            return
        self._save_session()

    def _save_session(self) -> None:
        """Save both session settings; the note says what was saved or what is wrong."""
        problems = []
        try:
            self.p.save_session_minutes(int(self.minutes.get()))
        except ValueError:
            problems.append("time limit: enter whole minutes, 0 for never")
        try:
            self.p.save_cast_timeout(self.recast.get())
        except ValueError as e:
            problems.append(f"recast: {e}")
        if problems:
            self.limit_note.configure(text="; ".join(problems))
            return
        quit_key = self.p.cfg.game.quit_key
        then = f"; at the time limit the bot presses {quit_key} to quit" if quit_key else ""
        self.limit_note.configure(text=f"Saved; used from the next Start{then}.")

    def _selected_device(self) -> str | None:
        i = self.device.current()
        return self.devices[i].id if 0 <= i < len(self.devices) else None

    def _save_device(self) -> None:
        sel = self._selected_device()
        if sel is not None:
            self.p.save_audio_device(sel)
            self._meter_error = ""
            self.p.start_meter(sel)

    def _verify(self) -> None:
        self._meter_was_on = self.p.meter_running
        self.p.stop_meter()  # the recording shows its own level on the meter
        self.verify_button.configure(state="disabled")
        self.verify_note.configure(text="Starting the recording...")
        self.p.record_and_verify(60.0, self._selected_device())

    def _calibrate(self) -> None:
        self.cal_button.configure(state="disabled")
        self.save_cal.configure(state="disabled")
        self.cal_note.configure(text="Starting...")
        self.p.grab_and_calibrate()

    def _save_bite(self) -> None:
        try:
            saved = self.p.save_bite_threshold(self.bite.get())
        except ValueError as e:
            self.bite_note.configure(text=str(e))
            return
        self.bite_note.configure(text=f"saved {saved:.2f}; used from the next Start")

    def _save_cal(self) -> None:
        self.p.save_calibration()
        self.cal_note.configure(text=self.cal_note.cget("text") + "  (saved)")

    # -- rendering --------------------------------------------------------------------------

    def render(self, view: View) -> None:
        pct = max(0.0, min(100.0, (view.level_dbfs + 60.0) / 60.0 * 100.0))
        self.meter.configure(value=pct)
        if not view.listening:
            self.level_text.configure(
                text=self._meter_error or "not listening (press Use and listen)"
            )
        else:
            level = "silent" if view.level_dbfs <= -60 else f"{view.level_dbfs:5.1f} dBFS"
            best = max(view.last_scores.values(), default=None)
            score = f"   bite score {best:.2f}" if best is not None and best > -1.0 else ""
            self.level_text.configure(text=level + score)
        if view.verify_status:
            self.verify_note.configure(text=view.verify_status)
        if view.calibrate_status:
            self.cal_note.configure(text=view.calibrate_status)
        for item, line in zip(view.log_events, view.new_lines, strict=True):
            match item:
                case CalibrateDone(calibration=cal):
                    self._photo = image.photo_from_png(item.thumbnail_png)
                    if self._photo.width() > 360:  # keep the Setup tab on one screen
                        self._photo = self._photo.subsample(2)
                    self.cal_image.configure(image=self._photo)
                    if cal.valid:
                        self.cal_note.configure(
                            text=f"Found it: {cal.icon_size_px} px, score {cal.score:.2f}. "
                            "Save calibration to keep it."
                        )
                        self.save_cal.configure(state="normal")
                    else:
                        self.cal_note.configure(
                            text=f"No gold gear icon seen in {CALIBRATE_WATCH_S:g} s. "
                            "Is SoftTargetIconGameObject on "
                            "and the bobber close enough to interact with? Try again."
                        )
                    self.cal_button.configure(state="normal")
                case VerifyDone(report=report):
                    self.verify_note.configure(text=line)
                    if report.stands_out:  # suggest, but only Save changes the config
                        self.bite.delete(0, "end")
                        self.bite.insert(0, f"{report.threshold:.2f}")
                        self.bite_note.configure(text="suggested by the bite check; Save to use it")
                    self._verify_finished()
                case WorkerFailed(what="verify the bite"):
                    self.verify_note.configure(text=line)
                    self._verify_finished()
                case WorkerFailed(what="calibrate"):
                    self.cal_note.configure(text=line)
                    self.cal_button.configure(state="normal")
                case WorkerFailed():
                    self._meter_error = line

    def _verify_finished(self) -> None:
        self.verify_button.configure(state="normal")
        if self._meter_was_on:
            self._meter_was_on = False
            self.p.start_meter(self._selected_device())
