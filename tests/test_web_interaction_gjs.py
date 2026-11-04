import pathlib
import shutil
import subprocess

import pytest


def test_shell_interaction_controller_and_client():
    if not shutil.which("gjs"):
        pytest.skip("GJS unavailable")
    root = pathlib.Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["gjs", "-m", "tests/gjs/web-interaction.js"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS:" in result.stdout
