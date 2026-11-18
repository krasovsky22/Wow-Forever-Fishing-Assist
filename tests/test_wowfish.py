"""
Everything here runs on bundled files and synthetic signals. No screen, audio device, window
system, or Tk is touched, so a failure means the code or the assets changed, not the machine.
"""

from __future__ import annotations

import itertools
import random
import subprocess
import sys
import tomllib
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import tomlkit
from loguru import logger
from typer.testing import CliRunner

from wowfish import capture
from wowfish.assets import CLIP_NAMES, ICON_SIZES, default_config_path
from wowfish.audio import AudioChunk, devices
from wowfish.audio.clips import build_fingerprints, load_reference_clips
from wowfish.audio.detector import BiteDetector, BiteEvent, detector_for
from wowfish.audio.features import DEFAULT_MEL
from wowfish.capture import WindowRef, _quartz, _wayland, _x11
from wowfish.capture._quartz import frontmost_pid, pick_quartz_window
from wowfish.capture._wayland import pick_hyprland_window
from wowfish.cli import _setup_logging, app
from wowfish.config import (
    AudioConfig,
    BehaviorConfig,
    Config,
    GameConfig,
    RunConfig,
    VisionConfig,
    ensure_config_file,
    load_config,
    save_config,
)
from wowfish.engine import events as ev
from wowfish.engine.engine import FishingEngine, unsupported_platform
from wowfish.engine.state import State
from wowfish.errors import PermissionMissing
from wowfish.gui.presenter import Presenter, describe
from wowfish.input import _quartz as input_quartz
from wowfish.input import get_input_device
from wowfish.input._wayland import keycodes_from_keymap
from wowfish.input.keys import MAC_KEYCODES, SUPPORTED_KEYS
from wowfish.vision.matcher import IconMatch, IconMatcher
from wowfish.vision.template import load_icon_templates, template_for_size

REPO_ROOT = Path(__file__).resolve().parents[1]
RATE = DEFAULT_MEL.sample_rate


def test_bundled_assets_load():
    """Every sprite and clip the code expects is shipped and readable; the release check agrees."""
    check = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_release.py")],
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr
    assert tuple(t.native_size for t in load_icon_templates()) == ICON_SIZES
    assert tuple(fp.name for fp in build_fingerprints()) == CLIP_NAMES


def test_default_config_matches_code_defaults(tmp_path):
    """The shipped default_config file and the dataclass defaults describe the same settings,
    and a config survives a save/load round trip unchanged."""
    path = tmp_path / "config.toml"
    assert ensure_config_file(path) is True
    assert ensure_config_file(path) is False  # never overwrites an existing file

    from_file, warnings = load_config(path)
    assert warnings == []
    assert from_file == Config(game=GameConfig(), vision=VisionConfig(), audio=AudioConfig())

    save_config(from_file, path)
    assert load_config(path) == (from_file, [])


def test_older_config_gains_new_settings_at_launch(tmp_path, monkeypatch):
    """A config from an older release gets every setting added since, with its comment from
    default_config, the next time wowfish starts. The user's values, comments and unknown keys
    stay, a second start leaves the file alone, a file that does not parse is left for
    load_config to report, no file is created where there was none, and nothing is written if
    the merge would change a value already there."""
    monkeypatch.setenv("WOWFISH_HOME", str(tmp_path))
    path = tmp_path / "config" / "config.toml"
    path.parent.mkdir()

    def launch() -> str:
        try:
            result = CliRunner().invoke(app, ["config", "--path"])
        finally:
            logger.remove()  # closes the log file so tmp_path can be deleted on Windows
        assert result.exit_code == 0, result.output
        return path.read_text() if path.exists() else ""

    assert launch() == ""  # no config yet: launching does not create one

    old = (
        "# my own note\n"
        "[vision]\n"
        "icon_size_px = 53\n"
        "legacy_key = 1\n"
        "\n"
        "[game]\n"
        "# Keybind for the Fishing ability on your action bar.\n"
        'cast_key = "7"'  # no newline at the end
    )
    path.write_text(old)
    merged = launch()

    expected = tomllib.loads(default_config_path().read_text())
    expected["game"]["cast_key"] = "7"
    expected["vision"].update(icon_size_px=53, legacy_key=1)
    assert tomllib.loads(merged) == expected
    for line in old.splitlines():
        assert line in merged.splitlines(), line
    assert "# Keybind of an action bar macro containing /quit." in merged
    assert "# Variations so the bot does not keep the exact same rhythm." in merged
    assert load_config(path)[1] == ["unknown key vision.legacy_key ignored"]

    assert launch() == merged  # nothing new to add: the file is not touched

    broken = "[game\ncast_key = 1\n"
    path.write_text(broken)
    assert launch() == broken

    path.write_text(old)  # if the merge ever changed a value already there, nothing is written
    monkeypatch.setattr(tomlkit, "dumps", lambda doc: 'cast_key = "7"\n')
    assert launch() == old


def test_logging_works_without_stderr(tmp_path, monkeypatch):
    """wowfish-gui.exe is built without a console, so on Windows Python sets sys.stderr to None.
    Logging setup must not crash there, and the log file must still be written."""
    monkeypatch.setenv("WOWFISH_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stderr", None)
    try:
        log_path = _setup_logging()
        logger.info("started without a console")
    finally:
        logger.remove()  # closes the file sink so tmp_path can be deleted on Windows
    assert "started without a console" in log_path.read_text()


def _hear(stream: np.ndarray) -> tuple[list[BiteEvent], BiteDetector]:
    """Run a stream through the detector the bot builds from the default config, in modest
    chunks like the live loop does."""
    det = detector_for(AudioConfig())
    events: list[BiteEvent] = []
    chunk = 2048
    for i in range(0, len(stream), chunk):
        part = stream[i : i + chunk]
        events += det.feed(AudioChunk(part, RATE, (i + len(part)) / RATE))
    return events, det


