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

    def raise_unexpected_error(config, config_dir):
        # A type unrelated to any plausible narrower catch clause (OSError,
        # ServiceError) — proves the route's except clause is a genuinely
        # bare `except Exception`, not merely `except OSError`.
        raise RuntimeError("boom")

    monkeypatch.setattr("notetaker.dashboard.service.check_and_salvage_orphan", raise_unexpected_error)
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: None)

    response = client.get("/status")

    assert response.status_code == 200
    assert "Not recording" in response.text


def test_start_creates_session_and_shows_recording_state(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = {}

    def fake_start_session(title, config, config_dir, tags=None):
        calls["title"] = title
        calls["tags"] = tags
        return info

    monkeypatch.setattr("notetaker.dashboard.service.start_session", fake_start_session)
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr("notetaker.dashboard.service.get_live_transcript_preview", lambda info: "")

    response = client.post("/start", data={"title": "Standup", "tags": "project-x, planning"})

    assert response.status_code == 200
    assert "Recording" in response.text
    assert calls["title"] == "Standup"
    assert calls["tags"] == ["project-x", "planning"]


def test_start_with_no_tags_passes_empty_list(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = {}

    def fake_start_session(title, config, config_dir, tags=None):
        calls["tags"] = tags
        return info

    monkeypatch.setattr("notetaker.dashboard.service.start_session", fake_start_session)
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr("notetaker.dashboard.service.get_live_transcript_preview", lambda info: "")

    client.post("/start", data={"title": "Standup", "tags": ""})

    assert calls["tags"] == []


def test_start_shows_error_when_service_raises(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    def fail(title, config, config_dir, tags=None):
        raise service.ServiceError("BlackHole is not active. Run `notetaker init` for setup instructions.")

    monkeypatch.setattr("notetaker.dashboard.service.start_session", fail)

    response = client.post("/start", data={"title": "Standup", "tags": ""})

    assert response.status_code == 200
    assert "BlackHole is not active" in response.text
    assert "Not recording" in response.text


def test_start_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.post("/start", data={"title": "Standup", "tags": ""})

    assert response.status_code == 200
    assert "not set up" in response.text


def test_stop_saves_note_and_shows_success(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)
    note_path = tmp_path / "notes" / "2026-09-11-standup.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text("---\ntitle: Standup\n---\n\n## Summary\nAll good.\n")

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr(
        "notetaker.dashboard.service.stop_session", lambda info, config, config_dir: note_path
    )

    response = client.post("/stop")

    assert response.status_code == 200
    assert "Not recording" in response.text
    assert "2026-09-11-standup.md" in response.text


def test_stop_shows_summarization_failed_note(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)
    note_path = tmp_path / "notes" / "2026-09-11-standup.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text("---\ntitle: Standup\n---\n\n## Summary\nSummarization failed: network down\n")

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr(
        "notetaker.dashboard.service.stop_session", lambda info, config, config_dir: note_path
    )

    response = client.post("/stop")

    assert response.status_code == 200
    assert "summarization failed" in response.text.lower()


def test_stop_shows_error_when_no_active_session(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: None)

    response = client.post("/stop")

    assert response.status_code == 200
    assert "no active session" in response.text


def test_stop_shows_error_when_stop_session_raises(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr("notetaker.dashboard.service.get_live_transcript_preview", lambda info: "")

    def fail(info, config, config_dir):
        raise RuntimeError("disk full")

    monkeypatch.setattr("notetaker.dashboard.service.stop_session", fail)

    response = client.post("/stop")

    assert response.status_code == 200
    assert "disk full" in response.text
    assert "Recording" in response.text


def test_cancel_discards_session_and_shows_success(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    calls = []
    monkeypatch.setattr(
        "notetaker.dashboard.service.cancel_session", lambda info, config_dir: calls.append((info, config_dir))
    )

    response = client.post("/cancel")

    assert response.status_code == 200
    assert "Not recording" in response.text
    assert "cancelled" in response.text.lower()
    assert len(calls) == 1


def test_cancel_shows_error_when_no_active_session(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: None)

    response = client.post("/cancel")

    assert response.status_code == 200
    assert "no active session" in response.text


def test_cancel_shows_error_when_cancel_session_raises(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr("notetaker.dashboard.service.get_live_transcript_preview", lambda info: "")

    def fail(info, config_dir):
        raise RuntimeError("permission denied")

    monkeypatch.setattr("notetaker.dashboard.service.cancel_session", fail)

    response = client.post("/cancel")

    assert response.status_code == 200
    assert "permission denied" in response.text
    assert "Recording" in response.text


def test_format_elapsed_under_an_hour():
    from notetaker.dashboard import _format_elapsed

    assert _format_elapsed(datetime(2026, 9, 11, 10, 0, 0), datetime(2026, 9, 11, 10, 5, 30)) == "05:30"


def test_format_elapsed_over_an_hour():
    from notetaker.dashboard import _format_elapsed

    assert _format_elapsed(datetime(2026, 9, 11, 10, 0, 0), datetime(2026, 9, 11, 11, 2, 3)) == "1:02:03"
