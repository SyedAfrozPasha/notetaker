import json
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from notetaker.config import Config
from notetaker.notes import write_note
from notetaker.recorder import BlackHoleStatus
from notetaker.service import (
    ServiceError,
    SessionInfo,
    get_note_body,
    list_all_notes,
    pid_alive,
    read_active_session,
    session_file_path,
    start_session,
    stop_session,
)
from notetaker.summarizer import Summary


def test_session_file_path_is_under_config_dir(tmp_path):
    assert session_file_path(tmp_path) == tmp_path / "current_session.json"


def test_pid_alive_true_for_current_process():
    assert pid_alive(os.getpid()) is True


def test_pid_alive_false_for_nonexistent_pid():
    assert pid_alive(999999) is False


def _config(tmp_path):
    return Config(tmp_path / "notes", "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY")


def test_start_session_fails_when_blackhole_not_active(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.NOT_INSTALLED)
    with pytest.raises(ServiceError, match="BlackHole is not active"):
        start_session("Standup", _config(tmp_path), tmp_path)


def test_start_session_fails_when_already_running(monkeypatch, tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps({"pid": os.getpid(), "title": "x", "start_time": "2026-09-11T10:00:00", "session_dir": str(tmp_path)})
    )
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    with pytest.raises(ServiceError, match="already running"):
        start_session("Standup", _config(tmp_path), tmp_path)


