import json
import os
import signal
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from notetaker.config import Config
from notetaker.notes import parse_note_meta, write_note
from notetaker.recorder import BlackHoleStatus
from notetaker.service import (
    ServiceError,
    SessionInfo,
    _claim_session_file,
    cancel_session,
    check_and_salvage_orphan,
    get_current_session_status,
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


def test_start_session_refuses_while_a_stop_is_in_progress(monkeypatch, tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.with_suffix(".salvaging").write_text("{}")
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    with pytest.raises(ServiceError, match="currently being stopped"):
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
        "tags": [],
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


def test_start_session_persists_tags(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.find_blackhole_device_index", lambda: 2)
    fake_proc = MagicMock(pid=555)
    fake_proc.poll.return_value = None
    monkeypatch.setattr("notetaker.service.subprocess.Popen", lambda *a, **k: fake_proc)

    info = start_session("Standup", _config(tmp_path), tmp_path, tags=["project-x", "planning"])

    assert info.tags == ["project-x", "planning"]
    raw = json.loads((tmp_path / "current_session.json").read_text())
    assert raw["tags"] == ["project-x", "planning"]


def test_start_session_defaults_to_no_tags(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.find_blackhole_device_index", lambda: 2)
    fake_proc = MagicMock(pid=556)
    fake_proc.poll.return_value = None
    monkeypatch.setattr("notetaker.service.subprocess.Popen", lambda *a, **k: fake_proc)

    info = start_session("Standup", _config(tmp_path), tmp_path)

    assert info.tags == []


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


def test_read_active_session_parses_tags(tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {
                "pid": 999999,
                "title": "Standup",
                "start_time": "2026-09-11T10:00:00",
                "session_dir": str(tmp_path),
                "tags": ["proj"],
            }
        )
    )
    info = read_active_session(tmp_path)
    assert info.tags == ["proj"]


def test_read_active_session_defaults_tags_when_absent(tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {"pid": 999999, "title": "Standup", "start_time": "2026-09-11T10:00:00", "session_dir": str(tmp_path)}
        )
    )
    info = read_active_session(tmp_path)
    assert info.tags == []


def _session_info(session_dir):
    return SessionInfo(pid=999999, title="Standup", start_time=datetime(2026, 9, 11, 10, 0), session_dir=session_dir)


def test_stop_session_merges_session_tags_before_provider_tags(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n")
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="s", action_items=[], tags=["ai-tag"]),
    )
    info = SessionInfo(
        pid=999999, title="Standup", start_time=datetime(2026, 9, 11, 10, 0),
        session_dir=session_dir, tags=["user-tag"],
    )

    note_path = stop_session(
        info, Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path
    )

    assert parse_note_meta(note_path).tags == ["user-tag", "ai-tag"]


def test_stop_session_dedupes_tags_the_provider_also_produced(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n")
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="s", action_items=[], tags=["ai-only", "shared-tag"]),
    )
    info = SessionInfo(
        pid=999999, title="Standup", start_time=datetime(2026, 9, 11, 10, 0),
        session_dir=session_dir, tags=["shared-tag"],
    )

    note_path = stop_session(
        info, Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path
    )

    # Session tags are merged in FIRST, so "shared-tag" must move to the front
    # even though the Provider listed it second — this fails without the
    # merge/de-dup logic (which would leave the Provider's own order intact).
    assert parse_note_meta(note_path).tags == ["shared-tag", "ai-only"]


