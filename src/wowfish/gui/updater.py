"""The update check in the window: ask GitHub in the background, then offer the new version
in a small dialog (Update now / Skip this version / Later).

Network work runs on a worker thread; results come back through a queue that the App drains
on its Tk tick, because Tk must only be touched from its own thread.
"""

from __future__ import annotations

import queue
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from wowfish import __version__, update
from wowfish.gui import theme

STARTUP_DELAY_MS = 1500  # let the window draw first
NOTES_MAX_CHARS = 4000


class Updater:
    def __init__(
        self,
        root: Any,
        presenter: Any,
        on_quit: Callable[[], None],
        *,
        fetch: Callable[[], update.Release] = update.fetch_latest,
        download: Callable[..., Path] = update.download_update,
        install: Callable[[Path, Path], None] = update.install_update,
        relaunch: Callable[[Path], None] = update.relaunch,
        exe: Path | bool | None = True,
        current: str = __version__,
        open_page: Callable[[str], Any] = webbrowser.open,
        ui: Any = None,
    ) -> None:
        self.root = root
        self.p = presenter
        self._on_quit = on_quit
        self._fetch, self._download, self._install = fetch, download, install
        self._relaunch, self._open_page = relaunch, open_page
        self.exe: Path | None = update.running_executable() if exe is True else exe or None
        self.current = current
        self.ui = ui or _TkUi(root)
        self.busy = False
        self.dialog: UpdateDialog | None = None
        self._q: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._cancel = threading.Event()

    # -- starting a check --------------------------------------------------------------------

    def start(self) -> None:
        """At window start: tidy up after a previous update, then check if the user wants it.
        Only an executable checks by itself; from source the button still works."""
        if self.exe is None:
            return
        threading.Thread(
            target=update.remove_leftovers, args=(self.exe,), name="wowfish-update-tidy",
            daemon=True,
        ).start()  # fmt: skip
        if self.p.cfg.updates.check:
            self.root.after(STARTUP_DELAY_MS, lambda: self.check(asked=False))

    def check(self, asked: bool = True) -> None:
        """Look for a newer release. `asked`: the user clicked Check for updates, so say so
        when there is none or GitHub cannot be reached (a startup check stays quiet)."""
        if self.busy or self.dialog is not None:
            return
        self.busy = True
        threading.Thread(
            target=self._check_worker, args=(asked,), name="wowfish-update-check", daemon=True
        ).start()

    def _check_worker(self, asked: bool) -> None:
        try:
            self._q.put(("checked", self._fetch(), None, asked))
        except update.UpdateError as e:
            self._q.put(("checked", None, str(e), asked))
        except Exception as e:  # never let a check take the window down
            logger.exception("update check failed")
            self._q.put(("checked", None, str(e), asked))

    # -- results, on the Tk thread -------------------------------------------------------------

    def poll(self) -> None:
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                return
            kind, *rest = item
            if kind == "checked":
                self._checked(*rest)
            elif kind == "progress" and self.dialog is not None:
                self.dialog.progress(*rest)
            elif kind == "downloaded":
                self._downloaded(*rest)
            elif kind == "failed":
                self._failed(rest[0])

    def _checked(self, release: update.Release | None, error: str | None, asked: bool) -> None:
        self.busy = False
        if release is None:
            logger.info(f"update check: {error}")
            if asked:
                self.ui.error("Check for updates", f"Could not check for updates: {error}.")
            return
        skipped = self.p.cfg.updates.skipped_version
        if not update.should_offer(release.version, self.current, skipped, asked):
            logger.info(f"update check: {self.current} is up to date (latest {release.version})")
            if asked:
                self.ui.info(
                    "Check for updates",
                    f"You have the latest version ({self.current}).",
                )
            return
        logger.info(f"update check: {release.version} is available (this is {self.current})")
        self.dialog = self.ui.offer(self, release)

    # -- the dialog's buttons ------------------------------------------------------------------

    def can_install(self, release: update.Release) -> bool:
        """Whether "Update now" can replace this executable, rather than open the page."""
        return update.update_asset(release, self.exe) is not None

    def update_now(self, release: update.Release) -> None:
        if not self.can_install(release):
            self.open_page(release)
            return
        if self.p.running:
            self.p.stop()  # the app restarts; don't leave the bot pressing keys meanwhile
        self.busy = True
        self._cancel.clear()
        threading.Thread(
            target=self._download_worker, args=(release,), name="wowfish-update-download",
            daemon=True,
        ).start()  # fmt: skip

    def _download_worker(self, release: update.Release) -> None:
        def progress(done: int, total: int) -> None:
            self._q.put(("progress", done, total))

        try:
            path = self._download(release, self.exe, progress, cancelled=self._cancel.is_set)
            self._q.put(("downloaded", release, path))
        except update.UpdateError as e:
            self._q.put(("failed", (release, str(e))))
        except Exception as e:
            logger.exception("update download failed")
            self._q.put(("failed", (release, str(e))))

    def _downloaded(self, release: update.Release, path: Path) -> None:
        assert self.exe is not None
        try:
            self._install(path, self.exe)
        except update.UpdateError as e:
            self._failed((release, str(e)))
            return
        logger.info(f"updated {self.exe} to {release.version}; restarting")
        try:
            self._relaunch(self.exe)
        except OSError as e:
            self.busy = False
            self._close_dialog()
            self.ui.info(
                "Update installed",
                f"Version {release.version} is installed, but it could not be started ({e}). "
                "Open the app again to use it.",
            )
        self._close_dialog()
        self._on_quit()

    def _failed(self, failure: tuple[update.Release, str]) -> None:
        release, error = failure
        self.busy = False
        self._close_dialog()
        if self._cancel.is_set():  # the user cancelled: nothing to report
            logger.info(f"update to {release.version} cancelled")
            return
        logger.warning(f"update to {release.version} failed: {error}")
        if self.ui.ask(
            "Update failed",
            f"Could not install version {release.version}: {error}.\n\n"
            "Open the download page to get it yourself?",
        ):
            self.open_page(release)

    def skip(self, release: update.Release) -> None:
        self.p.save_updates(skipped_version=release.version)
        self._close_dialog()

    def later(self) -> None:
        self._cancel.set()  # also stops a download in progress
        self.busy = False
        self._close_dialog()

    def open_page(self, release: update.Release) -> None:
        self._open_page(release.page_url)
        self._close_dialog()

    def _close_dialog(self) -> None:
        if self.dialog is not None:
            self.dialog.close()
            self.dialog = None


