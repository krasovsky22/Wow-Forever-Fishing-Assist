"""Bring the game window to the front and give it keyboard focus, so a Start in the GUI can
hand the screen straight to the game.

Best effort everywhere: `focus_window` returns whether the platform said yes and never raises.
The engine still pauses on its own while the game lacks focus, so a refusal only means the
player clicks into the game themselves.
"""

from __future__ import annotations

import sys
from typing import Any

from loguru import logger

from wowfish.capture import WindowRef, linux_hyprland, linux_x11, macos


def focus_window(window: WindowRef | None) -> bool:
    if window is None:
        return False
    try:
        if window.compositor == "hyprland":
            return _focus_hyprland(window)
        if linux_x11() and window.handle is not None:
            return _focus_x11(window.handle)
        if sys.platform == "win32" and window.handle is not None:
            return _focus_win32(window.handle)
        if macos() and window.pid is not None:
            return _focus_macos(window.pid)
        if linux_hyprland():
            return _focus_hyprland(window)
    except Exception as e:  # a refused or unsupported request; the player can click instead
        logger.debug(f"could not focus {window.title!r}: {type(e).__name__}: {e}")
    return False


# -- Windows ------------------------------------------------------------------------------------

SW_RESTORE = 9


def _focus_win32(hwnd: int) -> bool:
    """Restore the window if minimized and make it the foreground window.

    Windows only lets the foreground process hand the foreground on; a Start click makes
    wowfish that process. AttachThreadInput covers the case where it no longer is (e.g. the
    console `run`), by sharing input state with the current foreground thread for the call.
    """
    import ctypes
    from ctypes import wintypes

    user32: Any = ctypes.WinDLL("user32", use_last_error=True)
    kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    for name in ("IsIconic", "SetForegroundWindow", "BringWindowToTop"):
        getattr(user32, name).argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

    target = wintypes.HWND(hwnd)
    if user32.IsIconic(target):
        user32.ShowWindow(target, SW_RESTORE)
    fg = user32.GetForegroundWindow()
    ours = kernel32.GetCurrentThreadId()
    theirs = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(theirs and theirs != ours and user32.AttachThreadInput(ours, theirs, True))
    try:
        user32.BringWindowToTop(target)
        user32.SetForegroundWindow(target)
    finally:
        if attached:
            user32.AttachThreadInput(ours, theirs, False)
    return (user32.GetForegroundWindow() or 0) == hwnd


# -- Linux --------------------------------------------------------------------------------------


def _focus_x11(handle: int) -> bool:
    """Ask the window manager to activate the window (EWMH _NET_ACTIVE_WINDOW, source 2 =
    a pager/tool acting for the user, which window managers honour without focus stealing
    prevention)."""
    from Xlib import X, display, protocol

    d = display.Display()
    try:
        root = d.screen().root
        win = d.create_resource_object("window", handle)
        event = protocol.event.ClientMessage(
            window=win,
            client_type=d.intern_atom("_NET_ACTIVE_WINDOW"),
            data=(32, [2, X.CurrentTime, 0, 0, 0]),
        )
        root.send_event(event, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        win.raise_window()
        d.sync()
        return True
    finally:
        d.close()


def _focus_hyprland(window: WindowRef) -> bool:
    from wowfish.capture._wayland import hyprland_dispatch

    if window.compositor == "hyprland" and window.handle is not None:
        target = f"address:0x{window.handle:x}"
    elif window.pid is not None:
        target = f"pid:{window.pid}"
    else:
        return False
    return hyprland_dispatch(f"focuswindow {target}") == "ok"


# -- macOS --------------------------------------------------------------------------------------

ACTIVATE_IGNORING_OTHER_APPS = 1 << 1  # NSApplicationActivateIgnoringOtherApps


def _focus_macos(pid: int) -> bool:
    """[[NSRunningApplication runningApplicationWithProcessIdentifier:pid]
    activateWithOptions:NSApplicationActivateIgnoringOtherApps], through the ObjC runtime."""
    import ctypes

    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")  # registers the class
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    send = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    by_pid = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)(send)
    activate = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(
        send
    )
    cls = objc.objc_getClass(b"NSRunningApplication")
    app = by_pid(cls, objc.sel_registerName(b"runningApplicationWithProcessIdentifier:"), pid)
    if not app:
        return False
    return bool(
        activate(app, objc.sel_registerName(b"activateWithOptions:"), ACTIVATE_IGNORING_OTHER_APPS)
    )