def test_start_session_ignores_corrupt_session_file_and_starts_new_one(monkeypatch, tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text("{invalid json")
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.find_blackhole_device_index", lambda: 2)
    fake_proc = MagicMock(pid=54321)
    fake_proc.poll.return_value = None
    monkeypatch.setattr("notetaker.service.subprocess.Popen", lambda *a, **k: fake_proc)

    info = start_session("Weekly", _config(tmp_path), tmp_path)

    assert info.pid == 54321
    assert info.title == "Weekly"
    assert json.loads(session_file.read_text())["pid"] == 54321


def test_start_session_writes_session_file_and_spawns_recorder(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.find_blackhole_device_index", lambda: 2)
    fake_proc = MagicMock(pid=12345)
    fake_proc.poll.return_value = None
    captured_cmd = {}

    def fake_popen(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        return fake_proc

    monkeypatch.setattr("notetaker.service.subprocess.Popen", fake_popen)

    info = start_session("Standup", _config(tmp_path), tmp_path)

    assert info.pid == 12345
    assert info.title == "Standup"
    assert captured_cmd["cmd"] == [
        sys.executable, "-m", "notetaker.recorder", str(info.session_dir), "2", "tiny",
    ]
    session = json.loads((tmp_path / "current_session.json").read_text())
    assert session == {
        "pid": 12345,
        "title": "Standup",
        "start_time": info.start_time.isoformat(),
        "session_dir": str(info.session_dir),
    }


def test_start_session_fails_when_recorder_exits_immediately(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.find_blackhole_device_index", lambda: 2)
    fake_proc = MagicMock(pid=99999)
    fake_proc.poll.return_value = 0
    monkeypatch.setattr("notetaker.service.subprocess.Popen", lambda *a, **k: fake_proc)
    monkeypatch.setattr("notetaker.service.time.sleep", lambda s: None)

    with pytest.raises(ServiceError, match="recorder failed to start"):
        start_session("Standup", _config(tmp_path), tmp_path)

    assert not (tmp_path / "current_session.json").exists()


def test_read_active_session_fails_when_no_session_file(tmp_path):
    with pytest.raises(ServiceError, match="no active session"):
        read_active_session(tmp_path)


def test_read_active_session_fails_on_corrupt_file(tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text("{not valid json")
    with pytest.raises(ServiceError, match="corrupt or unreadable"):
        read_active_session(tmp_path)


def test_read_active_session_parses_valid_file(tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {"pid": 999999, "title": "Standup", "start_time": "2026-09-11T10:00:00", "session_dir": str(tmp_path)}
        )
    )
    info = read_active_session(tmp_path)
    assert info == SessionInfo(999999, "Standup", datetime(2026, 9, 11, 10, 0), tmp_path)


def _session_info(session_dir):
    return SessionInfo(pid=999999, title="Standup", start_time=datetime(2026, 9, 11, 10, 0), session_dir=session_dir)


def test_stop_session_skips_provider_call_when_transcript_empty(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    session_file = tmp_path / "current_session.json"
    session_file.write_text("{}")  # presence is all stop_session checks for cleanup
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)

    def fail_if_called(*a, **k):
        raise AssertionError("get_provider should not be called for an empty transcript")

    monkeypatch.setattr("notetaker.service.get_provider", fail_if_called)
    monkeypatch.setattr("notetaker.service.summarize_transcript", fail_if_called)

    note_path = stop_session(_session_info(session_dir), Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path)

    assert "No audio was captured" in note_path.read_text()
    assert not session_dir.exists()
    assert not session_file.exists()


def test_stop_session_salvages_transcript_and_writes_note(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n[00:00:07] world\n")
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="summary text", action_items=["a"], tags=["t"]),
    )

    note_path = stop_session(_session_info(session_dir), Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path)

    assert "summary text" in note_path.read_text()
    assert not session_dir.exists()


def test_stop_session_saves_note_with_error_when_summarization_fails(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n")
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())

    def raise_error(transcript, provider):
        raise RuntimeError("network down")

    monkeypatch.setattr("notetaker.service.summarize_transcript", raise_error)

    note_path = stop_session(_session_info(session_dir), Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path)

    text = note_path.read_text()
    assert "Summarization failed" in text
    assert "hello" in text


def test_list_all_notes_returns_notes_sorted_by_date(tmp_path):
    notes_dir = tmp_path / "notes"
    write_note(notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], ["proj"]), [])
    metas = list_all_notes(Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"))
    assert len(metas) == 1
    assert metas[0].title == "Standup"


def test_get_note_body_returns_body_text(tmp_path):
    notes_dir = tmp_path / "notes"
    write_note(notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("Summary text", [], []), ["[00:00:01] hi"])
    body = get_note_body(Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), "2026-09-11-standup")
    assert "Summary text" in body


def test_get_note_body_raises_for_missing_note(tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    with pytest.raises(ServiceError, match="no note found"):
        get_note_body(Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), "nonexistent")


def test_initialize_config_writes_when_missing(tmp_path):
    from notetaker.service import initialize_config
    config_path = tmp_path / "config.yaml"
    assert initialize_config(config_path) is True
    assert config_path.exists()


def test_initialize_config_skips_when_present(tmp_path):
    from notetaker.service import initialize_config
    config_path = tmp_path / "config.yaml"
    config_path.write_text("existing: true\n")
    assert initialize_config(config_path) is False
    assert config_path.read_text() == "existing: true\n"


def test_check_setup_reports_blackhole_and_ready_claude_provider(monkeypatch, tmp_path):
    from notetaker.service import SetupStatus, check_setup
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    status = check_setup(_config(tmp_path))
    assert status == SetupStatus(blackhole=BlackHoleStatus.ACTIVE, provider_ready=True, provider_problems=[])


def test_check_setup_reports_missing_claude_api_key(monkeypatch, tmp_path):
    from notetaker.service import check_setup
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status = check_setup(_config(tmp_path))
    assert status.provider_ready is False
    assert "ANTHROPIC_API_KEY is not set" in status.provider_problems[0]


def test_check_setup_reports_apple_local_problems(monkeypatch, tmp_path):
    from notetaker.service import check_setup
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.check_apple_local_preflight", lambda: ["apfel is not installed"])
    status = check_setup(Config(tmp_path, "tiny", "apple_local", "apple-foundationmodel", "UNUSED"))
    assert status.provider_ready is False
    assert status.provider_problems == ["apfel is not installed"]


def test_ensure_whisper_model_loads_transcriber(monkeypatch, tmp_path):
    from notetaker.service import ensure_whisper_model
    calls = []
    monkeypatch.setattr("notetaker.service.Transcriber", lambda model: calls.append(model))
    ensure_whisper_model(_config(tmp_path))
    assert calls == ["tiny"]
