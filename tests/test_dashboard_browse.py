"""Settings → Notes directory → "Choose in Finder…": a native macOS folder
picker (osascript `choose folder`) fills the field."""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from notetaker.config import Config
from notetaker.dashboard import app, pick_folder_with_finder


@pytest.fixture
def client():
    return TestClient(app, base_url="http://127.0.0.1")


def _config(tmp_path):
    return Config(tmp_path / "notes", "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY")


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_pick_folder_returns_the_chosen_directory(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _Completed(stdout="/Users/me/Meeting Notes/\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert pick_folder_with_finder() == Path("/Users/me/Meeting Notes")
    assert seen["cmd"][0] == "osascript"
    assert "choose folder" in " ".join(seen["cmd"])


def test_pick_folder_starts_in_the_current_notes_dir_when_it_exists(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: seen.setdefault("cmd", cmd) and _Completed(stdout="/x/"))
    start = tmp_path / 'odd "name"'
    start.mkdir()

    pick_folder_with_finder(start)

    script = " ".join(seen["cmd"])
    assert "default location" in script
    assert 'odd \\"name\\"' in script  # quotes escaped for AppleScript


def test_pick_folder_returns_none_when_the_user_cancels(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: _Completed(returncode=1, stderr="execution error: User canceled. (-128)")
    )

    assert pick_folder_with_finder() is None


def test_pick_folder_raises_a_readable_error_when_osascript_is_missing(monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError("osascript")

    monkeypatch.setattr(subprocess, "run", missing)

    with pytest.raises(RuntimeError, match="macOS"):
        pick_folder_with_finder()


def test_pick_folder_raises_on_other_osascript_failures(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: _Completed(returncode=1, stderr="execution error: Not authorized (-1743)")
    )

    with pytest.raises(RuntimeError, match="Not authorized"):
        pick_folder_with_finder()


def _wire(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_masked_provider_credential", lambda config: None)


def test_settings_page_offers_a_finder_button_for_the_notes_dir(client, monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)

    response = client.get("/settings")

    assert 'id="notes-dir-field"' in response.text
    assert 'hx-post="/settings/browse"' in response.text
    assert "Choose in Finder" in response.text


def test_browse_fills_the_field_with_the_chosen_folder(client, monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    monkeypatch.setattr("notetaker.dashboard.pick_folder_with_finder", lambda start=None: Path("/Users/me/Notes"))

    response = client.post("/settings/browse", data={"notes_dir": "~/old"})

    assert response.status_code == 200
    assert 'value="/Users/me/Notes"' in response.text
    assert 'id="notes-dir-field"' in response.text
    assert "Save configuration" in response.text  # reminds the user the choice is not saved yet


def test_browse_keeps_the_old_value_when_the_picker_is_cancelled(client, monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    monkeypatch.setattr("notetaker.dashboard.pick_folder_with_finder", lambda start=None: None)

    response = client.post("/settings/browse", data={"notes_dir": "~/old"})

    assert 'value="~/old"' in response.text
    assert "Save configuration" not in response.text


def test_browse_shows_the_error_inline_when_the_picker_fails(client, monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)

    def fail(start=None):
        raise RuntimeError("osascript not found — the Finder picker needs macOS.")

    monkeypatch.setattr("notetaker.dashboard.pick_folder_with_finder", fail)

    response = client.post("/settings/browse", data={"notes_dir": "~/old"})

    assert 'value="~/old"' in response.text
    assert "needs macOS" in response.text
