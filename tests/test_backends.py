"""Tests for apricus.backends (no display required)."""

from unittest.mock import patch

from apricus import backends
from apricus.backends import (
    GnomeShellWindowStateBackend,
    GnomeWallpaperBackend,
    NullWallpaperBackend,
    NullWindowStateBackend,
    WnckWindowStateBackend,
    create_wallpaper_backend,
    create_window_state_backend,
)


def test_null_wallpaper_backend_is_inert(tmp_path):
    wb = NullWallpaperBackend()
    assert wb.supports_static is False
    wb.capture_original()
    wb.apply_static(str(tmp_path / "x.png"))
    wb.restore_original()
    wb.prune_cache(set())
    wb.raise_desktop_icons()


def test_gnome_backend_settings_path(monkeypatch):
    written = {}

    class FakeSettings:
        def get_string(self, key):
            return f"orig-{key}"

        def set_string(self, key, value):
            written[key] = value

    wb = GnomeWallpaperBackend(flatpak=False)
    monkeypatch.setattr(wb, "_settings", FakeSettings)
    wb.capture_original()
    assert wb.original_wallpaper_uri == "orig-picture-uri"
    assert wb.original_wallpaper_uri_dark == "orig-picture-uri-dark"
    wb.apply_static("/tmp/frame.png")
    assert written["picture-uri"].endswith("frame.png")
    assert written["picture-uri-dark"].endswith("frame.png")
    wb.restore_original()
    assert written["picture-uri"] == "orig-picture-uri"
    assert written["picture-uri-dark"] == "orig-picture-uri-dark"


def test_gnome_backend_flatpak_path(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return None

    def fake_output(cmd, **kwargs):
        return "file:///orig.png\n"

    wb = GnomeWallpaperBackend(flatpak=True)
    monkeypatch.setattr(backends.subprocess, "run", fake_run)
    monkeypatch.setattr(backends.subprocess, "check_output", fake_output)
    wb.capture_original()
    assert wb.original_wallpaper_uri == "file:///orig.png\n"
    wb.apply_static("/tmp/frame.png")
    assert any("picture-uri-dark" in c for c in calls)
    wb.restore_original()
    hits = sum(1 for c in calls if any("picture-uri" in str(a) for a in c))
    assert hits >= 4


def test_gnome_prune_cache(tmp_path, monkeypatch):
    from apricus import commons

    monkeypatch.setattr(commons, "CONFIG_DIR", str(tmp_path))
    keep = tmp_path / "static-keep.png"
    stale = tmp_path / "static-123456.png"
    other = tmp_path / "notes.txt"
    keep.write_text("k")
    stale.write_text("s")
    other.write_text("o")
    wb = GnomeWallpaperBackend(flatpak=False)
    wb.prune_cache({str(keep)})
    assert keep.is_file()
    assert not stale.exists()
    assert other.is_file()


def test_parse_state():
    p = GnomeShellWindowStateBackend.parse_state
    assert p(None) == {"is_any_maximized": False, "is_any_fullscreen": False}
    assert p([{"maximized": True}])["is_any_maximized"] is True
    assert p([{"is-fullscreen": 1}])["is_any_fullscreen"] is True
    assert p([{"width": 800}, "junk"]) == {
        "is_any_maximized": False,
        "is_any_fullscreen": False,
    }


def test_probe_denied_returns_none():
    with patch.object(backends, "SessionBus", side_effect=Exception("denied")):
        assert GnomeShellWindowStateBackend.try_probe(lambda s: None) is None


def test_create_wallpaper_backend_selection(monkeypatch):
    monkeypatch.setattr(backends, "is_gnome", lambda: True)
    assert isinstance(create_wallpaper_backend(), GnomeWallpaperBackend)
    monkeypatch.setattr(backends, "is_gnome", lambda: False)
    assert isinstance(create_wallpaper_backend(), NullWallpaperBackend)


def test_create_window_state_x11(monkeypatch):
    monkeypatch.setattr(backends, "is_wayland", lambda: False)
    events = []
    wb = create_window_state_backend(events.append)
    assert isinstance(wb, WnckWindowStateBackend)


def test_create_window_state_wayland_denied_falls_back(monkeypatch):
    monkeypatch.setattr(backends, "is_wayland", lambda: True)
    monkeypatch.setattr(
        GnomeShellWindowStateBackend, "try_probe", classmethod(lambda cls, cb: None)
    )
    wb = create_window_state_backend(lambda s: None)
    assert isinstance(wb, NullWindowStateBackend)


def test_wnck_backend_delegates_cleanup(monkeypatch):
    cleaned = []

    class FakeHandler:
        def __init__(self, cb):
            self.cb = cb

        def cleanup(self):
            cleaned.append(True)

    monkeypatch.setattr(backends, "WindowHandler", FakeHandler, raising=False)
    # Wnck backend imports WindowHandler lazily from utils; patch utils instead
    import apricus.utils as U

    monkeypatch.setattr(U, "WindowHandler", FakeHandler)
    wb = WnckWindowStateBackend(lambda s: None)
    wb.start()
    wb.stop()
    assert cleaned == [True]
    wb.stop()  # idempotent