def test_stop_session_leaves_provider_tags_alone_when_no_session_tags(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n")
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="s", action_items=[], tags=["ai-tag-a", "ai-tag-b"]),
    )
    info = SessionInfo(999999, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)  # no tags — defaults to []

    note_path = stop_session(
        info, Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path
    )

    assert parse_note_meta(note_path).tags == ["ai-tag-a", "ai-tag-b"]


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
    monkeypatch.setattr("notetaker.service.get_provider_credential", lambda key: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    status = check_setup(_config(tmp_path))
    assert status == SetupStatus(blackhole=BlackHoleStatus.ACTIVE, provider_ready=True, provider_problems=[])


def test_check_setup_reports_ready_when_keychain_has_credential(monkeypatch, tmp_path):
    from notetaker.service import SetupStatus, check_setup
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.get_provider_credential", lambda key: "sk-ant-from-keychain")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status = check_setup(_config(tmp_path))
    assert status == SetupStatus(blackhole=BlackHoleStatus.ACTIVE, provider_ready=True, provider_problems=[])


def test_check_setup_reports_missing_claude_api_key(monkeypatch, tmp_path):
    from notetaker.service import check_setup
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.get_provider_credential", lambda key: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status = check_setup(_config(tmp_path))
    assert status.provider_ready is False
    assert "No credential found for ANTHROPIC_API_KEY" in status.provider_problems[0]


def test_check_setup_degrades_to_env_var_when_keychain_raises(monkeypatch, tmp_path):
    from keyring.errors import KeyringError

    from notetaker.service import SetupStatus, check_setup

    def raise_keyring_error(key):
        raise KeyringError("Keychain locked")

    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.get_provider_credential", raise_keyring_error)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    status = check_setup(_config(tmp_path))
    assert status == SetupStatus(blackhole=BlackHoleStatus.ACTIVE, provider_ready=True, provider_problems=[])


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


def test_stop_session_writes_transcript_sidecar_alongside_note(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n[00:00:07] world\n")
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="summary text", action_items=[], tags=[]),
    )

    note_path = stop_session(
        _session_info(session_dir),
        Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
        tmp_path,
    )

    sidecar_path = notes_dir / f"{note_path.stem}.transcript.txt"
    assert sidecar_path.exists()
    assert sidecar_path.read_text() == "[00:00:03] hello\n[00:00:07] world\n"


def test_stop_session_writes_empty_transcript_sidecar_when_no_audio(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)

    note_path = stop_session(
        _session_info(session_dir),
        Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
        tmp_path,
    )

    sidecar_path = notes_dir / f"{note_path.stem}.transcript.txt"
    assert sidecar_path.exists()
    assert sidecar_path.read_text() == ""


def test_check_and_salvage_orphan_returns_none_when_no_session_file(tmp_path):
    assert check_and_salvage_orphan(_config(tmp_path), tmp_path) is None


def test_check_and_salvage_orphan_returns_none_when_session_file_corrupt(tmp_path):
    (tmp_path / "current_session.json").write_text("{not valid json")
    assert check_and_salvage_orphan(_config(tmp_path), tmp_path) is None


def test_check_and_salvage_orphan_returns_none_when_pid_alive(tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {"pid": os.getpid(), "title": "x", "start_time": "2026-09-11T10:00:00", "session_dir": str(tmp_path)}
        )
    )
    assert check_and_salvage_orphan(_config(tmp_path), tmp_path) is None
    assert session_file.exists()  # untouched — a live session must not be disturbed


def test_check_and_salvage_orphan_salvages_dead_session_into_note(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n")
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {
                "pid": 999999,
                "title": "Standup",
                "start_time": "2026-09-11T10:00:00",
                "session_dir": str(session_dir),
            }
        )
    )
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="summary text", action_items=[], tags=[]),
    )

    note_path = check_and_salvage_orphan(
        Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path
    )

    assert note_path is not None
    assert "summary text" in note_path.read_text()
    assert not session_file.exists()
    assert not session_dir.exists()


def test_check_and_salvage_orphan_computes_duration_from_transcript_mtime(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    transcript_path = session_dir / "transcript.txt"
    transcript_path.write_text("[00:00:03] hello\n")
    # backdate the transcript's mtime to 5 minutes after the session's start_time
    start_time = datetime(2026, 9, 11, 10, 0, 0)
    backdated_mtime = (start_time + timedelta(minutes=5)).timestamp()
    os.utime(transcript_path, (backdated_mtime, backdated_mtime))

    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {
                "pid": 999999,
                "title": "Standup",
                "start_time": start_time.isoformat(),
                "session_dir": str(session_dir),
            }
        )
    )
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="summary text", action_items=[], tags=[]),
    )

    note_path = check_and_salvage_orphan(
        Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path
    )

    assert "duration_minutes: 5" in note_path.read_text()


