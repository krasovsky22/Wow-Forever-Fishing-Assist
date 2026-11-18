"""Tests for wowfish.update and the GUI's Updater (no network, no Tk)."""

from __future__ import annotations

import hashlib
import io
import json
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from wowfish import update as up
from wowfish.config import UpdatesConfig, add_missing_settings, load_config

# --- versions -------------------------------------------------------------------------------


def test_versions_order_as_pep440():
    ordered = [
        "0.0.0+unknown",
        "0.9.9",
        "1.0.0.dev3+g1a2b3c4",
        "1.0.0a1",
        "1.0.0b2",
        "1.0.0rc1",
        "1.0.0",
        "v1.0.0.post1",
        "1.0.1.dev1",
        "1.0.1",
        "1.1",
        "1.10.0",
        "2.0.0",
    ]
    keys = [up.version_key(v) for v in ordered]
    assert None not in keys
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)


def test_is_newer():
    assert up.is_newer("1.1.0", "1.0.0")
    assert up.is_newer("1.0.0", "0.0.0+unknown")
    assert not up.is_newer("1.0.0", "1.0.0")
    assert not up.is_newer("1.0.0", "1.0.1.dev2+gabc")  # a dev build past the release
    assert not up.is_newer("nonsense", "1.0.0")
    assert not up.is_newer("1.1.0", "nonsense")


def test_should_offer_respects_a_skipped_version_unless_asked():
    assert up.should_offer("1.1.0", "1.0.0")
    assert not up.should_offer("1.1.0", "1.0.0", skipped="1.1.0")
    assert up.should_offer("1.1.0", "1.0.0", skipped="1.1.0", asked=True)
    assert up.should_offer("1.2.0", "1.0.0", skipped="1.1.0")
    assert not up.should_offer("1.0.0", "1.0.0", asked=True)


# --- releases and assets --------------------------------------------------------------------

BINARY = b"new wowfish build " * 5000
NAME = "wowfish-gui-1.1.0-windows-x86_64.exe"


def _release_json(sums: str | None = None) -> dict:
    sums = sums if sums is not None else f"{hashlib.sha256(BINARY).hexdigest()}  {NAME}\n"
    return {
        "tag_name": "v1.1.0",
        "html_url": "https://github.com/x/y/releases/tag/v1.1.0",
        "body": "What's new",
        "assets": [
            {"name": NAME, "browser_download_url": "https://dl/bin", "size": len(BINARY)},
            {"name": "SHA256SUMS.txt", "browser_download_url": "https://dl/sums", "size": 99},
        ],
        "_sums": sums,
    }


class _Resp(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def _opener(routes: dict):
    calls = []

    def open_(req, timeout):
        url = req.full_url
        calls.append((url, dict(req.header_items())))
        value = routes[url]
        if isinstance(value, BaseException):
            raise value
        return _Resp(value)

    open_.calls = calls
    return open_


def _routes(data: dict, binary: bytes = BINARY) -> dict:
    return {
        up.API_LATEST: json.dumps(data).encode(),
        "https://dl/bin": binary,
        "https://dl/sums": data["_sums"].encode(),
    }


def test_fetch_latest_parses_the_release():
    opener = _opener(_routes(_release_json()))
    release = up.fetch_latest(opener)
    assert release.version == "1.1.0"
    assert release.notes == "What's new"
    assert release.asset(NAME).size == len(BINARY)
    headers = opener.calls[0][1]
    assert headers["User-agent"].startswith("wowfish/")


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (urllib.error.HTTPError(up.API_LATEST, 404, "nf", {}, None), "no release"),
        (urllib.error.HTTPError(up.API_LATEST, 403, "rate", {}, None), "403"),
        (urllib.error.URLError("no route"), "could not reach GitHub"),
        (TimeoutError("timed out"), "could not reach GitHub"),
    ],
)
def test_fetch_latest_errors_become_update_errors(error, message):
    with pytest.raises(up.UpdateError, match=message):
        up.fetch_latest(_opener({up.API_LATEST: error}))