class _TkUi:
    """The message boxes and the dialog; replaced by a fake in the tests."""

    def __init__(self, root: Any) -> None:
        self.root = root

    def info(self, title: str, text: str) -> None:
        from tkinter import messagebox

        messagebox.showinfo(title, text, parent=self.root)

    def error(self, title: str, text: str) -> None:
        from tkinter import messagebox

        messagebox.showerror(title, text, parent=self.root)

    def ask(self, title: str, text: str) -> bool:
        from tkinter import messagebox

        return bool(messagebox.askyesno(title, text, parent=self.root))

    def offer(self, updater: Updater, release: update.Release) -> UpdateDialog:
        return UpdateDialog(self.root, updater, release)


class UpdateDialog:
    """ "Version X is available": the release notes and three buttons."""

    def __init__(self, root: Any, updater: Updater, release: update.Release) -> None:
        import tkinter as tk

        import ttkbootstrap as tb

        self.updater, self.release = updater, release
        self.win = tb.Toplevel(root)
        self.win.title("Update available")
        self.win.transient(root)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", updater.later)
        body = tb.Frame(self.win, padding=20)
        body.pack(fill="both", expand=True)
        tb.Label(body, text=f"Version {release.version} is available", style="Brand.TLabel").pack(
            anchor="w"
        )
        tb.Label(body, text=f"You have {updater.current}.", style="Muted.TLabel").pack(
            anchor="w", pady=(2, 10)
        )
        notes = release.notes.strip()
        if notes:
            if len(notes) > NOTES_MAX_CHARS:
                notes = notes[:NOTES_MAX_CHARS].rstrip() + "\n…"
            text = tk.Text(
                body, width=64, height=12, wrap="word", relief="flat", padx=8, pady=6,
                background=theme.CARD, foreground=theme.FG, borderwidth=0,
            )  # fmt: skip
            text.insert("1.0", notes)
            text.configure(state="disabled")
            text.pack(fill="both", pady=(0, 10))
        self.bar = tb.Progressbar(body, mode="determinate", maximum=100, length=420)
        self.status = tb.Label(body, text="", style="Muted.TLabel")
        self.status.pack(anchor="w")
        buttons = tb.Frame(body)
        buttons.pack(fill="x", pady=(12, 0))
        installs = updater.can_install(release)
        self.update_button = tb.Button(
            buttons,
            text="Update now" if installs else "Open download page",
            command=self._update_now,
            bootstyle="primary",
        )
        self.update_button.pack(side="right")
        self.later_button = tb.Button(
            buttons, text="Later", command=updater.later, bootstyle="secondary"
        )
        self.later_button.pack(side="right", padx=(0, 8))
        self.skip_button = tb.Button(
            buttons,
            text="Skip this version",
            command=lambda: updater.skip(release),
            bootstyle="secondary-link",
        )
        self.skip_button.pack(side="left")
        if not installs:
            self.status.configure(text="Download the new file and replace this one with it.")
        self.win.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - self.win.winfo_width()) // 2
        y = root.winfo_rooty() + (root.winfo_height() - self.win.winfo_height()) // 3
        self.win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.win.grab_set()
        self.win.focus_force()

    def _update_now(self) -> None:
        if not self.updater.can_install(self.release):
            self.updater.open_page(self.release)
            return
        self.update_button.configure(state="disabled", text="Updating…")
        self.skip_button.configure(state="disabled")
        self.later_button.configure(text="Cancel")
        self.bar.pack(fill="x", before=self.status, pady=(0, 4))
        self.status.configure(text="Downloading…")
        self.updater.update_now(self.release)

    def progress(self, done: int, total: int) -> None:
        mb = done / 1_000_000
        if total:
            self.bar.configure(value=100 * done / total)
            self.status.configure(text=f"Downloading… {mb:.1f} of {total / 1_000_000:.1f} MB")
        else:
            self.status.configure(text=f"Downloading… {mb:.1f} MB")

    def close(self) -> None:
        try:
            self.win.grab_release()
            self.win.destroy()
        except Exception:  # already gone with the main window
            pass
