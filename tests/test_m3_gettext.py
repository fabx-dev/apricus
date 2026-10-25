"""Extraction + compiled gettext + real headless GTK4 subtitle selection."""

import gettext
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MESSAGES = (
    "Requires GNOME on Wayland",
    "Window policies are not available in Snap on Wayland",
    "Window-state access has not been verified in Flatpak",
    "Requires the Apricus GNOME extension on Wayland",
)
LANGUAGES = (ROOT / "po/LINGUAS").read_text().split()


def test_m3_subtitles_are_extracted_and_in_template(tmp_path):
    output = tmp_path / "extracted.pot"
    subprocess.run(
        [
            "xgettext",
            "--language=Python",
            "--from-code=UTF-8",
            "--keyword=_",
            "--files-from=po/POTFILES",
            "--output=" + str(output),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    for text in (output.read_text(), (ROOT / "po/apricus.pot").read_text()):
        for message in MESSAGES:
            assert f'msgid "{message}"' in text


@pytest.mark.parametrize("language", LANGUAGES)
def test_compiled_subtitles_load_through_gui_domain(tmp_path, language):
    catalog = tmp_path / language / "LC_MESSAGES/apricus.mo"
    catalog.parent.mkdir(parents=True)
    subprocess.run(
        ["msgfmt", "--check", "--output-file=" + str(catalog), str(ROOT / "po" / f"{language}.po")],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    translator = gettext.translation("apricus", localedir=tmp_path, languages=[language])
    assert translator.info()["language"] == language
    for message in MESSAGES:
        assert translator.gettext(message).strip()
        assert translator.gettext(message) != message, (language, message)
    # GTK3 and GTK4 cannot share an interpreter. Run the real GTK4 helper and
    # init_translations in isolation, with no display or session bus access.
    driver = r"""
import gettext
import sys
import apricus.gui.gtk4app as app
from apricus.commons import TRANSLATION_DOMAIN
from apricus.utils import init_translations
assert TRANSLATION_DOMAIN == 'apricus'
init_translations(sys.argv[1])
assert gettext.textdomain() == TRANSLATION_DOMAIN
translator = gettext.translation(TRANSLATION_DOMAIN, sys.argv[1], languages=[sys.argv[2]])
panel = app.ControlPanel.__new__(app.ControlPanel)
panel._server = lambda: None
app.is_wayland = lambda: True
for gnome, snap, flatpak, message in [
    (False, False, False, 'Requires GNOME on Wayland'),
    (True, True, False, 'Window policies are not available in Snap on Wayland'),
    (True, False, True, 'Window-state access has not been verified in Flatpak'),
    (True, False, False, 'Requires the Apricus GNOME extension on Wayland'),
]:
    app.is_gnome = lambda: gnome
    app.is_snap = lambda: snap
    app.is_flatpak = lambda: flatpak
    enabled, subtitle = panel._window_policy_support()
    assert subtitle == translator.gettext(message), (message, subtitle)
    assert subtitle != message, (sys.argv[2], message)
print('PASS: four localized GUI subtitles, domain apricus, language', sys.argv[2])
"""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), LANGUAGE=language, LC_ALL="C.UTF-8")
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent/m3-gettext-test-bus"
    result = subprocess.run(
        [sys.executable, "-c", driver, str(tmp_path), language],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
