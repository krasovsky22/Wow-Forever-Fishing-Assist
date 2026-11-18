"""Updates from GitHub Releases: find the newest published release, and for the executables
replace the running file with the new one.

The flow, used by the GUI and `wowfish update`:

1. `fetch_latest()` asks the GitHub API for the newest published release (drafts and
   pre-releases are never returned), and `is_newer()` compares it with this version.
2. `download_update()` downloads the release file built for this platform next to the running
   executable as `<name>.new` and checks it against the release's SHA256SUMS.txt.
3. `install_update()` swaps it in. Windows will not overwrite or delete a running .exe but does
   let it be renamed, so the running file becomes `<name>.old` first. `relaunch()` starts the
   new file, and `remove_leftovers()` deletes the `.old` file on the next start.

Anything that goes wrong raises UpdateError; the caller then offers the release page instead.
Running from source (not a PyInstaller executable) only ever offers the page.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = "krasovsky22/Wow-Forever-Fishing-Assist"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
SUMS_NAME = "SHA256SUMS.txt"
CHECK_TIMEOUT_S = 5.0
DOWNLOAD_TIMEOUT_S = 30.0
_CHUNK = 256 * 1024
_MAX_SUMS_BYTES = 64 * 1024

Opener = Callable[..., Any]  # urllib.request.urlopen, or a fake in the tests
Progress = Callable[[int, int], None]  # (bytes done, total bytes or 0 when unknown)


class UpdateError(Exception):
    """An update could not be checked, downloaded or installed. The message is for people."""


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int = 0


@dataclass(frozen=True)
class Release:
    version: str  # "1.1.0", from the tag "v1.1.0"
    page_url: str
    notes: str = ""
    assets: tuple[Asset, ...] = ()

    def asset(self, name: str) -> Asset | None:
        return next((a for a in self.assets if a.name == name), None)


# --- versions -------------------------------------------------------------------------------

_VERSION_RE = re.compile(
    r"^v?(?P<release>\d+(?:\.\d+)*)"
    r"(?:(?P<pre>a|b|rc)(?P<pre_n>\d+))?"
    r"(?:\.post(?P<post>\d+))?"
    r"(?:\.dev(?P<dev>\d+))?"
    r"(?:\+[\w.]+)?$"
)
_PRE_RANK = {"a": 0, "b": 1, "rc": 2}


def version_key(version: str) -> tuple | None:
    """A sort key that orders versions as PEP 440 does for the forms hatch-vcs produces
    (1.0.0, 1.1.0rc1, 1.0.1.dev3+g1a2b3c4, 1.0.0.post1), or None for anything else."""
    m = _VERSION_RE.match(version.strip())
    if not m:
        return None
    release = tuple(int(p) for p in m["release"].split("."))
    release += (0,) * (3 - len(release))
    if m["pre"]:
        pre = (_PRE_RANK[m["pre"]], int(m["pre_n"]))
    elif m["dev"] is not None and m["post"] is None:
        pre = (-1, 0)  # 1.0.0.dev3 comes before 1.0.0a1
    else:
        pre = (3, 0)  # a final release
    post = int(m["post"]) if m["post"] is not None else -1
    dev = (0, int(m["dev"])) if m["dev"] is not None else (1, 0)
    return (release, pre, post, dev)


def is_newer(latest: str, current: str) -> bool:
    """Whether `latest` is a newer version than `current`. False when either is not a version
    (e.g. "0.0.0+unknown" from a source tree without git tags is still a version, the oldest)."""
    a, b = version_key(latest), version_key(current)
    return a is not None and b is not None and a > b


def should_offer(latest: str, current: str, skipped: str = "", asked: bool = False) -> bool:
    """Whether to offer `latest`: it must be newer than this version, and, unless the user
    asked (Check for updates), newer than the version they chose to skip."""
    if not is_newer(latest, current):
        return False
    return asked or not skipped or is_newer(latest, skipped)


# --- the release ----------------------------------------------------------------------------


def fetch_latest(opener: Opener | None = None, timeout: float = CHECK_TIMEOUT_S) -> Release:
    """The newest published release. Raises UpdateError when GitHub cannot be reached or has
    no published release yet."""
    import urllib.error

    try:
        with _open(API_LATEST, opener, timeout, accept="application/vnd.github+json") as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("no release has been published yet") from e
        raise UpdateError(f"GitHub answered {e.code}") from e
    except (OSError, ValueError) as e:  # URLError, timeouts and bad JSON included
        raise UpdateError(f"could not reach GitHub ({_reason(e)})") from e
    return parse_release(data)


def parse_release(data: dict[str, Any]) -> Release:
    tag = str(data.get("tag_name") or "")
    version = tag[1:] if tag.startswith("v") else tag
    if version_key(version) is None:
        raise UpdateError(f"the latest release has an unexpected tag {tag!r}")
    assets = tuple(
        Asset(str(a["name"]), str(a["browser_download_url"]), int(a.get("size") or 0))
        for a in data.get("assets") or ()
        if a.get("name") and a.get("browser_download_url")
    )
    page = str(data.get("html_url") or RELEASES_PAGE)
    return Release(version, page, str(data.get("body") or ""), assets)


# --- this executable ------------------------------------------------------------------------


def running_executable() -> Path | None:
    """The PyInstaller executable this process runs from, or None when running from source
    (only an executable can replace itself)."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve()