def test_fetch_latest_rejects_a_strange_tag():
    data = _release_json() | {"tag_name": "latest"}
    with pytest.raises(up.UpdateError, match="unexpected tag"):
        up.fetch_latest(_opener(_routes(data)))


def test_platform_target_and_asset_names():
    assert up.platform_target("win32", "AMD64") == "windows-x86_64"
    assert up.platform_target("darwin", "arm64") == "macos-arm64"
    assert up.platform_target("linux", "x86_64") == "linux-x86_64"
    assert up.platform_target("darwin", "x86_64") is None
    assert up.platform_target("win32", "ARM64") is None
    gui = Path("C:/Games/wowfish-gui-1.0.0-windows-x86_64.exe")
    assert up.asset_name(gui, "1.1.0", "windows-x86_64", windows=True) == NAME
    cli = Path("/home/me/wowfish")
    name = up.asset_name(cli, "1.1.0", "linux-x86_64", windows=False)
    assert name == "wowfish-1.1.0-linux-x86_64"


def test_parse_sums():
    a, b = "a" * 64, "B" * 64
    sums = up.parse_sums(f"{a}  one.exe\n{b} *two\nnot a line\n\n")
    assert sums == {"one.exe": a, "two": "b" * 64}


# --- download and install -------------------------------------------------------------------


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(up, "platform_target", lambda *a, **k: "windows-x86_64")
    monkeypatch.setattr(up.sys, "platform", "win32")


def _exe(tmp_path: Path) -> Path:
    exe = tmp_path / "wowfish-gui.exe"
    exe.write_bytes(b"old build")
    return exe


def test_download_update_checks_the_file(tmp_path, on_windows):
    exe = _exe(tmp_path)
    data = _release_json()
    release = up.parse_release(data)
    seen = []
    new = up.download_update(release, exe, lambda d, t: seen.append((d, t)), _opener(_routes(data)))
    assert new == tmp_path / "wowfish-gui.exe.new"
    assert new.read_bytes() == BINARY
    assert seen[-1] == (len(BINARY), len(BINARY))
    assert exe.read_bytes() == b"old build"


def test_download_update_refuses_a_file_that_does_not_match(tmp_path, on_windows):
    exe = _exe(tmp_path)
    data = _release_json()
    release = up.parse_release(data)
    with pytest.raises(up.UpdateError, match="checksum"):
        up.download_update(release, exe, opener=_opener(_routes(data, binary=b"tampered")))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["wowfish-gui.exe"]


def test_download_update_needs_the_file_listed_in_the_sums(tmp_path, on_windows):
    data = _release_json(sums=f"{'0' * 64}  something-else\n")
    with pytest.raises(up.UpdateError, match="does not list"):
        up.download_update(up.parse_release(data), _exe(tmp_path), opener=_opener(_routes(data)))


def test_download_update_without_a_file_for_this_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "platform_target", lambda *a, **k: "macos-arm64")
    data = _release_json()
    with pytest.raises(up.UpdateError, match="no file for this computer"):
        up.download_update(up.parse_release(data), _exe(tmp_path), opener=_opener(_routes(data)))


def test_download_update_can_be_cancelled(tmp_path, on_windows):
    exe = _exe(tmp_path)
    data = _release_json()
    with pytest.raises(up.UpdateError, match="cancelled"):
        up.download_update(
            up.parse_release(data), exe, opener=_opener(_routes(data)), cancelled=lambda: True
        )
    assert not (tmp_path / "wowfish-gui.exe.new").exists()


def test_download_update_into_a_folder_it_cannot_write(tmp_path, on_windows):
    exe = tmp_path / "gone" / "wowfish-gui.exe"  # the folder does not exist: open() fails
    data = _release_json()
    with pytest.raises(up.UpdateError, match="cannot write"):
        up.download_update(up.parse_release(data), exe, opener=_opener(_routes(data)))


