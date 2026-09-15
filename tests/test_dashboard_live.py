"""Live status page: transcript rows, freshness, pinned start warnings."""

import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from notetaker.config import Config
from notetaker.dashboard import app, count_segments, parse_transcript_rows, seconds_since_transcript_update
from notetaker.service import SessionInfo


@pytest.fixture
def client():
    return TestClient(app, base_url="http://127.0.0.1")


def _config(tmp_path):
    return Config(tmp_path / "notes", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY")


def _wire(monkeypatch, tmp_path, config):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: config)
    monkeypatch.setattr("notetaker.dashboard.service.check_and_salvage_orphan", lambda config, config_dir: None)


def test_parse_transcript_rows_splits_speaker_lines():
    rows = parse_transcript_rows("[00:00:03] Me: hello there\n[00:00:07] Others: hi back\n")

    assert [(r.time, r.speaker, r.text) for r in rows] == [
        ("00:00:03", "me", "hello there"),
        ("00:00:07", "others", "hi back"),
    ]


def test_parse_transcript_rows_handles_mono_lines_and_status_lines():
    text = "[00:00:03] plain mono text\n[warning] no meeting audio detected\n\n[note] microphone disconnected\n"
    rows = parse_transcript_rows(text)

    assert [(r.time, r.speaker, r.text) for r in rows] == [
        ("00:00:03", "", "plain mono text"),
        ("", "status", "no meeting audio detected"),
        ("", "status", "microphone disconnected"),
    ]


def test_parse_transcript_rows_marks_failed_chunk_lines_as_status():
    rows = parse_transcript_rows("[transcription failed for chunk 3: boom]\n")

    assert [(r.speaker, r.text) for r in rows] == [("status", "transcription failed for chunk 3: boom")]


def test_parse_transcript_rows_keeps_unrecognised_lines_as_plain_text():
    rows = parse_transcript_rows("something odd\n")

    assert [(r.time, r.speaker, r.text) for r in rows] == [("", "", "something odd")]


def test_count_segments_ignores_status_rows():
    rows = parse_transcript_rows("[00:00:03] Me: a\n[warning] b\n[00:00:09] Others: c\n")

    assert count_segments(rows) == 2


def test_seconds_since_transcript_update_returns_none_when_no_transcript(tmp_path):
    assert seconds_since_transcript_update(tmp_path / "missing.txt", now=1000.0) is None


def test_seconds_since_transcript_update_uses_file_mtime(tmp_path):
    path = tmp_path / "transcript.txt"
    path.write_text("[00:00:03] hi\n")
    os.utime(path, (990.0, 990.0))

    assert seconds_since_transcript_update(path, now=1000.0) == 10


def test_status_renders_transcript_as_speaker_rows_with_freshness(client, monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, _config(tmp_path))
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] Me: hello\n[00:00:07] Others: hi\n[warning] echo\n")
    info = SessionInfo(
        pid=1, title="Standup", start_time=datetime.now(), session_dir=session_dir, tags=["proj", "weekly"]
    )
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)

    response = client.get("/status")

    assert response.status_code == 200
    assert "Standup" in response.text
    assert 'class="row me"' in response.text
    assert 'class="row others"' in response.text
    assert 'class="row status"' in response.text
    assert "00:00:07" in response.text
    assert "2 segments" in response.text
    assert "last transcribed" in response.text
    assert 'class="tag">proj<' in response.text


def test_status_shows_waiting_message_before_first_transcript(client, monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, _config(tmp_path))
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True)
    info = SessionInfo(pid=1, title="Standup", start_time=datetime.now(), session_dir=session_dir)
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)

    response = client.get("/status")

    assert "Waiting for the first" in response.text
    assert "last transcribed" not in response.text


def test_start_warnings_stay_pinned_across_status_polls_until_session_ends(client, monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, _config(tmp_path))
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True)
    info = SessionInfo(
        pid=1,
        title="Standup",
        start_time=datetime.now(),
        session_dir=session_dir,
        warnings=["Warning: microphone stopped delivering audio"],
    )
    state = {"info": None}
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: state["info"])

    def fake_start(title, config, config_dir, tags=None):
        state["info"] = info
        return info

    monkeypatch.setattr("notetaker.dashboard.service.start_session", fake_start)

    client.post("/start", data={"title": "Standup"})
    polled = client.get("/status")
    assert "microphone stopped delivering audio" in polled.text

    state["info"] = None  # session ended elsewhere (CLI stop, crash)
    idle = client.get("/status")
    assert "microphone stopped delivering audio" not in idle.text

    state["info"] = info  # same dir reappears without a /start: nothing pinned any more
    again = client.get("/status")
    assert "microphone stopped delivering audio" not in again.text
