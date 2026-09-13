from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from notetaker import service
from notetaker.config import Config
from notetaker.dashboard import app


@pytest.fixture
def client():
    return TestClient(app)


def test_index_page_loads_and_wires_the_status_poller(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Notetaker" in response.text
    assert 'hx-get="/status"' in response.text
    assert 'hx-trigger="load, every 2s"' in response.text


def test_index_page_loads_htmx_from_cdn(client):
    response = client.get("/")

    assert "htmx.org@2.0.10" in response.text


def _config(tmp_path):
    return Config(tmp_path / "notes", "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY")


def test_status_shows_idle_state_with_start_form(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.check_and_salvage_orphan", lambda config, config_dir: None)
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: None)

    response = client.get("/status")

    assert response.status_code == 200
    assert "Not recording" in response.text
    assert 'hx-post="/start"' in response.text


def test_status_shows_recording_state_with_elapsed_and_transcript(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(999999, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.check_and_salvage_orphan", lambda config, config_dir: None)
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr(
        "notetaker.dashboard.service.get_live_transcript_preview", lambda info: "[00:00:03] hello\n"
    )

    response = client.get("/status")

    assert response.status_code == 200
    assert "Recording" in response.text
    assert "[00:00:03] hello" in response.text
    assert 'hx-post="/stop"' in response.text
    assert 'hx-post="/cancel"' in response.text


def test_status_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.get("/status")

    assert response.status_code == 200
    assert "not set up" in response.text
    assert "Run" in response.text


def test_status_shows_recovery_banner_when_orphan_salvaged(client, monkeypatch, tmp_path):
    salvaged_note_path = tmp_path / "notes" / "2026-09-10-standup.md"

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr(
        "notetaker.dashboard.service.check_and_salvage_orphan", lambda config, config_dir: salvaged_note_path
    )
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: None)

    response = client.get("/status")

    assert response.status_code == 200
    assert "Recovered a crashed session" in response.text
    assert "2026-09-10-standup.md" in response.text


def test_status_continues_when_salvage_raises_unexpectedly(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    def raise_disk_error(config, config_dir):
        raise OSError("disk full")

    monkeypatch.setattr("notetaker.dashboard.service.check_and_salvage_orphan", raise_disk_error)
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: None)

    response = client.get("/status")

    assert response.status_code == 200
    assert "Not recording" in response.text


def test_format_elapsed_under_an_hour():
    from notetaker.dashboard import _format_elapsed

    assert _format_elapsed(datetime(2026, 9, 11, 10, 0, 0), datetime(2026, 9, 11, 10, 5, 30)) == "05:30"


def test_format_elapsed_over_an_hour():
    from notetaker.dashboard import _format_elapsed

    assert _format_elapsed(datetime(2026, 9, 11, 10, 0, 0), datetime(2026, 9, 11, 11, 2, 3)) == "1:02:03"