def test_cancel_session_discards_without_writing_note(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n")
    session_file = tmp_path / "current_session.json"
    session_file.write_text("{}")
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)

    cancel_session(_session_info(session_dir), tmp_path)

    assert not session_dir.exists()
    assert not session_file.exists()


def test_cancel_session_sends_sigterm_and_waits_for_live_pid(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    session_file = tmp_path / "current_session.json"
    session_file.write_text("{}")

    calls = {"kill": None, "sleeps": 0}
    pid_states = iter([True, True, False])
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: next(pid_states))
    monkeypatch.setattr("notetaker.service.os.kill", lambda pid, sig: calls.__setitem__("kill", (pid, sig)))
    monkeypatch.setattr(
        "notetaker.service.time.sleep", lambda s: calls.__setitem__("sleeps", calls["sleeps"] + 1)
    )

    cancel_session(_session_info(session_dir), tmp_path)

    assert calls["kill"] == (999999, signal.SIGTERM)
    assert calls["sleeps"] == 1
    assert not session_dir.exists()


def test_save_provider_credential_rejects_non_claude_provider(tmp_path):
    from notetaker.service import save_provider_credential

    config = Config(tmp_path, "tiny", "apple_local", "apple-foundationmodel", "UNUSED")
    with pytest.raises(ServiceError, match="only supported for the 'claude' provider"):
        save_provider_credential(config, "sk-ant-whatever")


def test_save_provider_credential_rejects_invalid_key(monkeypatch, tmp_path):
    from notetaker.service import save_provider_credential

    monkeypatch.setattr("notetaker.service.validate_claude_api_key", lambda key: False)
    with pytest.raises(ServiceError, match="rejected by Anthropic's API"):
        save_provider_credential(_config(tmp_path), "sk-ant-bad-key")


def test_save_provider_credential_stores_valid_key(monkeypatch, tmp_path):
    from notetaker.service import save_provider_credential

    calls = {}
    monkeypatch.setattr("notetaker.service.validate_claude_api_key", lambda key: True)
    monkeypatch.setattr(
        "notetaker.service.set_provider_credential", lambda key_name, value: calls.__setitem__("args", (key_name, value))
    )
    save_provider_credential(_config(tmp_path), "sk-ant-good-key")
    assert calls["args"] == ("ANTHROPIC_API_KEY", "sk-ant-good-key")


def test_save_provider_credential_raises_service_error_when_keychain_raises(monkeypatch, tmp_path):
    from keyring.errors import KeyringError

    from notetaker.service import save_provider_credential

    monkeypatch.setattr("notetaker.service.validate_claude_api_key", lambda key: True)

    def raise_keyring_error(key_name, value):
        raise KeyringError("Keychain locked")

    monkeypatch.setattr("notetaker.service.set_provider_credential", raise_keyring_error)
    with pytest.raises(ServiceError, match="Could not save to the macOS Keychain"):
        save_provider_credential(_config(tmp_path), "sk-ant-good-key")


def test_get_masked_provider_credential_returns_none_when_unset(monkeypatch, tmp_path):
    from notetaker.service import get_masked_provider_credential

    monkeypatch.setattr("notetaker.service.get_provider_credential", lambda key: None)
    assert get_masked_provider_credential(_config(tmp_path)) is None


def test_get_masked_provider_credential_returns_none_when_keychain_raises(monkeypatch, tmp_path):
    from keyring.errors import KeyringError

    from notetaker.service import get_masked_provider_credential

    def raise_keyring_error(key):
        raise KeyringError("Keychain locked")

    monkeypatch.setattr("notetaker.service.get_provider_credential", raise_keyring_error)
    assert get_masked_provider_credential(_config(tmp_path)) is None


