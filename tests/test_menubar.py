from datetime import datetime
from pathlib import Path

import pytest

from notetaker import service
from notetaker.config import ConfigError
from notetaker.menubar import (
    NotetakerMenuBarApp,
    auto_generated_title,
    format_elapsed,
    menu_bar_title,
    note_summarization_failed,
    toggle_item_title,
)
from notetaker.service import SessionInfo


@pytest.fixture(autouse=True)
def _block_real_rumps_dialogs(monkeypatch):
    """Fails fast if a test reaches real rumps.alert/rumps.notification
    without mocking them first — the alternative is a silent hang on a real
    modal dialog (NSAlert.runModal()).
    """

    def _fail(*args, **kwargs):
        raise AssertionError(
            "A test reached real rumps.alert/rumps.notification without mocking it. "
            "Mock notetaker.menubar.rumps.alert / .notification explicitly."
        )

    monkeypatch.setattr("notetaker.menubar.rumps.alert", _fail)
    monkeypatch.setattr("notetaker.menubar.rumps.notification", _fail)


def test_format_elapsed_under_an_hour():
    start = datetime(2026, 9, 16, 10, 0, 0)
    now = datetime(2026, 9, 16, 10, 5, 23)
    assert format_elapsed(start, now) == "05:23"


def test_format_elapsed_over_an_hour():
    start = datetime(2026, 9, 16, 10, 0, 0)
    now = datetime(2026, 9, 16, 11, 5, 23)
    assert format_elapsed(start, now) == "1:05:23"


def test_menu_bar_title_idle():
    assert menu_bar_title(None, datetime(2026, 9, 16, 10, 0, 0)) == "Notetaker"


def test_menu_bar_title_recording():
    info = SessionInfo(123, "Standup", datetime(2026, 9, 16, 10, 0, 0), Path("/tmp"))
    now = datetime(2026, 9, 16, 10, 5, 23)
    assert menu_bar_title(info, now) == "⏺ 05:23"


def test_toggle_item_title_idle():
    assert toggle_item_title(None) == "Start Recording"


def test_toggle_item_title_recording():
    info = SessionInfo(123, "Standup", datetime(2026, 9, 16, 10, 0, 0), Path("/tmp"))
    assert toggle_item_title(info) == "Stop Recording"


def test_auto_generated_title():
    now = datetime(2026, 9, 16, 14, 30, 0)
    assert auto_generated_title(now) == "Meeting 2026-09-16 14:30"


def test_note_summarization_failed_true(tmp_path):
    note_path = tmp_path / "note.md"
    note_path.write_text("## Summary\nSummarization failed: network down\n")
    assert note_summarization_failed(note_path) is True


def test_note_summarization_failed_false(tmp_path):
    note_path = tmp_path / "note.md"
    note_path.write_text("## Summary\nEverything went great.\n")
    assert note_summarization_failed(note_path) is False


@pytest.fixture
def app():
    instance = NotetakerMenuBarApp()
    yield instance
    instance._timer.stop()


def _config(tmp_path):
    from notetaker.config import Config

    return Config(tmp_path / "notes", "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY")


def test_on_tick_shows_warning_when_config_missing(app, monkeypatch):
    def raise_config_error():
        raise ConfigError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.menubar.load_config", raise_config_error)

    app._on_tick(None)

    assert "⚠️" in app.title


