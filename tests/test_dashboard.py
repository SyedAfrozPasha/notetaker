from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from notetaker import service
from notetaker.config import Config
from notetaker.dashboard import app


@pytest.fixture
def client():
    # base_url matters: TrustedHostMiddleware only allows 127.0.0.1/localhost,
    # and TestClient's default base_url ("http://testserver") would send a
    # Host header that gets rejected.
    return TestClient(app, base_url="http://127.0.0.1")


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
    # id + hx-preserve keep htmx from tearing down (and re-creating, losing
    # any typed value/focus in) this form on the outer status div's own
    # 2-second poll cycle — htmx's swap algorithm matches on this exact
    # id+hx-preserve pairing to keep the existing DOM node in place.
    assert 'id="start-form"' in response.text
    assert 'hx-preserve="true"' in response.text


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


def test_start_shows_error_and_recording_state_when_a_session_is_already_active(client, monkeypatch, tmp_path):
    # start_session's own "already running"/"currently being stopped" guards
    # are exactly the cases where a session IS (or may be) active — the
    # error branch must re-derive status, not hardcode idle, or the page
    # would show a Start form for a session that's still recording.
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr("notetaker.dashboard.service.get_live_transcript_preview", lambda info: "")

    def fail(title, config, config_dir, tags=None):
        raise service.ServiceError("a session is already running. Run `notetaker stop` first.")

    monkeypatch.setattr("notetaker.dashboard.service.start_session", fail)

    response = client.post("/start", data={"title": "Standup", "tags": ""})

    assert response.status_code == 200
    assert "already running" in response.text
    # "Recording" alone is a vacuous check here — the idle state's own
    # "Start Recording" button label contains it too. Assert the
    # recording-state markup specifically, and that the idle Start form is
    # NOT present, so this genuinely fails if the branch reverts to
    # hardcoding {"recording": False, ...}.
    assert 'hx-post="/stop"' in response.text
    assert "Not recording" not in response.text
    assert 'id="start-form"' not in response.text


def test_start_shows_error_when_unexpected_exception_raised(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: None)

    def fail(title, config, config_dir, tags=None):
        raise OSError("recorder failed to start — see recorder.log for details")

    monkeypatch.setattr("notetaker.dashboard.service.start_session", fail)

    response = client.post("/start", data={"title": "Standup", "tags": ""})

    assert response.status_code == 200
    assert "recorder failed to start" in response.text


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
    # "Recording" alone is vacuous — the idle state's "Start Recording"
    # button contains it too. Assert the recording-state markup
    # specifically, proving _status_context was re-derived rather than
    # hardcoded to idle.
    assert 'hx-post="/stop"' in response.text
    assert "Not recording" not in response.text


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
    # "Recording" alone is vacuous — the idle state's "Start Recording"
    # button contains it too. Assert the recording-state markup
    # specifically, proving _status_context was re-derived rather than
    # hardcoded to idle.
    assert 'hx-post="/stop"' in response.text
    assert "Not recording" not in response.text


def test_format_elapsed_under_an_hour():
    from notetaker.dashboard import _format_elapsed

    assert _format_elapsed(datetime(2026, 9, 11, 10, 0, 0), datetime(2026, 9, 11, 10, 5, 30)) == "05:30"


def test_format_elapsed_over_an_hour():
    from notetaker.dashboard import _format_elapsed

    assert _format_elapsed(datetime(2026, 9, 11, 10, 0, 0), datetime(2026, 9, 11, 11, 2, 3)) == "1:02:03"


def test_rejects_post_with_cross_site_fetch_metadata(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: None)

    response = client.post("/cancel", headers={"Sec-Fetch-Site": "cross-site"})

    assert response.status_code == 403


def test_allows_post_with_no_fetch_metadata(client, monkeypatch, tmp_path):
    # Non-browser local tools (curl, scripts, this test's own client) don't
    # send Sec-Fetch-Site at all — only an explicit "cross-site" is rejected.
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: None)

    response = client.post("/cancel")

    assert response.status_code == 200