def test_bite_detector_hears_each_clip_once():
    """Each of the four bundled splashes, padded with silence, yields exactly one bite naming
    that clip, above the shipped threshold, timed inside the clip, and the "best match" the
    status line reports and the peak the Scores graph plots are that same peak. Two splashes
    well apart yield two."""
    threshold = AudioConfig().bite_threshold
    gap = np.zeros(RATE, dtype=np.float32)  # 1 s
    for name, clip in load_reference_clips(RATE).items():
        events, det = _hear(np.concatenate([gap, clip, gap]))
        assert [e.clip for e in events] == [name], name
        assert events[0].score >= threshold, (name, events[0].score)
        assert 1.0 - 0.1 <= events[0].t <= 1.0 + len(clip) / RATE, (name, events[0].t)
        best = det.best_heard
        assert (best.name, best.score) == (name, events[0].score), (name, best)
        assert det.take_peaks()[name] == events[0].score, name  # what the Scores graph plots
        assert det.take_peaks()[name] == -1.0, name  # taking the peaks clears them

    clip = load_reference_clips(RATE)[CLIP_NAMES[0]]
    apart = np.zeros(4 * RATE, dtype=np.float32)  # longer than the default refractory period
    assert len(_hear(np.concatenate([gap, clip, apart, clip, gap]))[0]) == 2


def test_bite_detector_stays_quiet_on_silence_and_noise():
    """Nothing fires on silence or broadband noise, and the scores stay well under threshold."""
    threshold = AudioConfig().bite_threshold
    rng = np.random.default_rng(0)
    silence = np.zeros(3 * RATE, dtype=np.float32)
    noise = (rng.standard_normal(3 * RATE) * 0.1).astype(np.float32)
    for stream in (silence, noise):
        det = detector_for(AudioConfig())
        events: list[BiteEvent] = []
        for i in range(0, len(stream), 2048):
            part = stream[i : i + 2048]
            events += det.feed(AudioChunk(part, RATE, (i + len(part)) / RATE))
        assert events == []
        assert max(s.score for s in det.last_scores) < threshold - 0.2


def test_icon_matcher_finds_sprite_and_rejects_water():
    """Every sprite pasted onto noisy blue water is found at the right spot, at 1080p and on a
    4K-wide frame that the matcher downscales. The same water without a sprite is rejected."""
    threshold = VisionConfig().icon_threshold
    templates = load_icon_templates()
    rng = np.random.default_rng(0)
    for frame_w, frame_h in ((1920, 1080), (3840, 2160)):
        water = np.full((frame_h, frame_w, 3), (90, 60, 30), np.uint8)  # BGR: bluish
        water += rng.integers(0, 20, water.shape, dtype=np.uint8)
        for size in ICON_SIZES:
            t = template_for_size(templates, size)
            x, y = frame_w // 3, frame_h // 3
            frame = water.copy()
            frame[y : y + size, x : x + size][t.mask > 0] = t.bgr[t.mask > 0]

            hit = IconMatcher(t).match(frame)
            assert hit.accepted(threshold), (frame_w, size, hit)
            assert abs(hit.x - (x + size / 2)) <= 2 and abs(hit.y - (y + size / 2)) <= 2

            miss = IconMatcher(t).match(water)
            assert not miss.accepted(threshold), (frame_w, size, miss)


KEYMAP = """xkb_keymap {
xkb_keycodes "evdev" {
	minimum = 8;
	maximum = 255;
	<AE01> = 10;
	<AC04> = 41;
	<RTRN> = 36;
	<FK01> = 67;
	alias <LatF> = <AC04>;
};
xkb_types "complete" { type "ONE_LEVEL" { modifiers= none; }; };
xkb_symbols "pc+fr" {
%s};
};
"""
NAMED_SYMBOLS = """\tkey <AE01> {\t[ ampersand, 1 ] };
\tkey <LatF> {\t[ f, F ] };
\tkey <RTRN> {\t[ Return ] };
\tkey <FK01> {
\t\ttype= "CTRL+ALT",
\t\tsymbols[Group1]= [ F1, F1, F1, F1, XF86Switch_VT_1 ]
\t};
"""
HEX_SYMBOLS = """\tkey <AE01> {\t[ 0x26, 0x31 ] };
\tkey <LatF> {\t[ 0x66, 0x46 ] };
\tkey <RTRN> {\t[ 0xff0d ] };
\tkey <FK01> {
\t\ttype= "CTRL+ALT",
\t\tsymbols[1]= [ 0xffbe, 0xffbe, 0xffbe, 0xffbe, 0x1008fe01 ]
\t};
"""


def test_wayland_window_and_keys_from_compositor_replies(monkeypatch):
    """Hyprland reports logical geometry for native and Xwayland windows alike; the window comes
    back sized in its output's pixels, and the largest title match wins whichever kind it is. An
    Xwayland winner is then looked up through X11 by its pid. Each key resolves to its evdev
    keycode (xkb code - 8) whether the keymap names keysyms (xkbcommon) or writes them in hex
    (Hyprland), through aliases and from a shifted level (the digits on AZERTY)."""

    def client(address, title, at, size, monitor, pid=42, xwayland=False):
        return {"address": address, "title": title, "pid": pid, "at": at, "size": size,
                "monitor": monitor, "mapped": True, "hidden": False,
                "xwayland": xwayland}  # fmt: skip

    clients = [
        client("0x1f", "World of Warcraft - Wowhead", [0, 0], [800, 600], 0),
        client("0x2a", "World of Warcraft", [2560, 0], [3200, 1800], 1),
        client("0x3b", "Discord", [0, 0], [3840, 2160], 0),
        client("0x4c", "World of Warcraft", [0, 0], [3000, 1700], 0, 7, xwayland=True),
    ]
    monitors = [{"id": 0, "scale": 1.0}, {"id": 1, "scale": 1.2}, {"id": 2, "scale": 2.0}]
    w = pick_hyprland_window(clients, monitors, "world of warcraft")
    assert w is not None
    assert (w.title, w.pid, w.left, w.top, w.width, w.height) == (
        "World of Warcraft", 42, 2560, 0, 3840, 2160,
    )  # fmt: skip
    assert (w.handle, w.compositor, w.scale) == (0x2A, "hyprland", 1.2)
    assert pick_hyprland_window(clients, monitors, "Hearthstone") is None

    clients.append(client("0x5d", "World of Warcraft", [0, 0], [2560, 1440], 2, 7, xwayland=True))
    x = pick_hyprland_window(clients, monitors, "world of warcraft")
    assert x is not None
    assert (x.pid, x.width, x.height, x.handle, x.compositor) == (7, 5120, 2880, None, None)

    asked: list[int | None] = []
    monkeypatch.setattr(capture, "linux_hyprland", lambda: True)
    monkeypatch.setattr(capture, "linux_x11", lambda: True)
    monkeypatch.setattr(_x11, "find_window_x11", lambda _, pid: asked.append(pid))
    for winner, x11_asked in ((w, []), (x, [7])):
        monkeypatch.setattr(_wayland, "find_window_hyprland", lambda _, found=winner: found)
        capture.find_window()
        assert asked == x11_asked

    for symbols in (NAMED_SYMBOLS, HEX_SYMBOLS):
        codes = keycodes_from_keymap(KEYMAP % symbols)
        assert codes == {"1": 2, "f": 33, "enter": 28, "f1": 59}, symbols


