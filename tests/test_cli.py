import json
from unittest.mock import MagicMock

from typer.testing import CliRunner

from notetaker.cli import app
from notetaker.config import Config
from notetaker.recorder import BlackHoleStatus

runner = CliRunner()


def test_init_writes_config_and_reports_blackhole_active(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.write_default_config", lambda: True)
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(tmp_path, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )
    monkeypatch.setattr("notetaker.cli.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.cli.Transcriber", lambda model: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "BlackHole is installed and active" in result.output


def test_init_fails_when_api_key_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.write_default_config", lambda: True)
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(tmp_path, "tiny", "claude", "claude-sonnet-5", "MISSING_KEY"),
    )
    monkeypatch.setattr("notetaker.cli.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.delenv("MISSING_KEY", raising=False)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1


def test_init_reports_apple_local_problems(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.write_default_config", lambda: True)
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(tmp_path, "tiny", "apple_local", "apple-foundationmodel", "UNUSED"),
    )
    monkeypatch.setattr("notetaker.cli.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.cli.check_apple_local_preflight", lambda: ["apfel is not installed"])

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "apfel is not installed" in result.output


def test_start_fails_when_blackhole_not_active(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", tmp_path / "current_session.json")
    monkeypatch.setattr("notetaker.cli.check_blackhole", lambda: BlackHoleStatus.NOT_INSTALLED)

    result = runner.invoke(app, ["start", "Standup"])
    assert result.exit_code == 1


def test_start_fails_when_session_already_running(monkeypatch, tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(json.dumps({"pid": os_getpid_for_test(), "title": "x", "start_time": "2026-09-11T10:00:00", "session_dir": str(tmp_path)}))
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", session_file)
    monkeypatch.setattr("notetaker.cli.check_blackhole", lambda: BlackHoleStatus.ACTIVE)

    result = runner.invoke(app, ["start", "Standup"])
    assert result.exit_code == 1
    assert "already running" in result.output


def os_getpid_for_test():
    import os
    return os.getpid()


def test_start_spawns_recorder_and_writes_session_file(monkeypatch, tmp_path):
    session_file = tmp_path / "current_session.json"
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", session_file)
    monkeypatch.setattr("notetaker.cli.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.cli.find_blackhole_device_index", lambda: 2)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(tmp_path, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )
    fake_proc = MagicMock(pid=12345)
    fake_proc.poll.return_value = None
    monkeypatch.setattr("notetaker.cli.subprocess.Popen", lambda *a, **k: fake_proc)

    result = runner.invoke(app, ["start", "Standup"])

    assert result.exit_code == 0
    assert "Recording started" in result.output
    assert "microphone access" in result.output
    assert "Teams will not show" in result.output
    session = json.loads(session_file.read_text())
    assert session["pid"] == 12345
    assert session["title"] == "Standup"


def test_start_recovers_from_corrupt_session_file(monkeypatch, tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text("{invalid json content")
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", session_file)
    monkeypatch.setattr("notetaker.cli.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.cli.find_blackhole_device_index", lambda: 2)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(tmp_path, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )
    fake_proc = MagicMock(pid=54321)
    fake_proc.poll.return_value = None
    monkeypatch.setattr("notetaker.cli.subprocess.Popen", lambda *a, **k: fake_proc)

    result = runner.invoke(app, ["start", "Weekly"])

    assert result.exit_code == 0
    assert "Recording started" in result.output
    session = json.loads(session_file.read_text())
    assert session["pid"] == 54321
    assert session["title"] == "Weekly"


def test_start_fails_when_recorder_exits_immediately(monkeypatch, tmp_path):
    session_file = tmp_path / "current_session.json"
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", session_file)
    monkeypatch.setattr("notetaker.cli.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.cli.find_blackhole_device_index", lambda: 2)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(tmp_path, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )
    fake_proc = MagicMock(pid=99999)
    fake_proc.poll.return_value = 0
    monkeypatch.setattr("notetaker.cli.subprocess.Popen", lambda *a, **k: fake_proc)
    monkeypatch.setattr("notetaker.cli.time.sleep", lambda s: None)

    result = runner.invoke(app, ["start", "Standup"])

    assert result.exit_code == 1
    assert "recorder failed to start" in result.output
    assert not session_file.exists()


def test_stop_fails_with_no_active_session(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", tmp_path / "current_session.json")
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 1
    assert "no active session" in result.output


def test_stop_fails_cleanly_on_corrupt_session_file(monkeypatch, tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text("{not valid json")
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", session_file)

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 1
    assert "corrupt or unreadable" in result.output
    assert str(session_file) in result.output


def test_stop_skips_provider_call_when_transcript_empty(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    # no transcript.txt written — simulates a recorder that crashed at startup

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
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", session_file)
    monkeypatch.setattr("notetaker.cli._pid_alive", lambda pid: False)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )

    def fail_if_called(*a, **k):
        raise AssertionError("get_provider should not be called for an empty transcript")

    monkeypatch.setattr("notetaker.cli.get_provider", fail_if_called)
    monkeypatch.setattr("notetaker.cli.summarize_transcript", fail_if_called)

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
    saved_notes = list(notes_dir.glob("*.md"))
    assert len(saved_notes) == 1
    assert "No audio was captured" in saved_notes[0].read_text()


def test_stop_salvages_transcript_and_writes_note(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n[00:00:07] world\n")

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
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", session_file)
    monkeypatch.setattr("notetaker.cli._pid_alive", lambda pid: False)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )

    from notetaker.summarizer import Summary

    monkeypatch.setattr("notetaker.cli.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.cli.summarize_transcript",
        lambda transcript, provider: Summary(text="summary text", action_items=["a"], tags=["t"]),
    )

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
    assert not session_file.exists()
    assert not session_dir.exists()
    saved_notes = list(notes_dir.glob("*.md"))
    assert len(saved_notes) == 1
    assert "summary text" in saved_notes[0].read_text()


def test_stop_saves_note_with_error_when_summarization_fails(monkeypatch, tmp_path):
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
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", session_file)
    monkeypatch.setattr("notetaker.cli._pid_alive", lambda pid: False)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )
    monkeypatch.setattr("notetaker.cli.get_provider", lambda config: object())

    def raise_error(transcript, provider):
        raise RuntimeError("network down")

    monkeypatch.setattr("notetaker.cli.summarize_transcript", raise_error)

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
    saved_notes = list(notes_dir.glob("*.md"))
    assert len(saved_notes) == 1
    assert "Summarization failed" in saved_notes[0].read_text()
    assert "hello" in saved_notes[0].read_text()


from datetime import datetime

from notetaker.notes import write_note
from notetaker.summarizer import Summary


def test_list_prints_notes(monkeypatch, tmp_path):
    notes_dir = tmp_path / "notes"
    write_note(notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], ["proj"]), [])
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "Standup" in result.output
    assert "proj" in result.output


def test_show_prints_note_body(monkeypatch, tmp_path):
    notes_dir = tmp_path / "notes"
    write_note(
        notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5,
        Summary("Summary text", [], []), ["[00:00:01] hi"],
    )
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )
    result = runner.invoke(app, ["show", "2026-09-11-standup"])
    assert result.exit_code == 0
    assert "Summary text" in result.output


def test_show_missing_note_fails(monkeypatch, tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )
    result = runner.invoke(app, ["show", "nonexistent"])
    assert result.exit_code == 1