def test_rejects_request_with_untrusted_host_header(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    untrusted_client = TestClient(app, base_url="http://evil.example")

    response = untrusted_client.get("/status")

    assert response.status_code == 400


def test_notes_list_shows_all_notes_when_no_filters(client, monkeypatch, tmp_path):
    from notetaker.notes import NoteMeta

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    metas = [
        NoteMeta("2026-09-11-standup", "Standup", datetime(2026, 9, 11, 10, 0), 5, ["proj"], tmp_path / "2026-09-11-standup.md"),
    ]
    monkeypatch.setattr("notetaker.dashboard.service.search_notes", lambda config, **kw: metas)

    response = client.get("/notes")

    assert response.status_code == 200
    assert "Standup" in response.text
    assert "/notes/2026-09-11-standup" in response.text


def test_notes_list_filters_by_query(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = {}

    def fake_search(config, **kw):
        calls.update(kw)
        return []

    monkeypatch.setattr("notetaker.dashboard.service.search_notes", fake_search)

    client.get("/notes", params={"query": "roadmap"})

    assert calls["query"] == "roadmap"
    assert calls["tag"] is None


def test_notes_list_filters_by_tag(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = {}

    def fake_search(config, **kw):
        calls.update(kw)
        return []

    monkeypatch.setattr("notetaker.dashboard.service.search_notes", fake_search)

    client.get("/notes", params={"tag": "project-x"})

    assert calls["tag"] == "project-x"
    assert calls["query"] is None


def test_notes_list_filters_by_date_range(client, monkeypatch, tmp_path):
    from datetime import date

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = {}

    def fake_search(config, **kw):
        calls.update(kw)
        return []

    monkeypatch.setattr("notetaker.dashboard.service.search_notes", fake_search)

    client.get("/notes", params={"start_date": "2026-09-10", "end_date": "2026-09-15"})

    assert calls["start_date"] == date(2026, 9, 10)
    assert calls["end_date"] == date(2026, 9, 15)


def test_notes_list_shows_no_notes_message_when_empty(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.search_notes", lambda config, **kw: [])

    response = client.get("/notes")

    assert response.status_code == 200
    assert "No notes found" in response.text


def test_notes_list_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.get("/notes")

    assert response.status_code == 200
    assert "not set up" in response.text


def test_notes_list_shows_error_when_search_notes_raises(client, monkeypatch, tmp_path):
    # search_notes can raise a raw (non-ServiceError) exception, e.g. an
    # unreadable note file surfacing a PermissionError while scanning
    # summaries for a text match — this must render an inline error, not
    # an unhandled 500, and must preserve the submitted filter values.
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    def fail(config, **kw):
        raise PermissionError("[Errno 13] Permission denied")

    monkeypatch.setattr("notetaker.dashboard.service.search_notes", fail)

    response = client.get("/notes", params={"query": "roadmap"})

    assert response.status_code == 200
    assert "Permission denied" in response.text
    assert 'value="roadmap"' in response.text


def test_base_layout_has_notes_nav_link(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/notes"' in response.text


def test_notes_detail_shows_structured_fields(client, monkeypatch, tmp_path):
    from notetaker.service import NoteDetail

    detail = NoteDetail(
        note_id="2026-09-11-standup", title="Standup", date=datetime(2026, 9, 11, 10, 0),
        duration_minutes=18, tags=["project-x"], summary_text="We discussed X.",
        action_items=["Follow up with Bob"], transcript="[00:00:03] hello",
        path=tmp_path / "2026-09-11-standup.md",
    )
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", lambda config, note_id: detail)
    monkeypatch.setattr("notetaker.dashboard.service.get_note_body", lambda config, note_id: "raw body text")

    response = client.get("/notes/2026-09-11-standup")

    assert response.status_code == 200
    assert "Standup" in response.text
    assert "We discussed X." in response.text
    assert "Follow up with Bob" in response.text
    assert "[00:00:03] hello" in response.text
    assert "project-x" in response.text


def test_notes_detail_shows_none_for_empty_action_items(client, monkeypatch, tmp_path):
    from notetaker.service import NoteDetail

    detail = NoteDetail(
        note_id="2026-09-11-standup", title="Standup", date=datetime(2026, 9, 11, 10, 0),
        duration_minutes=5, tags=[], summary_text="s", action_items=[], transcript="",
        path=tmp_path / "2026-09-11-standup.md",
    )
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", lambda config, note_id: detail)
    monkeypatch.setattr("notetaker.dashboard.service.get_note_body", lambda config, note_id: "")

    response = client.get("/notes/2026-09-11-standup")

    assert response.status_code == 200
    assert "(none)" in response.text


def test_notes_detail_shows_not_found_for_missing_note(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    def fail(config, note_id):
        raise service.ServiceError(f"no note found with id '{note_id}'.")

    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", fail)

    response = client.get("/notes/nonexistent")

    assert response.status_code == 200
    assert "no note found" in response.text


def test_notes_detail_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.get("/notes/2026-09-11-standup")

    assert response.status_code == 200
    assert "not set up" in response.text


def test_notes_detail_has_copy_summary_button(client, monkeypatch, tmp_path):
    from notetaker.service import NoteDetail

    detail = NoteDetail(
        note_id="2026-09-11-standup", title="Standup", date=datetime(2026, 9, 11, 10, 0),
        duration_minutes=5, tags=[], summary_text="We discussed X.", action_items=[], transcript="",
        path=tmp_path / "2026-09-11-standup.md",
    )
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", lambda config, note_id: detail)
    monkeypatch.setattr("notetaker.dashboard.service.get_note_body", lambda config, note_id: "raw")

    response = client.get("/notes/2026-09-11-standup")

    assert 'id="summary-text"' in response.text
    assert "navigator.clipboard.writeText" in response.text


def test_notes_detail_has_copy_full_markdown_button(client, monkeypatch, tmp_path):
    from notetaker.service import NoteDetail

    detail = NoteDetail(
        note_id="2026-09-11-standup", title="Standup", date=datetime(2026, 9, 11, 10, 0),
        duration_minutes=5, tags=[], summary_text="s", action_items=[], transcript="",
        path=tmp_path / "2026-09-11-standup.md",
    )
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", lambda config, note_id: detail)
    monkeypatch.setattr("notetaker.dashboard.service.get_note_body", lambda config, note_id: "the raw markdown body")

    response = client.get("/notes/2026-09-11-standup")

    assert 'id="full-markdown"' in response.text
    assert "the raw markdown body" in response.text


def test_notes_edit_form_prefills_current_fields(client, monkeypatch, tmp_path):
    from notetaker.service import NoteDetail

    detail = NoteDetail(
        note_id="2026-09-11-standup", title="Standup", date=datetime(2026, 9, 11, 10, 0),
        duration_minutes=5, tags=["proj"], summary_text="old summary",
        action_items=["item one", "item two"], transcript="t",
        path=tmp_path / "2026-09-11-standup.md",
    )
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", lambda config, note_id: detail)

    response = client.get("/notes/2026-09-11-standup/edit")

    assert response.status_code == 200
    assert 'value="Standup"' in response.text
    assert "old summary" in response.text
    assert "item one" in response.text
    assert "item two" in response.text
    assert "proj" in response.text


def test_notes_edit_form_shows_not_found_for_missing_note(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    def fail(config, note_id):
        raise service.ServiceError(f"no note found with id '{note_id}'.")

    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", fail)

    response = client.get("/notes/nonexistent/edit")

    assert response.status_code == 200
    assert "no note found" in response.text


def test_notes_edit_form_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.get("/notes/2026-09-11-standup/edit")

    assert response.status_code == 200
    assert "not set up" in response.text


def test_notes_edit_submit_updates_note_and_redirects(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = {}

    def fake_update_note(config, note_id, *, title, tags, summary_text, action_items):
        calls["note_id"] = note_id
        calls["title"] = title
        calls["tags"] = tags
        calls["summary_text"] = summary_text
        calls["action_items"] = action_items
        return tmp_path / f"{note_id}.md"

    monkeypatch.setattr("notetaker.dashboard.service.update_note", fake_update_note)

    response = client.post(
        "/notes/2026-09-11-standup/edit",
        data={
            "title": "New Title",
            "tags": "proj, planning",
            "summary_text": "new summary",
            "action_items": "item one\nitem two",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/notes/2026-09-11-standup"
    assert calls["note_id"] == "2026-09-11-standup"
    assert calls["title"] == "New Title"
    assert calls["tags"] == ["proj", "planning"]
    assert calls["summary_text"] == "new summary"
    assert calls["action_items"] == ["item one", "item two"]


def test_notes_edit_submit_shows_error_on_failure(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    def fail(config, note_id, *, title, tags, summary_text, action_items):
        raise RuntimeError("disk full")

    monkeypatch.setattr("notetaker.dashboard.service.update_note", fail)
    # get_note_detail must NOT be called on this path — the failure re-render
    # uses what the user just submitted, not a re-fetch from disk (a re-fetch
    # would show the OLD, unsaved-edit content and silently discard the
    # user's in-progress typing).
    def fail_if_called(config, note_id):
        raise AssertionError("get_note_detail should not be called on a failed save")

    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", fail_if_called)

    response = client.post(
        "/notes/2026-09-11-standup/edit",
        data={
            "title": "New Title The User Just Typed",
            "tags": "proj, planning",
            "summary_text": "new summary the user just typed",
            "action_items": "new item one\nnew item two",
        },
    )

    assert response.status_code == 200
    assert "disk full" in response.text
    # The edit form must still be usable, pre-filled with exactly what the
    # user just submitted — proving their in-progress edit wasn't discarded.
    assert 'value="New Title The User Just Typed"' in response.text
    assert "new summary the user just typed" in response.text
    assert "new item one" in response.text
    assert "new item two" in response.text
    assert "proj, planning" in response.text


def test_notes_delete_removes_note_and_redirects(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = []
    monkeypatch.setattr(
        "notetaker.dashboard.service.delete_note", lambda config, note_id: calls.append(note_id)
    )

    response = client.post("/notes/2026-09-11-standup/delete")

    assert response.status_code == 200
    assert response.headers["hx-redirect"] == "/notes"
    assert calls == ["2026-09-11-standup"]


def test_notes_delete_shows_error_for_missing_note(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    def fail(config, note_id):
        raise service.ServiceError(f"no note found with id '{note_id}'.")

    monkeypatch.setattr("notetaker.dashboard.service.delete_note", fail)

    response = client.post("/notes/nonexistent/delete")

    assert response.status_code == 200
    assert "no note found" in response.text


def test_notes_delete_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.post("/notes/2026-09-11-standup/delete")

    assert response.status_code == 200
    assert "not set up" in response.text


def test_notes_resummarize_updates_summary_and_redirects(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = []
    monkeypatch.setattr(
        "notetaker.dashboard.service.resummarize_note",
        lambda config, note_id: calls.append(note_id) or (tmp_path / f"{note_id}.md"),
    )

    response = client.post("/notes/2026-09-11-standup/resummarize", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/notes/2026-09-11-standup"
    assert calls == ["2026-09-11-standup"]


def test_notes_resummarize_shows_error_and_detail_on_failure(client, monkeypatch, tmp_path):
    from notetaker.service import NoteDetail

    detail = NoteDetail(
        note_id="2026-09-11-standup", title="Standup", date=datetime(2026, 9, 11, 10, 0),
        duration_minutes=5, tags=[], summary_text="old summary", action_items=[], transcript="t",
        path=tmp_path / "2026-09-11-standup.md",
    )
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", lambda config, note_id: detail)
    monkeypatch.setattr("notetaker.dashboard.service.get_note_body", lambda config, note_id: "raw")

    def fail(config, note_id):
        raise service.ServiceError("resummarization failed: network down")

    monkeypatch.setattr("notetaker.dashboard.service.resummarize_note", fail)

    response = client.post("/notes/2026-09-11-standup/resummarize")

    assert response.status_code == 200
    assert "network down" in response.text
    assert "old summary" in response.text


def test_notes_resummarize_shows_not_found_when_note_missing_entirely(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    def fail(config, note_id):
        raise service.ServiceError(f"no note found with id '{note_id}'.")

    monkeypatch.setattr("notetaker.dashboard.service.resummarize_note", fail)
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", fail)

    response = client.post("/notes/nonexistent/resummarize")

    assert response.status_code == 200
    assert "no note found" in response.text


def test_notes_resummarize_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.post("/notes/2026-09-11-standup/resummarize")

    assert response.status_code == 200
    assert "not set up" in response.text


def test_settings_page_shows_current_config_and_masked_credential(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr(
        "notetaker.dashboard.service.get_masked_provider_credential", lambda config: "sk-ant••••1234"
    )

    response = client.get("/settings")

    assert response.status_code == 200
    assert "sk-ant••••1234" in response.text
    assert 'value="tiny"' in response.text
    assert '<option value="claude" selected>' in response.text


def test_settings_page_shows_not_set_when_no_credential(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_masked_provider_credential", lambda config: None)

    response = client.get("/settings")

    assert response.status_code == 200
    assert "Not set" in response.text


def test_settings_page_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.get("/settings")

    assert response.status_code == 200
    assert "not set up" in response.text


def test_base_layout_has_settings_nav_link(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/settings"' in response.text