def test_resummarize_note_raises_for_missing_note(tmp_path):
    from notetaker.service import resummarize_note

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    with pytest.raises(ServiceError, match="no note found"):
        resummarize_note(Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), "nonexistent")


def test_resummarize_note_raises_when_no_transcript_sidecar(tmp_path):
    from notetaker.service import resummarize_note

    notes_dir = tmp_path / "notes"
    write_note(notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("old", [], []), ["hello"])
    # write_note alone doesn't create the sidecar — only stop_session does — so this note has none
    with pytest.raises(ServiceError, match="no persisted transcript"):
        resummarize_note(
            Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), "2026-09-11-standup"
        )


def test_resummarize_note_replaces_summary_from_sidecar(monkeypatch, tmp_path):
    from notetaker.service import resummarize_note

    notes_dir = tmp_path / "notes"
    note_path = write_note(
        notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("old summary", [], []), ["[00:00:01] hello"]
    )
    (notes_dir / f"{note_path.stem}.transcript.txt").write_text("[00:00:01] hello\n")

    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="new summary", action_items=["new item"], tags=["new-tag"]),
    )

    result_path = resummarize_note(
        Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), "2026-09-11-standup"
    )

    assert result_path == note_path
    text = note_path.read_text()
    assert "new summary" in text
    assert "old summary" not in text


def test_resummarize_note_raises_and_preserves_note_when_provider_fails(monkeypatch, tmp_path):
    from notetaker.service import resummarize_note

    notes_dir = tmp_path / "notes"
    note_path = write_note(
        notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("old summary", [], []), ["[00:00:01] hello"]
    )
    (notes_dir / f"{note_path.stem}.transcript.txt").write_text("[00:00:01] hello\n")
    original_text = note_path.read_text()

    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())

    def raise_error(transcript, provider):
        raise RuntimeError("network down")

    monkeypatch.setattr("notetaker.service.summarize_transcript", raise_error)

    with pytest.raises(ServiceError, match="resummarization failed"):
        resummarize_note(Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), "2026-09-11-standup")

    assert note_path.read_text() == original_text


def test_resummarize_note_raises_for_unparseable_note_without_calling_provider(monkeypatch, tmp_path):
    from notetaker.service import resummarize_note

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    note_path = notes_dir / "2026-09-11-standup.md"
    note_path.write_text("not a valid note file, no frontmatter at all")
    (notes_dir / "2026-09-11-standup.transcript.txt").write_text("[00:00:01] hello\n")

    def fail_if_called(*a, **k):
        raise AssertionError("get_provider should not be called for an unparseable note")

    monkeypatch.setattr("notetaker.service.get_provider", fail_if_called)

    with pytest.raises(ServiceError, match="could not be parsed"):
        resummarize_note(
            Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), "2026-09-11-standup"
        )


def test_get_masked_provider_credential_returns_masked_value(monkeypatch, tmp_path):
    from notetaker.service import get_masked_provider_credential

    monkeypatch.setattr("notetaker.service.get_provider_credential", lambda key: "sk-ant-api03-abcdef1234")
    assert get_masked_provider_credential(_config(tmp_path)) == "sk-ant••••1234"


def test_stop_session_reports_phases_via_callback(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)

    phases = []
    stop_session(
        _session_info(session_dir),
        Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
        tmp_path,
        on_phase=phases.append,
    )

    assert phases == ["Stopping recorder...", "Summarizing..."]


def test_check_and_salvage_orphan_returns_none_when_claim_loses_race(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {
                "pid": 999999,
                "title": "Standup",
                "start_time": "2026-09-11T10:00:00",
                "session_dir": str(session_dir),
            }
        )
    )
    notes_dir = tmp_path / "notes"

    def fake_pid_alive(pid):
        # Simulate a concurrent caller (e.g. the menu bar app's timer) winning
        # the claim race in the window between our pid check and our own
        # rename attempt.
        session_file.rename(session_file.with_suffix(".salvaging"))
        return False

    monkeypatch.setattr("notetaker.service.pid_alive", fake_pid_alive)

    result = check_and_salvage_orphan(
        Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path
    )

    assert result is None


