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
        lambda: Config(tmp_path, "tiny", "apple_local", "apple-fm", "UNUSED"),
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
    monkeypatch.setattr("notetaker.cli.subprocess.Popen", lambda *a, **k: fake_proc)

    result = runner.invoke(app, ["start", "Standup"])

    assert result.exit_code == 0
    assert "Recording started" in result.output
    assert "microphone access" in result.output
    assert "Teams will not show" in result.output
    session = json.loads(session_file.read_text())
    assert session["pid"] == 12345
    assert session["title"] == "Standup"