def test_on_tick_updates_title_when_idle(app, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.menubar.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.menubar.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.menubar.service.check_and_salvage_orphan", lambda config, config_dir: None)
    monkeypatch.setattr("notetaker.menubar.service.get_current_session_status", lambda config_dir: None)

    app._on_tick(None)

    assert app.title == "Notetaker"
    assert app._toggle_item.title == "Start Recording"


def test_on_tick_notifies_when_orphan_salvaged(app, monkeypatch, tmp_path):
    salvaged_path = tmp_path / "notes" / "2026-09-15-standup.md"
    monkeypatch.setattr("notetaker.menubar.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.menubar.CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        "notetaker.menubar.service.check_and_salvage_orphan", lambda config, config_dir: salvaged_path
    )
    monkeypatch.setattr("notetaker.menubar.service.get_current_session_status", lambda config_dir: None)
    calls = []
    monkeypatch.setattr(
        "notetaker.menubar.rumps.notification",
        lambda title, subtitle, message: calls.append((title, subtitle, message)),
    )

    app._on_tick(None)

    assert len(calls) == 1
    assert calls[0][0] == "Recovered a crashed session"


def test_on_toggle_shows_alert_when_config_missing(app, monkeypatch):
    def raise_config_error():
        raise ConfigError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.menubar.load_config", raise_config_error)
    calls = []
    monkeypatch.setattr(
        "notetaker.menubar.rumps.alert", lambda title, message: calls.append((title, message))
    )

    app._on_toggle(None)

    assert len(calls) == 1
    assert calls[0][0] == "Notetaker is not set up"


def test_on_toggle_starts_when_idle(app, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.menubar.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.menubar.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.menubar.service.get_current_session_status", lambda config_dir: None)
    calls = []
    monkeypatch.setattr(
        "notetaker.menubar.service.start_session",
        lambda title, config, config_dir: calls.append(title),
    )

    app._on_toggle(None)

    assert len(calls) == 1
    assert calls[0].startswith("Meeting ")


def test_on_toggle_shows_alert_when_start_fails(app, monkeypatch, tmp_path):
    from notetaker.service import ServiceError

    monkeypatch.setattr("notetaker.menubar.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.menubar.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.menubar.service.get_current_session_status", lambda config_dir: None)

    def fail(title, config, config_dir):
        raise ServiceError("BlackHole is not active.")

    monkeypatch.setattr("notetaker.menubar.service.start_session", fail)
    calls = []
    monkeypatch.setattr(
        "notetaker.menubar.rumps.alert", lambda title, message: calls.append((title, message))
    )

    app._on_toggle(None)

    assert len(calls) == 1
    assert "BlackHole is not active." in calls[0][1]


def test_on_toggle_shows_alert_when_start_raises_unexpected_error(app, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.menubar.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.menubar.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.menubar.service.get_current_session_status", lambda config_dir: None)

    def fail(title, config, config_dir):
        raise RuntimeError("disk full")

    monkeypatch.setattr("notetaker.menubar.service.start_session", fail)
    calls = []
    monkeypatch.setattr(
        "notetaker.menubar.rumps.alert", lambda title, message: calls.append((title, message))
    )

    app._on_toggle(None)

    assert len(calls) == 1
    assert calls[0][0] == "Could not start recording"
    assert "disk full" in calls[0][1]


def test_on_toggle_shows_alert_when_stop_fails(app, monkeypatch, tmp_path):
    from datetime import datetime

    info = service.SessionInfo(123, "Standup", datetime(2026, 9, 16, 10, 0, 0), tmp_path)

    monkeypatch.setattr("notetaker.menubar.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.menubar.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.menubar.service.get_current_session_status", lambda config_dir: info)

    def fail(info, config, config_dir):
        raise RuntimeError("disk full")

    monkeypatch.setattr("notetaker.menubar.service.stop_session", fail)
    calls = []
    monkeypatch.setattr(
        "notetaker.menubar.rumps.alert", lambda title, message: calls.append((title, message))
    )

    app._on_toggle(None)

    assert len(calls) == 1
    assert calls[0][0] == "Could not save the recording"
    assert "disk full" in calls[0][1]


def test_on_toggle_stops_and_notifies_when_recording(app, monkeypatch, tmp_path):
    from datetime import datetime

    info = service.SessionInfo(123, "Standup", datetime(2026, 9, 16, 10, 0, 0), tmp_path)
    note_path = tmp_path / "notes" / "2026-09-16-standup.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text("## Summary\nAll good.\n")

    monkeypatch.setattr("notetaker.menubar.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.menubar.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.menubar.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr(
        "notetaker.menubar.service.stop_session", lambda info, config, config_dir: note_path
    )
    calls = []
    monkeypatch.setattr(
        "notetaker.menubar.rumps.notification",
        lambda title, subtitle, message: calls.append((title, subtitle, message)),
    )

    app._on_toggle(None)

    assert len(calls) == 1
    assert calls[0] == ("Recording saved", "", "2026-09-16-standup.md")


def test_on_toggle_notifies_summarization_failure_variant(app, monkeypatch, tmp_path):
    from datetime import datetime

    info = service.SessionInfo(123, "Standup", datetime(2026, 9, 16, 10, 0, 0), tmp_path)
    note_path = tmp_path / "notes" / "2026-09-16-standup.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text("## Summary\nSummarization failed: network down\n")

    monkeypatch.setattr("notetaker.menubar.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.menubar.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.menubar.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr(
        "notetaker.menubar.service.stop_session", lambda info, config, config_dir: note_path
    )
    calls = []
    monkeypatch.setattr(
        "notetaker.menubar.rumps.notification",
        lambda title, subtitle, message: calls.append((title, subtitle, message)),
    )

    app._on_toggle(None)

    assert calls[0][1] == "Summarization failed"