def test_check_and_salvage_orphan_restores_session_file_when_finalize_stop_fails(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {
                "pid": 999999,
                "title": "Standup",
                "start_time": "2026-09-11T10:00:00",
                "session_dir": str(session_dir),
            }
        )
    )
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)

    def raise_error(info, config, claim_path, end_time=None, on_phase=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr("notetaker.service._finalize_stop", raise_error)

    with pytest.raises(RuntimeError, match="disk full"):
        check_and_salvage_orphan(
            Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path
        )

    # The claim must be released so a future attempt can retry — the session
    # file is back under its original name, not lost.
    assert session_file.exists()
    assert not session_file.with_suffix(".salvaging").exists()


def test_cancel_session_raises_when_no_active_session(tmp_path):
    with pytest.raises(ServiceError, match="no active session"):
        cancel_session(_session_info(tmp_path), tmp_path)


def test_check_and_salvage_orphan_does_not_double_salvage_during_stop_session(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {
                "pid": 999999,
                "title": "Standup",
                "start_time": "2026-09-11T10:00:00",
                "session_dir": str(session_dir),
            }
        )
    )
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)

    config = Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY")

    # Simulate check_and_salvage_orphan's poll landing while a stop_session
    # (or another finalizer) is already mid-flight and holds the claim.
    claim = _claim_session_file(tmp_path)
    assert claim is not None

    result = check_and_salvage_orphan(config, tmp_path)

    assert result is None
    assert not notes_dir.exists()


def test_get_current_session_status_returns_none_when_no_session_file(tmp_path):
    assert get_current_session_status(tmp_path) is None


def test_get_current_session_status_returns_none_when_session_file_corrupt(tmp_path):
    (tmp_path / "current_session.json").write_text("{not valid json")
    assert get_current_session_status(tmp_path) is None


def test_get_current_session_status_returns_none_when_pid_dead(tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {"pid": 999999, "title": "Standup", "start_time": "2026-09-11T10:00:00", "session_dir": str(tmp_path)}
        )
    )
    assert get_current_session_status(tmp_path) is None


def test_get_current_session_status_returns_info_when_pid_alive(tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {"pid": os.getpid(), "title": "Standup", "start_time": "2026-09-11T10:00:00", "session_dir": str(tmp_path)}
        )
    )
    info = get_current_session_status(tmp_path)
    assert info == SessionInfo(os.getpid(), "Standup", datetime(2026, 9, 11, 10, 0), tmp_path)


def test_delete_note_removes_file_and_sidecar(tmp_path):
    from notetaker.service import delete_note

    notes_dir = tmp_path / "notes"
    note_path = write_note(notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], []), ["hi"])
    sidecar_path = notes_dir / f"{note_path.stem}.transcript.txt"
    sidecar_path.write_text("hi")

    delete_note(_config(tmp_path), "2026-09-11-standup")

    assert not note_path.exists()
    assert not sidecar_path.exists()


def test_delete_note_removes_file_when_sidecar_absent(tmp_path):
    from notetaker.service import delete_note

    notes_dir = tmp_path / "notes"
    note_path = write_note(notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], []), ["hi"])

    delete_note(_config(tmp_path), "2026-09-11-standup")

    assert not note_path.exists()


def test_delete_note_raises_for_missing_note(tmp_path):
    from notetaker.service import delete_note

    with pytest.raises(ServiceError, match="no note found"):
        delete_note(_config(tmp_path), "nonexistent")


def test_get_live_transcript_preview_returns_transcript_text(tmp_path):
    from notetaker.service import get_live_transcript_preview

    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n")
    info = SessionInfo(999999, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    assert get_live_transcript_preview(info) == "[00:00:03] hello\n"


def test_get_live_transcript_preview_returns_empty_string_before_first_chunk(tmp_path):
    from notetaker.service import get_live_transcript_preview

    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = SessionInfo(999999, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    assert get_live_transcript_preview(info) == ""