def platform_target(system: str | None = None, machine: str | None = None) -> str | None:
    """The release file suffix for this machine, as release.yml names them."""
    system = system or sys.platform
    machine = (machine or platform.machine()).lower()
    if system == "win32" and machine in ("amd64", "x86_64"):
        return "windows-x86_64"
    if system == "darwin" and machine == "arm64":
        return "macos-arm64"
    if system.startswith("linux") and machine in ("x86_64", "amd64"):
        return "linux-x86_64"
    return None


def asset_name(exe: Path, version: str, target: str, windows: bool | None = None) -> str:
    """The release file that replaces `exe`: the GUI for wowfish-gui, else the command line."""
    windows = sys.platform == "win32" if windows is None else windows
    program = "wowfish-gui" if exe.name.lower().startswith("wowfish-gui") else "wowfish"
    return f"{program}-{version}-{target}" + (".exe" if windows else "")


def update_asset(release: Release, exe: Path | None) -> Asset | None:
    """The file of `release` that can replace `exe`, or None (running from source, another
    platform, or the release lacks it)."""
    target = platform_target()
    if exe is None or target is None:
        return None
    return release.asset(asset_name(exe, release.version, target))


# --- download, check, install ---------------------------------------------------------------


def parse_sums(text: str) -> dict[str, str]:
    """`sha256sum` output: "<hex>  <name>" (or "<hex> *<name>") per line → {name: hex}."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            sums[parts[1].lstrip("*").strip()] = parts[0].lower()
    return sums


def download_update(
    release: Release,
    exe: Path,
    progress: Progress | None = None,
    opener: Opener | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Download the release file for `exe` next to it as `<exe>.new`, check its SHA-256
    against the release's SHA256SUMS.txt, and return its path. Leaves nothing behind when it
    fails or is cancelled."""
    asset = update_asset(release, exe)
    if asset is None:
        raise UpdateError(f"release {release.version} has no file for this computer")
    sums_asset = release.asset(SUMS_NAME)
    if sums_asset is None:
        raise UpdateError(f"release {release.version} has no {SUMS_NAME} to check the file with")
    try:
        with _open(sums_asset.url, opener, DOWNLOAD_TIMEOUT_S) as resp:
            sums = parse_sums(resp.read(_MAX_SUMS_BYTES).decode("utf-8", errors="replace"))
    except OSError as e:
        raise UpdateError(f"could not download {SUMS_NAME} ({_reason(e)})") from e
    expected = sums.get(asset.name)
    if expected is None:
        raise UpdateError(f"{SUMS_NAME} does not list {asset.name}")

    new = exe.with_name(exe.name + ".new")
    digest = hashlib.sha256()
    try:
        out = open(new, "wb")  # noqa: SIM115 - closed below, and removed on any failure
    except OSError as e:
        raise UpdateError(f"cannot write to {exe.parent} ({_reason(e)})") from e
    try:
        with out, _open(asset.url, opener, DOWNLOAD_TIMEOUT_S) as resp:
            total = int(resp.headers.get("Content-Length") or asset.size or 0)
            done = 0
            while chunk := resp.read(_CHUNK):
                if cancelled is not None and cancelled():
                    raise UpdateError("the download was cancelled")
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        if total and done != total:
            raise UpdateError(f"the download stopped early ({done} of {total} bytes)")
        if digest.hexdigest() != expected:
            raise UpdateError(f"{asset.name} does not match its checksum, so it was not used")
    except BaseException as e:
        _unlink(new)
        if isinstance(e, OSError):
            raise UpdateError(f"the download failed ({_reason(e)})") from e
        raise
    if sys.platform != "win32":
        new.chmod(new.stat().st_mode | 0o755)
    return new