def test_macos_window_focus_and_keys(monkeypatch):
    """CoreGraphics lists windows front to back, in points. The largest visible title match wins;
    when macOS withholds titles (Screen Recording off) the app's name stands in. The front-most
    visible app window owns the keyboard, whatever floats above it. On macOS the window lookup,
    the platform check, and the input device all go through Quartz, every supported key has its
    own keycode, and a key press without the Accessibility permission says so instead of being
    dropped."""

    def win(number, name, owner, pid, bounds, layer=0, alpha=1.0):
        return {"number": number, "name": name, "owner": owner, "pid": pid, "layer": layer,
                "alpha": alpha, "bounds": bounds}  # fmt: skip

    infos = [
        win(1, "", "Window Server", 90, (0, 0, 1512, 33), layer=25),  # the menu bar
        win(2, None, "Terminal", 50, (100, 100, 800, 500)),
        win(3, "World of Warcraft", "World of Warcraft", 77, (0, 33, 1512, 949)),
        win(4, "World of Warcraft - Wowhead", "Safari", 60, (0, 0, 600, 400)),
        win(5, "World of Warcraft", "World of Warcraft", 77, (0, 0, 4000, 3000), alpha=0.0),
    ]
    w = pick_quartz_window(infos, "world of warcraft")
    assert w is not None
    assert (w.title, w.pid, w.left, w.top, w.width, w.height, w.handle, w.scale) == (
        "World of Warcraft", 77, 0, 33, 1512, 949, 3, 1.0,
    )  # fmt: skip
    hidden = pick_quartz_window([{**i, "name": None} for i in infos], "World of Warcraft")
    assert hidden is not None and (hidden.title, hidden.handle) == ("World of Warcraft", 3)
    assert pick_quartz_window(infos, "Hearthstone") is None
    assert (frontmost_pid(infos), frontmost_pid(infos[2:])) == (50, 77)

    assert set(MAC_KEYCODES) == set(SUPPORTED_KEYS)
    assert len(set(MAC_KEYCODES.values())) == len(MAC_KEYCODES)
    assert (MAC_KEYCODES["1"], MAC_KEYCODES["f"], MAC_KEYCODES["f1"]) == (0x12, 0x03, 0x7A)

    posted: list[tuple[int, bool]] = []
    cg = SimpleNamespace(
        CGEventSourceCreate=lambda state: 1,
        CGEventCreateKeyboardEvent=lambda source, code, down: (code, down),
        CGEventSetFlags=lambda event, flags: None,
        CGEventPost=lambda tap, event: posted.append(event),
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(_quartz, "window_infos", lambda: infos)
    monkeypatch.setattr(_quartz, "frameworks", lambda: (cg, SimpleNamespace(CFRelease=id)))
    assert capture.find_window() == w
    assert unsupported_platform() == ""
    keyboard = get_input_device(w)
    assert isinstance(keyboard, input_quartz.QuartzInputDevice)
    assert keyboard.is_game_focused(w) is False  # Terminal is in front
    monkeypatch.setattr(input_quartz, "accessibility_available", lambda: (False, "Accessibility"))
    try:
        keyboard.press_key("f", 0)
    except PermissionMissing as e:
        assert str(e) == "Accessibility"
    else:
        raise AssertionError("pressed a key without the Accessibility permission")
    monkeypatch.setattr(input_quartz, "accessibility_available", lambda: (True, ""))
    keyboard.press_key("F", 0)
    assert posted == [(0x03, True), (0x03, False)]


def test_macos_listens_to_blackhole(monkeypatch, tmp_path):
    """macOS has no loopback capture, so the bot listens to an input device: BlackHole is listed
    first and is the default, under its name (CoreAudio ids change across reboots), and an exact
    id or name wins over one it is part of. It records with CoreAudio's own buffer size, since a
    device such as BlackHole refuses the 1024 frames used elsewhere. The GUI offers no "game
    only" choice there, and Start names the missing permission and the audio setting until both
    are fixed."""
    asked: list[dict] = []

    def mic(id_, name):
        return SimpleNamespace(id=id_, name=name, isloopback=False,
                               recorder=lambda **kw: asked.append(kw) or nullcontext())  # fmt: skip

    mics = [mic(4, "MacBook Pro Microphone"), mic(42, "BlackHole 16ch"), mic(43, "BlackHole 2ch")]
    soundcard = SimpleNamespace(all_microphones=lambda: list(mics), default_speaker=lambda: None)
    monkeypatch.setenv("WOWFISH_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(devices, "_soundcard", lambda: soundcard)
    found = [(d.id, d.is_default) for d in devices.list_loopback_devices()]
    assert found == [("BlackHole 16ch", True), ("BlackHole 2ch", True),
                     ("MacBook Pro Microphone", False)]  # fmt: skip
    assert devices.open_loopback("BlackHole 2ch").mic is mics[2]
    assert devices.open_loopback("blackhole").mic is mics[1]
    assert devices.open_loopback("4").mic is mics[0]  # not BlackHole 16ch, whose id is 42
    with devices.open_loopback("blackhole"):
        assert asked == [{"samplerate": 22050, "channels": 1, "blocksize": None}]

    monkeypatch.setattr(_quartz, "screen_capture_available", lambda request: (True, ""))
    monkeypatch.setattr(input_quartz, "accessibility_available", lambda request: (False, "AX off"))
    p = Presenter(find_window=lambda _: None)
    assert [d.id for d in p.audio_devices()] == [m.name for m in mics[1:] + mics[:1]]
    assert p.prerequisites()[:2] == ["AX off", devices.MAC_NO_GAME_AUDIO]
    p.save_audio_device(p.default_device().id)
    assert (p.cfg.audio.capture, p.cfg.audio.device) == ("device", "BlackHole 16ch")
    assert devices.MAC_NO_GAME_AUDIO not in p.prerequisites()


class _Pond:
    """A game in which every cast lands a bobber that bites `bite_after_s` later and Interact
    clears it. The quit key "0" closes the window 20 s after a press, once `ignored_quits`
    presses have gone unanswered. Fills in for the clock, window lookup, capturer, keyboard,
    matcher, audio, and detector; time moves when the engine sleeps, and 10 ms per audio read
    (one pass of the loop)."""

    def __init__(self, bite_after_s: float = 0.0, ignored_quits: int = 0) -> None:
        self.t = 0.0
        self.sample_rate = RATE
        self.bobber = False
        self.cast_at = 0.0
        self.bite_after_s = bite_after_s
        self.ignored_quits = ignored_quits
        self.closes_at = float("inf")
        self.window = WindowRef("World of Warcraft", None, 0, 0, 10, 10)
        self.focused = True
        self.pressed: list[tuple[float, str, float]] = []  # time, key, hold
        self.bites: list[float] = []
        self.cleared: list[float] = []  # when the engine dropped the buffered audio
        self.casts = 0

    def clock(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s

    def open(self, window) -> None:
        pass

    def close(self) -> None:
        pass

    def grab(self, box=None) -> np.ndarray:
        return np.zeros((10, 10, 3), np.uint8)

    def match(self, frame, region=None, full_width=None) -> IconMatch:
        # like the game, each cast lands the bobber somewhere a little different
        x = 3 + 3 * (self.casts % 2)
        return IconMatch(0.9 if self.bobber else 0.0, x, 5, 4, 1.0)

    def press_key(self, key: str, hold_s: float) -> None:
        self.pressed.append((self.t, key, hold_s))
        if key == "1":
            self.casts += 1
        if key in ("1", "f"):
            self.bobber = key == "1"
            self.cast_at = self.t
        if key == "0":
            self.ignored_quits -= 1
            if self.ignored_quits < 0:
                self.closes_at = min(self.closes_at, self.t + 20)

    def find(self, title: str) -> WindowRef | None:
        return None if self.t >= self.closes_at else self.window

    def is_game_focused(self, window) -> bool:
        return self.focused

    def read(self, n: int) -> np.ndarray:
        self.t += 0.01
        return np.zeros(n, np.float32)

    def clear(self) -> int:
        self.cleared.append(self.t)
        return 0

    def feed(self, chunk) -> list[BiteEvent]:
        if not self.bobber or self.t - self.cast_at < self.bite_after_s:
            return []
        self.bites.append(self.t)
        return [BiteEvent("fishingbobber_1", 0.9, 0.0)]

    def reset(self) -> None:
        pass


def _fish(
    behavior: BehaviorConfig,
    minutes: float,
    game: GameConfig | None = None,
    pond: _Pond | None = None,
    run: RunConfig | None = None,
) -> tuple[_Pond, list[str]]:
    """Fish for `minutes`, or until the session limit in `game` stops the engine."""
    pond = pond or _Pond()
    cfg = Config(game=game or GameConfig(), vision=VisionConfig(), audio=AudioConfig(),
                 run=run or RunConfig(), behavior=behavior)  # fmt: skip
    engine = FishingEngine(
        cfg, pond.window,
        capturer=pond, input_device=pond, audio_source=pond, detector=pond, matcher=pond,
        find_window=pond.find, clock=pond.clock, sleep=pond.sleep, rng=random.Random(7),
    )  # fmt: skip
    while pond.t < minutes * 60 and engine.state is not State.STOPPED:
        engine.step()
    return pond, [describe(e) for e in engine.events.queue]


def test_fishing_loop_varies_its_rhythm_only_when_asked():
    """Out of the box every press holds run.key_hold_ms, nothing but cast and Interact is
    pressed, and each cast first drops the audio buffered while waiting, so the splash is heard
    when it happens. Each [behavior] setting, once filled in, shows in the presses:
    - breaks: after the fishing stretch the loop rests for the whole break
    - reaction: Interact waits out the reaction time after the splash
    - skipped bites: some splashes get no Interact
    - key holds: they vary within the jitter
    - jumps: every cast follows a jump, once the character has landed
    """
    pond, _ = _fish(BehaviorConfig(), 1)
    assert {k for _, k, _ in pond.pressed} == {"1", "f"}
    assert {h for *_, h in pond.pressed} == {RunConfig().key_hold_ms / 1000}
    assert pond.cleared == [t for t, k, _ in pond.pressed if k == "1"]

    behavior = BehaviorConfig(
        break_every_min_minutes=1,
        break_every_max_minutes=1,
        break_min_minutes=2,
        break_max_minutes=2,
        reaction_min_ms=200,
        reaction_max_ms=400,
        skip_bite_chance=0.3,
        key_hold_jitter_ms=20,
        jump_chance=1,
    )
    pond, lines = _fish(behavior, 5)
    gaps = [b[0] - a[0] for a, b in zip(pond.pressed, pond.pressed[1:], strict=False)]
    assert max(gaps) >= 120
    assert any("Taking a 2.0 min break" in line for line in lines)
    for t in (t for t, key, _ in pond.pressed if key == "f"):
        heard = max(b for b in pond.bites if b <= t)
        assert 0.2 - 1e-9 <= t - heard <= 0.4 + 1e-9
    assert 0 < [k for _, k, _ in pond.pressed].count("f") < len(pond.bites)
    assert any("Let this bite go on purpose" in line for line in lines)
    holds = {h for *_, h in pond.pressed}
    assert len(holds) > 1 and all(0.04 <= h <= 0.08 for h in holds)
    assert pond.pressed[0][1] == "space"
    for (jumped_at, before, _), (t, key, _) in zip(pond.pressed, pond.pressed[1:], strict=False):
        if key == "1":
            assert before == "space" and t - jumped_at >= 1.0


def test_session_limit_quits_the_game_only_when_asked():
    """When the time limit runs out mid-cast with a quit key set, that cast still ends in
    Interact; only then is the key pressed, and the bot stops once the game window is gone. A
    press the game ignores is repeated, 3 presses at most. Without a quit key, or with the game
    not focused, the bot only stops."""
    game = GameConfig(session_minutes=1, quit_key="0")
    pond, lines = _fish(BehaviorConfig(), 5, game, _Pond(bite_after_s=15))
    keys = [k for _, k, _ in pond.pressed]
    (last_cast, *_), (interact, *_), (quit_at, *_) = pond.pressed[-3:]
    assert keys[-3:] == ["1", "f", "0"] and keys.count("0") == 1
    assert last_cast < 60 < interact <= quit_at
    assert "The game closed. Stopping." in lines and lines[-1] == "Stopped (session limit)"

    pond, lines = _fish(BehaviorConfig(), 5, game, _Pond(ignored_quits=1))
    assert [k for _, k, _ in pond.pressed].count("0") == 2
    assert lines[-1] == "Stopped (session limit)"
    pond, lines = _fish(BehaviorConfig(), 5, game, _Pond(ignored_quits=9))
    assert [k for _, k, _ in pond.pressed].count("0") == 3
    assert any("the game is still open" in line for line in lines)

    pond, _ = _fish(BehaviorConfig(), 5, GameConfig(session_minutes=1))
    assert "0" not in {k for _, k, _ in pond.pressed}

    pond = _Pond()
    pond.focused = False
    pond, lines = _fish(BehaviorConfig(), 5, game, pond)
    assert pond.pressed == []
    assert any("left it running" in line for line in lines)


class _AwayPond(_Pond):
    """A pond the player alt-tabs away from: the game is not focused from `away[0]` to
    `away[1]` seconds into the session."""

    def __init__(self, away: tuple[float, float], **kw) -> None:
        super().__init__(**kw)
        self.away = away

    def is_game_focused(self, window) -> bool:
        return not self.away[0] <= self.t < self.away[1]


def _casts(pond: _Pond) -> list[float]:
    return [t for t, k, _ in pond.pressed if k == "1"]


def test_a_cast_that_goes_nowhere_is_recast_after_cast_timeout():
    """No splash ever comes and the tracker keeps seeing the bobber, so nothing but the cast's
    hard limit ends a cast: every cast is followed by the next one run.cast_timeout_s later
    (plus the random pause), and Interact is never pressed."""
    run = RunConfig(cast_timeout_s=10.0)
    pond, lines = _fish(BehaviorConfig(), 2, pond=_Pond(bite_after_s=1e9), run=run)
    casts = _casts(pond)
    assert len(casts) >= 8 and "f" not in {k for _, k, _ in pond.pressed}
    gaps = [b - a for a, b in itertools.pairwise(casts)]
    assert all(10.0 < g <= 10.0 + run.pause_max_s + 0.1 for g in gaps)
    assert sum("No splash within 10 s" in line for line in lines) >= len(gaps)


def test_a_bite_just_before_the_limit_still_gets_looted():
    run = RunConfig(cast_timeout_s=10.0)
    pond, lines = _fish(BehaviorConfig(), 1, pond=_Pond(bite_after_s=9.9), run=run)
    assert "f" in {k for _, k, _ in pond.pressed}
    assert not any("No splash within" in line for line in lines)


def test_a_cast_that_runs_out_while_paused_is_recast_on_resume():
    """The game's cast goes on while the player is away, so a cast whose limit passed in the
    meantime is recast as soon as the game has focus again."""
    run = RunConfig(cast_timeout_s=10.0)
    pond, lines = _fish(BehaviorConfig(), 0.5, pond=_AwayPond((2, 20), bite_after_s=1e9), run=run)
    after = [t for t in _casts(pond) if t >= 20]
    assert after and after[0] <= 20 + run.pause_max_s + 0.2
    assert not [t for t in _casts(pond) if 2 <= t < 20]
    assert any("ran out while the game was not focused" in line for line in lines)


def test_a_short_pause_does_not_cost_the_cast():
    """Away for 3 s with the cast well inside its limit: the bot carries on and loots the
    bite that comes after the pause."""
    run = RunConfig(cast_timeout_s=10.0)
    pond, lines = _fish(BehaviorConfig(), 0.2, pond=_AwayPond((2, 5), bite_after_s=8), run=run)
    first_interact = next(t for t, k, _ in pond.pressed if k == "f")
    assert 8 <= first_interact < 9
    assert "Resumed" in lines and not any("No splash within" in line for line in lines)


def test_a_bite_heard_just_before_a_pause_is_looted_on_a_quick_return():
    """The splash comes, then focus goes during the reaction delay. Back within 1.5 s: Interact
    is pressed at once. Back later: that bite is let go."""
    behavior = BehaviorConfig(reaction_min_ms=200, reaction_max_ms=200)
    quick = _AwayPond((3.1, 3.6), bite_after_s=3)
    pond, _ = _fish(behavior, 0.1, pond=quick)
    first_interact = next(t for t, k, _ in pond.pressed if k == "f")
    assert 3.6 <= first_interact < 3.8

    slow = _AwayPond((3.1, 6.0), bite_after_s=3)
    pond, _ = _fish(behavior, 0.1, pond=slow)
    assert not [t for t, k, _ in pond.pressed if k == "f" and t < 6.0 + 0.1]


def test_cast_timeout_is_checked_and_saved_without_losing_the_file(tmp_path):
    """run.cast_timeout_s must be 5-120 s. Saving a change rewrites only that line: comments,
    unknown keys, and lines the user left out stay as they were."""
    path = tmp_path / "config.toml"
    for bad in (4.9, 121):
        path.write_text(f"[run]\ncast_timeout_s = {bad}\n", encoding="utf-8")
        try:
            load_config(path)
        except Exception as e:
            assert "run.cast_timeout_s" in str(e)
        else:
            raise AssertionError(f"{bad} was accepted")
    for good in (5, 120):
        path.write_text(f"[run]\ncast_timeout_s = {good}\n", encoding="utf-8")
        assert load_config(path)[0].run.cast_timeout_s == good

    text = '# mine\n[game]\ncast_key = "2"  # my key\nextra = 1\n\n[audio]\ndevice = "Динамики"\n'
    path.write_text(text, encoding="utf-8")
    cfg, _ = load_config(path)
    save_config(replace(cfg, run=replace(cfg.run, cast_timeout_s=22.0)), path)
    saved = path.read_text(encoding="utf-8")
    assert saved.startswith(text) and "cast_timeout_s = 22.0" in saved
    assert "interact_key" not in saved  # still at its default, so still left out
    assert load_config(path)[0].run.cast_timeout_s == 22.0


def test_cast_timeout_from_the_setup_tab_and_its_log_line(tmp_path, monkeypatch):
    monkeypatch.setenv("WOWFISH_HOME", str(tmp_path))
    p = Presenter(build_engine=lambda *a, **k: None, list_devices=lambda: [],
                  find_window=lambda *a: None)  # fmt: skip
    assert p.save_cast_timeout("22") == 22.0 and p.cfg.run.cast_timeout_s == 22.0
    for bad in ("4", "abc", "121"):
        try:
            p.save_cast_timeout(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad!r} was accepted")
    line = describe(ev.Timeout(1.0, "cast", 22.0, "nothing matched"))
    assert line == "No catch within 22 s of casting; nothing matched; recasting"


def test_fish_tab_words_and_numbers():
    """The stat tiles and the state pill read as plain words, not engine names."""
    from wowfish.gui import theme
    from wowfish.gui.fish_page import tile_values

    assert tile_values(None)["rate"] == "-"
    tiles = tile_values(ev.Stats(0.0, casts=42, catches=37, misses=5, elapsed_s=3725))
    assert tiles == {"catches": "37", "casts": "42", "misses": "5", "rate": "88%",
                     "time": "1:02:05"}  # fmt: skip
    assert theme.state_look("WAIT_BITE")[0] == "Waiting for a bite"
    assert theme.state_look("STOPPED", "Stopped (error)")[1] == theme.RED


def test_start_hands_the_screen_to_the_game(tmp_path, monkeypatch):
    """Start focuses the game window before the bot's first step; the focus helper picks
    the platform's way and never raises."""
    import queue

    from wowfish.capture import focus

    monkeypatch.setenv("WOWFISH_HOME", str(tmp_path))
    window = WindowRef("World of Warcraft", 42, 0, 0, 10, 10, handle=7)
    order: list[str] = []
    engine = SimpleNamespace(
        window=window, events=queue.Queue(), run=lambda: order.append("run"), stop=lambda: None
    )
    focused: list[object] = []
    p = Presenter(
        build_engine=lambda *a, **k: engine, list_devices=lambda: [],
        find_window=lambda *a: window,
        focus_window=lambda w: focused.append(w) or order.append("focus") or True,
    )  # fmt: skip
    p.start()
    p._thread._thread.join(2)
    assert focused == [window] and order == ["focus", "run"]
    assert p.focus_game() is True

    assert focus.focus_window(None) is False
    sent: list[str] = []
    monkeypatch.setattr(_wayland, "hyprland_dispatch", lambda c: sent.append(c) or "ok")
    native = WindowRef("World of Warcraft", 9, 0, 0, 10, 10, 0x5F00, "hyprland")
    assert focus.focus_window(native) is True and sent == ["focuswindow address:0x5f00"]
    monkeypatch.setattr(_wayland, "hyprland_dispatch", lambda c: 1 / 0)
    assert focus.focus_window(native) is False


def test_mini_bar_setting(tmp_path, monkeypatch):
    """gui.mini_bar is on by default, can be switched off in the file or on the Setup tab,
    and an older config gains it with its comment."""
    monkeypatch.setenv("WOWFISH_HOME", str(tmp_path))
    assert Config(game=GameConfig(), vision=VisionConfig(), audio=AudioConfig()).gui.mini_bar
    path = tmp_path / "config.toml"
    path.write_text("[gui]\nmini_bar = false\n", encoding="utf-8")
    assert load_config(path)[0].gui.mini_bar is False
    path.write_text("[gui]\nmini_bar = 1\n", encoding="utf-8")
    try:
        load_config(path)
    except Exception as e:
        assert "gui.mini_bar must be bool" in str(e)
    else:
        raise AssertionError("an int was accepted for a bool")

    p = Presenter(build_engine=lambda *a, **k: None, list_devices=lambda: [],
                  find_window=lambda *a: None)  # fmt: skip
    p.save_mini_bar(False)
    assert p.cfg.gui.mini_bar is False and "mini_bar = false" in p_config_text(tmp_path)


def p_config_text(home: Path) -> str:
    return (home / "config" / "config.toml").read_text(encoding="utf-8")


def test_config_written_on_windows_round_trips(tmp_path):
    """A config file with CRLF line endings (as Windows writes it) loads, saves, and loads
    again; saving never doubles the CR, and adds no empty sections."""
    from wowfish import config as config_module

    path = tmp_path / "config.toml"
    path.write_bytes(b'# mine\r\n[game]\r\ncast_key = "2"\r\n')
    assert "\r" not in config_module._read_text(path)
    cfg, _ = load_config(path)
    save_config(replace(cfg, run=replace(cfg.run, cast_timeout_s=22.0)), path)
    raw = path.read_bytes()
    assert b"\r\r" not in raw and b"[vision]" not in raw and b"[gui]" not in raw
    reloaded, _ = load_config(path)
    assert reloaded.game.cast_key == "2" and reloaded.run.cast_timeout_s == 22.0


class _UiPond(_Pond):
    """A pond whose screen also shows a gold UI gear (a minimap button, say) that matches better
    than the bobber and never moves: pixel 200 marks it, pixel 100 marks the bobber."""

    UI_XY = (180, 10)
    BOBBER_XY = (60, 150)

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.window = WindowRef("World of Warcraft", None, 0, 0, 200, 200)

    def grab(self, box=None) -> np.ndarray:
        frame = np.zeros((200, 200, 3), np.uint8)
        frame[self.UI_XY[1], self.UI_XY[0]] = 200
        if self.bobber:
            frame[self.BOBBER_XY[1], self.BOBBER_XY[0]] = 100
        if box is not None:
            x, y, w, h = box
            return frame[y : y + h, x : x + w]
        return frame

    def match(self, frame, region=None, full_width=None) -> IconMatch:
        for value, score in ((200, 0.95), (100, 0.9)):
            ys, xs = np.nonzero(frame[..., 0] == value)
            if len(xs):
                return IconMatch(score, int(xs[0]), int(ys[0]), 4, 1.0)
        return IconMatch(0.0, -1, -1, 4, 0.0)


def test_a_gear_that_never_moves_is_part_of_the_ui_not_the_bobber():
    """A gold gear on the same pixel before two casts in a row is part of the UI. It may fool
    the first cast; from the second on it is ignored, the real bobber is found, and caught
    fish count as catches, not misses."""
    _, lines = _fish(BehaviorConfig(), 1, pond=_UiPond(bite_after_s=3))
    ignoring = [i for i, line in enumerate(lines) if line.startswith("Ignoring a gold gear")]
    assert len(ignoring) == 1 and "(180, 10)" in lines[ignoring[0]]
    after = lines[ignoring[0] :]
    found = [line for line in after if line.startswith("Found the bobber at")]
    assert len(found) >= 5 and all("(60, 150)" in line for line in found)
    assert not any("did not go away" in line for line in after)
    assert sum(line.startswith("Caught") for line in after) >= 5


class _StuckGearPond(_Pond):
    """A pond where the tracked gear stays after Interact (the bot is watching a UI gear, or
    the loot is slow), but the game shows its "YOUR CATCH" popup when a fish is looted."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.popup_up = False

    def press_key(self, key: str, hold_s: float) -> None:
        if key == "f":
            self.pressed.append((self.t, key, hold_s))
            self.popup_up = True  # caught: the popup shows, the gear stays
            return
        self.popup_up = False if key == "1" else self.popup_up
        super().press_key(key, hold_s)

    def seen(self, frame) -> bool:  # the popup detector
        return self.popup_up


def _fish_with_popup(pond: _Pond, minutes: float, popup: object) -> list[str]:
    cfg = Config(game=GameConfig(), vision=VisionConfig(), audio=AudioConfig())
    engine = FishingEngine(
        cfg, pond.window,
        capturer=pond, input_device=pond, audio_source=pond, detector=pond, matcher=pond,
        find_window=pond.find, clock=pond.clock, sleep=pond.sleep, rng=random.Random(7),
        catch_popup=popup,
    )  # fmt: skip
    while pond.t < minutes * 60:
        engine.step()
    return [describe(e) for e in engine.events.queue]


def test_the_catch_popup_counts_as_a_catch():
    """After Interact, the game's "YOUR CATCH" popup counts the fish even while the tracked
    gear stays on screen; without the popup check those casts are misses."""
    pond = _StuckGearPond(bite_after_s=3)
    lines = _fish_with_popup(pond, 1, popup=pond)
    assert sum(line == "Caught a fish; the catch popup showed" for line in lines) >= 5
    assert not any("did not go away" in line for line in lines)

    pond = _StuckGearPond(bite_after_s=3)
    lines = _fish_with_popup(pond, 1, popup=SimpleNamespace(seen=lambda frame: False))
    assert not any(line.startswith("Caught") for line in lines)
    assert any("did not go away" in line for line in lines)


def test_a_popup_left_from_the_last_fish_does_not_count():
    """A popup already up when Interact is pressed says nothing about this cast."""
    pond = _StuckGearPond(bite_after_s=3)
    always = SimpleNamespace(seen=lambda frame: True)
    lines = _fish_with_popup(pond, 0.5, popup=always)
    assert not any("the catch popup showed" in line for line in lines)


def test_catch_popup_detector_on_a_real_screenshot():
    """The detector finds the header in the screenshot it was cut from, at other UI scales on
    a 4K frame, and nothing once the popup is painted over."""
    import cv2

    from wowfish.vision.catch_popup import CatchPopup

    popup = CatchPopup()
    template = cv2.imread(str(popup_template_path()), cv2.IMREAD_COLOR)
    frame = np.full((2160, 3840, 3), (60, 70, 70), np.uint8)
    for scale in (1.0, 2.0):
        shown = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        canvas = frame.copy()
        canvas[1000 : 1000 + shown.shape[0], 2000 : 2000 + shown.shape[1]] = shown
        assert popup.seen(canvas), scale
    assert not popup.seen(frame)


def popup_template_path():
    from wowfish.vision.catch_popup import template_path

    return template_path()


def _badge_scene(scale: float) -> np.ndarray:
    """Grey-blue water with the bundled anvil badge drawn on it at `scale`."""
    import cv2

    from wowfish.assets import icon_path

    rgba = cv2.imread(str(icon_path(52)), cv2.IMREAD_UNCHANGED)
    rgba = cv2.resize(rgba, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    rng = np.random.default_rng(3)
    frame = (np.array([120, 112, 100]) + rng.normal(0, 6, (600, 800, 3))).clip(0, 255)
    frame = frame.astype(np.uint8)
    h, w = rgba.shape[:2]
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255
    roi = frame[300 : 300 + h, 400 : 400 + w].astype(np.float32)
    frame[300 : 300 + h, 400 : 400 + w] = (alpha * rgba[:, :, :3] + (1 - alpha) * roi).astype(
        np.uint8
    )
    return frame


def test_auto_calibration_finds_the_anvil_badge():
    """Clients that draw a round anvil badge over the bobber instead of the gold gear: the gear
    templates find nothing there, "auto" finds the badge at its size and spot."""
    from wowfish.vision.calibrate import calibrate_icon_scale
    from wowfish.vision.template import load_icon_templates

    for scale in (1.0, 1.5):
        frame = _badge_scene(scale)
        side = round(52 * scale)
        gear = calibrate_icon_scale(frame, load_icon_templates("gear"))
        assert not gear.valid
        cal = calibrate_icon_scale(frame, load_icon_templates("auto"))
        assert cal.valid and cal.style == "badge", (scale, cal)
        assert abs(cal.icon_size_px - side) <= side * 0.15
        assert abs(cal.x - (400 + side / 2)) <= 4 and abs(cal.y - (300 + side / 2)) <= 4


def test_icon_style_setting():
    """vision.icon_style picks the icon; "auto" starts from the style of the saved size."""
    from wowfish.engine.engine import _starting_style

    def cfg(**vision):
        return Config(game=GameConfig(), vision=VisionConfig(**vision), audio=AudioConfig())

    assert cfg().vision.icon_style == "auto"
    assert _starting_style(cfg()) == "gear"
    assert _starting_style(cfg(icon_native_size=52)) == "badge"
    assert _starting_style(cfg(icon_native_size=42)) == "gear"
    assert _starting_style(cfg(icon_style="badge", icon_native_size=42)) == "badge"


def test_icon_style_is_validated(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[vision]\nicon_style = "badge"\n', encoding="utf-8")
    assert load_config(path)[0].vision.icon_style == "badge"
    path.write_text('[vision]\nicon_style = "anvil"\n', encoding="utf-8")
    try:
        load_config(path)
    except Exception as e:
        assert "vision.icon_style" in str(e)
    else:
        raise AssertionError("an unknown icon style was accepted")


def test_reset_settings(tmp_path, monkeypatch):
    """Reset puts every setting back to its default and keeps the old file as a .bak; it is
    refused while fishing."""
    from wowfish.config import reset_config_file

    path = tmp_path / "config.toml"
    assert reset_config_file(path) is None and path.exists()  # nothing to back up yet
    path.write_text('[game]\ncast_key = "2"\n[vision]\nicon_size_px = 23\n', encoding="utf-8")
    backup = reset_config_file(path)
    assert backup == tmp_path / "config.toml.bak"
    assert 'cast_key = "2"' in backup.read_text(encoding="utf-8")
    cfg, _ = load_config(path)
    assert cfg == Config(game=GameConfig(), vision=VisionConfig(), audio=AudioConfig())

    monkeypatch.setenv("WOWFISH_HOME", str(tmp_path / "home"))
    p = Presenter(build_engine=lambda *a, **k: None, list_devices=lambda: [],
                  find_window=lambda *a: None)  # fmt: skip
    p.save_cast_timeout("22")
    assert p.reset_config() is not None and p.cfg.run.cast_timeout_s == 30.0
    monkeypatch.setattr(Presenter, "running", property(lambda self: True))
    try:
        p.reset_config()
    except RuntimeError as e:
        assert "stop fishing" in str(e)
    else:
        raise AssertionError("reset while fishing was allowed")


def test_titlebar_colorref_is_bgr():
    from wowfish.gui import theme, titlebar

    assert titlebar.colorref("#112233") == 0x332211
    colors = titlebar.frame_colors()
    assert colors[titlebar.DWMWA_CAPTION_COLOR] == titlebar.colorref(theme.BG)
    assert colors[titlebar.DWMWA_BORDER_COLOR] == titlebar.colorref(theme.CARD_EDGE)


def test_titlebar_dark_frame_is_a_noop_off_windows(monkeypatch):
    from wowfish.gui import titlebar

    monkeypatch.setattr(titlebar.sys, "platform", "linux")
    titlebar.dark_frame(object())  # touches nothing on the window


def test_desktop_shortcut_target_prefers_the_gui(tmp_path):
    from wowfish import shortcut

    assert shortcut.gui_target(None) is None  # running from source
    gui = tmp_path / "wowfish-gui.exe"
    assert shortcut.gui_target(gui, windows=True) == shortcut.Target(gui)
    cli = tmp_path / "wowfish.exe"
    assert shortcut.gui_target(cli, windows=True) == shortcut.Target(cli, ("gui",))
    gui.write_bytes(b"")
    assert shortcut.gui_target(cli, windows=True) == shortcut.Target(gui)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths and symlinks")
def test_desktop_shortcut_on_linux_and_macos(tmp_path, monkeypatch):
    from wowfish import shortcut

    monkeypatch.setattr(shortcut, "_trust_on_gnome", lambda _p: None)
    exe = tmp_path / "my apps" / "wowfish"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    target = shortcut.Target(exe, ("gui",))

    path = shortcut.create(target, desktop, platform="linux", icon=tmp_path / "icon.png")
    text = path.read_text(encoding="utf-8")
    assert f'Exec="{exe}" gui\n' in text and f"Icon={tmp_path / 'icon.png'}" in text
    assert path.stat().st_mode & 0o111
    assert shortcut.create(target, desktop, platform="linux") == path  # overwrites

    link = shortcut.create(target, desktop, platform="darwin")
    assert link.is_symlink() and link.resolve() == exe.resolve()
    assert shortcut.create(target, desktop, platform="darwin") == link  # replaces the link

    try:
        shortcut.create(target, tmp_path / "nowhere", platform="linux")
    except shortcut.ShortcutError as e:
        assert "no desktop folder" in str(e)
    else:
        raise AssertionError("a missing desktop was accepted")


def test_desktop_entry_quotes_exec_arguments():
    from wowfish.shortcut import _desktop_quote

    assert _desktop_quote("/opt/wowfish") == "/opt/wowfish"
    assert _desktop_quote("/a b/100%") == '"/a b/100%%"'
    assert _desktop_quote('/a "b"') == '"/a \\\\"b\\\\""'


def test_desktop_shortcut_from_source_is_refused(tmp_path, monkeypatch):
    from wowfish.shortcut import ShortcutError

    monkeypatch.setenv("WOWFISH_HOME", str(tmp_path))
    p = Presenter(build_engine=lambda *a, **k: None, list_devices=lambda: [],
                  find_window=lambda *a: None)  # fmt: skip
    try:
        p.create_desktop_shortcut()
    except ShortcutError as e:
        assert "running from source" in str(e)
    else:
        raise AssertionError("a shortcut was made with no executable")


def test_desktop_shortcut_icon_is_copied_out_of_the_bundle(tmp_path):
    from wowfish.shortcut import saved_icon

    icon = saved_icon(tmp_path)
    assert icon is not None and icon.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