def test_install_update_on_windows_keeps_the_running_file_as_old(tmp_path):
    exe = _exe(tmp_path)
    new = tmp_path / "wowfish-gui.exe.new"
    new.write_bytes(b"new build")
    up.install_update(new, exe, windows=True)
    assert exe.read_bytes() == b"new build"
    assert (tmp_path / "wowfish-gui.exe.old").read_bytes() == b"old build"
    assert not new.exists()
    assert up.remove_leftovers(exe, tries=1)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["wowfish-gui.exe"]


def test_install_update_puts_the_old_file_back_when_the_swap_fails(tmp_path):
    exe = _exe(tmp_path)
    with pytest.raises(up.UpdateError, match="could not replace"):
        up.install_update(tmp_path / "missing.new", exe, windows=True)
    assert exe.read_bytes() == b"old build"
    assert not (tmp_path / "wowfish-gui.exe.old").exists()


def test_install_update_elsewhere_replaces_in_place(tmp_path):
    exe = tmp_path / "wowfish"
    exe.write_bytes(b"old build")
    new = tmp_path / "wowfish.new"
    new.write_bytes(b"new build")
    up.install_update(new, exe, windows=False)
    assert exe.read_bytes() == b"new build"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["wowfish"]


def test_running_from_source_never_self_updates():
    assert up.running_executable() is None  # pytest is not a PyInstaller executable
    assert up.update_asset(up.parse_release(_release_json()), None) is None


# --- config ---------------------------------------------------------------------------------