def install_update(new: Path, exe: Path, windows: bool | None = None) -> None:
    """Put the downloaded `new` file in place of `exe`. On Windows the running file is renamed
    to `<exe>.old` first (it cannot be overwritten); if the swap fails it is put back."""
    windows = sys.platform == "win32" if windows is None else windows
    try:
        if not windows:  # a running file can be replaced directly
            os.replace(new, exe)
            return
        old = _old_path(exe)
        _unlink(old)
        os.replace(exe, old)
        try:
            os.replace(new, exe)
        except OSError:
            os.replace(old, exe)
            raise
    except OSError as e:
        _unlink(new)
        raise UpdateError(f"could not replace {exe.name} ({_reason(e)})") from e


def relaunch(exe: Path, args: list[str] | None = None) -> None:
    """Start the updated executable. The caller then exits."""
    env = dict(os.environ)
    # The new process is a fresh PyInstaller app, not a child sharing this one's unpacked files.
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(exe), *(args or [])], env=env, cwd=str(exe.parent), close_fds=True,
        creationflags=flags,
    )  # fmt: skip


def remove_leftovers(exe: Path | None = None, tries: int = 10, wait_s: float = 1.0) -> bool:
    """Delete `<exe>.old` and `<exe>.new` left by an update. The old process may still be
    closing when the new one starts, so keep trying for a few seconds. True when none is left."""
    exe = exe or running_executable()
    if exe is None:
        return True
    paths = [_old_path(exe), exe.with_name(exe.name + ".new")]
    for i in range(tries):
        for p in paths:
            _unlink(p)
        if not any(p.exists() for p in paths):
            return True
        if i + 1 < tries:
            time.sleep(wait_s)
    return False


# --- helpers --------------------------------------------------------------------------------


def _open(url: str, opener: Opener | None, timeout: float, accept: str | None = None) -> Any:
    import urllib.request

    from wowfish import __version__

    headers = {"User-Agent": f"wowfish/{__version__}"}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    if opener is not None:
        return opener(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


# Where macOS and the common Linux distributions keep their CA bundle.
_CA_FILES = (
    "/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",
)


def _ssl_context() -> Any:
    """The default TLS context, plus the system's CA file when the bundled OpenSSL found no
    certificates (a frozen Python can look for them in its build machine's paths)."""
    import ssl

    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) == 0:
        for path in _CA_FILES:
            if os.path.isfile(path):
                with contextlib.suppress(ssl.SSLError, OSError):
                    ctx.load_verify_locations(cafile=path)
                    break
    return ctx


def _old_path(exe: Path) -> Path:
    return exe.with_name(exe.name + ".old")


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def _reason(e: BaseException) -> str:
    return str(getattr(e, "reason", None) or e) or type(e).__name__
