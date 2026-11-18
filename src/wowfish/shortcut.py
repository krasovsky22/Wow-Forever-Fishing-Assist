"""A desktop shortcut to the running wowfish executable (Setup tab: Create desktop shortcut).

Windows gets a .lnk (written by PowerShell's WScript.Shell, so no extra dependency) with the
same name the installer uses, so it replaces the installer's shortcut instead of adding a
second one. Linux gets a .desktop launcher; macOS a symlink to the executable.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SHORTCUT_NAME = "WoW Forever Fishing Assist"  # as packaging/wowfish.iss names its shortcut
GUI_PROGRAM = "wowfish-gui"


class ShortcutError(RuntimeError):
    """A shortcut could not be made; the message is meant for the user."""


@dataclass(frozen=True)
class Target:
    """What the shortcut starts: an executable and its arguments."""

    exe: Path
    args: tuple[str, ...] = ()


def gui_target(exe: Path | None, windows: bool | None = None) -> Target | None:
    """The executable that opens the window: `exe` itself when it is wowfish-gui, else the
    wowfish-gui next to it, else `exe gui`. None when running from source (no executable)."""
    if exe is None:
        return None
    windows = sys.platform == "win32" if windows is None else windows
    if exe.name.lower().startswith(GUI_PROGRAM):
        return Target(exe)
    sibling = exe.with_name(GUI_PROGRAM + (".exe" if windows else ""))
    if sibling.is_file():
        return Target(sibling)
    return Target(exe, ("gui",))


def desktop_dir() -> Path:
    """The user's desktop folder (follows a OneDrive-moved or XDG-renamed desktop)."""
    import platformdirs

    return Path(platformdirs.user_desktop_dir())


def create(
    target: Target,
    desktop: Path | None = None,
    platform: str | None = None,
    icon: Path | None = None,
) -> Path:
    """Write (or overwrite) the shortcut on the desktop and return its path."""
    platform = platform or sys.platform
    desktop = desktop or desktop_dir()
    if not desktop.is_dir():
        raise ShortcutError(f"no desktop folder at {desktop}")
    try:
        if platform == "win32":
            return _windows(target, desktop)
        if platform == "darwin":
            return _macos(target, desktop)
        return _linux(target, desktop, icon)
    except OSError as e:
        raise ShortcutError(str(e)) from e


def _windows(target: Target, desktop: Path) -> Path:
    path = desktop / f"{SHORTCUT_NAME}.lnk"
    # Paths go in through the environment, so no quoting of them inside the script.
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:WOWFISH_LNK);"
        "$s.TargetPath = $env:WOWFISH_EXE;"
        "$s.Arguments = $env:WOWFISH_ARGS;"
        "$s.WorkingDirectory = $env:WOWFISH_DIR;"
        "$s.IconLocation = $env:WOWFISH_EXE + ',0';"
        f"$s.Description = '{SHORTCUT_NAME}';"
        "$s.Save()"
    )
    env = dict(
        os.environ,
        WOWFISH_LNK=str(path),
        WOWFISH_EXE=str(target.exe),
        WOWFISH_ARGS=subprocess.list2cmdline(target.args),
        WOWFISH_DIR=str(target.exe.parent),
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash from the GUI
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env, capture_output=True, text=True, timeout=30, creationflags=flags,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        raise ShortcutError("PowerShell did not answer in 30 s") from None
    if done.returncode != 0 or not path.exists():
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise ShortcutError(detail[-1] if detail else f"PowerShell exited with {done.returncode}")
    return path


def desktop_entry(target: Target, icon: Path | None = None) -> str:
    """A freedesktop .desktop launcher for `target`."""
    exec_line = " ".join(_desktop_quote(str(a)) for a in (target.exe, *target.args))
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={SHORTCUT_NAME}",
        "Comment=Fishing assist for WoW Forever",
        f"Exec={exec_line}",
        f"Path={target.exe.parent}",
        "Terminal=false",
        "Categories=Game;",
    ]
    if icon is not None:
        lines.append(f"Icon={icon}")
    return "\n".join(lines) + "\n"


def _desktop_quote(arg: str) -> str:
    """Quote an Exec argument as the Desktop Entry spec asks (and escape % field codes)."""
    arg = arg.replace("%", "%%")
    if not any(c in arg for c in " \t\n\"'\\><~|&;$*?#()`"):
        return arg
    for c in ("\\", '"', "`", "$"):
        arg = arg.replace(c, "\\" + c)
    return f'"{arg}"'.replace("\\", "\\\\")


def _linux(target: Target, desktop: Path, icon: Path | None) -> Path:
    path = desktop / "wowfish.desktop"
    path.write_text(desktop_entry(target, icon), encoding="utf-8")
    path.chmod(0o755)  # desktops only launch executable .desktop files
    _trust_on_gnome(path)
    return path


def _trust_on_gnome(path: Path) -> None:
    """GNOME also wants the launcher marked trusted; best effort, ignored elsewhere."""
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            ["gio", "set", str(path), "metadata::trusted", "true"],
            capture_output=True, timeout=5, check=False,
        )  # fmt: skip


def _macos(target: Target, desktop: Path) -> Path:
    path = desktop / SHORTCUT_NAME
    if path.is_symlink() or path.exists():
        if not path.is_symlink():
            raise ShortcutError(f"{path} already exists and is not a shortcut")
        path.unlink()
    path.symlink_to(target.exe)
    return path


def saved_icon(data_dir: Path) -> Path | None:
    """Copy the app icon out of the bundle (a single-file build unpacks it to a temp folder
    that is gone after exit) so a Linux launcher can show it; None if it cannot."""
    from importlib import resources

    try:
        png = resources.files("wowfish.assets").joinpath("icon.png").read_bytes()
        icon = data_dir / "icon.png"
        if not icon.exists() or icon.read_bytes() != png:
            icon.write_bytes(png)
        return icon
    except OSError:
        return None