def test_updates_config_defaults_and_is_added_to_old_files(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[game]\ncast_key = "2"\n', encoding="utf-8")
    assert load_config(path)[0].updates == UpdatesConfig(check=True, skipped_version="")
    added = add_missing_settings(path)
    assert "[updates]" in added
    cfg, warnings = load_config(path)
    assert cfg.updates.check is True and cfg.game.cast_key == "2" and not warnings


# --- the GUI's Updater ----------------------------------------------------------------------


class _Ui:
    def __init__(self, answer: bool = False) -> None:
        self.messages: list[tuple[str, str]] = []
        self.offered: list = []
        self.answer = answer

    def info(self, title, text):
        self.messages.append(("info", text))

    def error(self, title, text):
        self.messages.append(("error", text))

    def ask(self, title, text):
        self.messages.append(("ask", text))
        return self.answer

    def offer(self, updater, release):
        self.offered.append(release)
        return SimpleNamespace(close=lambda: None, progress=lambda d, t: None)


class _Presenter:
    def __init__(self, check: bool = True, skipped: str = "") -> None:
        self.cfg = SimpleNamespace(updates=UpdatesConfig(check=check, skipped_version=skipped))
        self.running = False
        self.saved: list[str] = []
        self.stopped = False

    def save_updates(self, check=None, skipped_version=None):
        self.saved.append(skipped_version)

    def stop(self):
        self.stopped = True


class _Root:
    def __init__(self) -> None:
        self.after_calls = []

    def after(self, ms, fn):
        self.after_calls.append((ms, fn))


def _updater(release=None, error=None, exe=None, **kw):
    from wowfish.gui.updater import Updater

    def fetch():
        if error:
            raise up.UpdateError(error)
        return release

    events = SimpleNamespace(quit=0, pages=[], installed=[], relaunched=[])
    u = Updater(
        _Root(),
        kw.pop("presenter", None) or _Presenter(),
        on_quit=lambda: setattr(events, "quit", events.quit + 1),
        fetch=fetch,
        exe=exe,
        current=kw.pop("current", "1.0.0"),
        open_page=events.pages.append,
        install=lambda new, exe_: events.installed.append((new, exe_)),
        relaunch=events.relaunched.append,
        ui=kw.pop("ui", None) or _Ui(),
        **kw,
    )
    return u, events


def _settle(u, until, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        u.poll()
        if until():
            return
        time.sleep(0.01)
    raise AssertionError("the updater never got there")


def test_updater_offers_a_newer_release():
    release = up.parse_release(_release_json())
    u, _ = _updater(release)
    u.check(asked=False)
    _settle(u, lambda: not u.busy)
    assert u.ui.offered == [release]
    assert u.dialog is not None


def test_updater_startup_check_is_quiet_when_up_to_date_or_offline():
    u, _ = _updater(up.parse_release(_release_json()), current="1.1.0")
    u.check(asked=False)
    _settle(u, lambda: not u.busy)
    u2, _ = _updater(error="could not reach GitHub (offline)")
    u2.check(asked=False)
    _settle(u2, lambda: not u2.busy)
    assert u.ui.messages == [] and u2.ui.messages == []
    assert u.dialog is None and u2.dialog is None


def test_updater_says_so_when_asked():
    u, _ = _updater(up.parse_release(_release_json()), current="1.1.0")
    u.check(asked=True)
    _settle(u, lambda: not u.busy)
    assert u.ui.messages == [("info", "You have the latest version (1.1.0).")]
    u2, _ = _updater(error="GitHub answered 403")
    u2.check(asked=True)
    _settle(u2, lambda: not u2.busy)
    assert u2.ui.messages == [("error", "Could not check for updates: GitHub answered 403.")]


def test_updater_skip_and_skipped_versions():
    release = up.parse_release(_release_json())
    u, _ = _updater(release)
    u.check(asked=False)
    _settle(u, lambda: not u.busy)
    u.skip(release)
    assert u.p.saved == ["1.1.0"] and u.dialog is None
    u2, _ = _updater(release, presenter=_Presenter(skipped="1.1.0"))
    u2.check(asked=False)
    _settle(u2, lambda: not u2.busy)
    assert u2.ui.offered == []


def test_updater_start_checks_only_from_an_executable_and_when_enabled(tmp_path):
    u, _ = _updater(exe=None)
    u.start()
    assert u.root.after_calls == []
    u2, _ = _updater(exe=_exe(tmp_path))
    u2.start()
    assert [ms for ms, _ in u2.root.after_calls] == [1500]
    u3, _ = _updater(exe=_exe(tmp_path), presenter=_Presenter(check=False))
    u3.start()
    assert u3.root.after_calls == []


def test_updater_from_source_opens_the_page():
    release = up.parse_release(_release_json())
    u, events = _updater(release, exe=None)
    assert not u.can_install(release)
    u.update_now(release)
    assert events.pages == [release.page_url]


def test_updater_update_now_downloads_installs_and_restarts(tmp_path, on_windows):
    data = _release_json()
    release = up.parse_release(data)
    exe = _exe(tmp_path)

    def download(rel, exe_, progress, cancelled):
        return up.download_update(rel, exe_, progress, _opener(_routes(data)), cancelled)

    presenter = _Presenter()
    presenter.running = True
    u, events = _updater(release, exe=exe, download=download, presenter=presenter)
    u.dialog = SimpleNamespace(close=lambda: None, progress=lambda d, t: None)
    u.update_now(release)
    _settle(u, lambda: events.quit)
    assert presenter.stopped
    assert events.installed == [(tmp_path / "wowfish-gui.exe.new", exe)]
    assert events.relaunched == [exe]
    assert u.dialog is None


def test_updater_failed_download_offers_the_page(tmp_path, on_windows):
    data = _release_json()
    release = up.parse_release(data)

    def download(rel, exe_, progress, cancelled):
        raise up.UpdateError("the download failed (reset)")

    u, events = _updater(release, exe=_exe(tmp_path), download=download, ui=_Ui(answer=True))
    u.update_now(release)
    _settle(u, lambda: not u.busy)
    assert u.ui.messages[0][0] == "ask" and "the download failed" in u.ui.messages[0][1]
    assert events.pages == [release.page_url]
    assert events.quit == 0
