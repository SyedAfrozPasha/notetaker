from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from notetaker.cli import app
from notetaker.config import Config
from notetaker.recorder import BlackHoleStatus
from notetaker.service import ServiceError, SessionInfo, SetupStatus

runner = CliRunner()


def _config(tmp_path):
    return Config(tmp_path / "notes", "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY")


def test_init_writes_config_and_reports_blackhole_active(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.service.initialize_config", lambda path: True)
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        "notetaker.cli.service.check_setup",
        lambda config: SetupStatus(BlackHoleStatus.ACTIVE, True, []),
    )
    monkeypatch.setattr("notetaker.cli.service.ensure_whisper_model", lambda config: None)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "BlackHole is installed and active" in result.output
    assert "ANTHROPIC_API_KEY is set." in result.output


def test_init_fails_when_provider_not_ready(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.service.initialize_config", lambda path: True)
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        "notetaker.cli.service.check_setup",
        lambda config: SetupStatus(BlackHoleStatus.ACTIVE, False, ["ANTHROPIC_API_KEY is not set."]),
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY is not set." in result.output


def test_start_reports_service_error(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.cli.service.check_and_salvage_orphan", lambda config, config_dir: None)

    def fail(*a, **k):
        raise ServiceError("BlackHole is not active. Run `notetaker init` for setup instructions.")

    monkeypatch.setattr("notetaker.cli.service.start_session", fail)

    result = runner.invoke(app, ["start", "Standup"])

    assert result.exit_code == 1
    assert "BlackHole is not active" in result.output


def test_start_prints_reminders_and_confirmation_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.cli.service.check_and_salvage_orphan", lambda config, config_dir: None)
    monkeypatch.setattr(
        "notetaker.cli.service.start_session",
        lambda title, config, config_dir: SessionInfo(12345, title, datetime.now(), tmp_path),
    )

    result = runner.invoke(app, ["start", "Standup"])

    assert result.exit_code == 0
    assert "microphone access" in result.output
    assert "Teams will not show" in result.output
    assert "Recording started: Standup" in result.output


def test_start_prints_recovery_message_when_orphan_salvaged(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    salvaged_note_path = tmp_path / "notes" / "2026-09-10-standup.md"
    monkeypatch.setattr(
        "notetaker.cli.service.check_and_salvage_orphan", lambda config, config_dir: salvaged_note_path
    )
    monkeypatch.setattr(
        "notetaker.cli.service.start_session",
        lambda title, config, config_dir: SessionInfo(12345, title, datetime.now(), tmp_path),
    )

    result = runner.invoke(app, ["start", "Standup"])

    assert result.exit_code == 0
    assert f"Recovered a crashed session and saved it as a note: {salvaged_note_path}" in result.output
    assert "Recording started: Standup" in result.output


def test_stop_reports_service_error_for_no_active_session(monkeypatch):
    def fail(config_dir):
        raise ServiceError("no active session.")

    monkeypatch.setattr("notetaker.cli.service.read_active_session", fail)

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 1
    assert "no active session" in result.output


def test_stop_prints_note_path_on_success(monkeypatch, tmp_path):
    info = SessionInfo(999999, "Standup", datetime.now(), tmp_path)
    monkeypatch.setattr("notetaker.cli.service.read_active_session", lambda config_dir: info)
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    note_path = tmp_path / "notes" / "2026-09-11-standup.md"
    monkeypatch.setattr("notetaker.cli.service.stop_session", lambda info, config, config_dir: note_path)

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
    assert str(note_path) in result.output


def test_list_prints_notes(monkeypatch, tmp_path):
    from notetaker.notes import NoteMeta

    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    meta = NoteMeta("2026-09-11-standup", "Standup", datetime(2026, 9, 11, 10, 0), 5, ["proj"], Path("x.md"))
    monkeypatch.setattr("notetaker.cli.service.list_all_notes", lambda config: [meta])

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "Standup" in result.output
    assert "proj" in result.output


def test_show_prints_note_body(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.cli.service.get_note_body", lambda config, note_id: "Summary text")

    result = runner.invoke(app, ["show", "2026-09-11-standup"])

    assert result.exit_code == 0
    assert "Summary text" in result.output


def test_show_missing_note_fails(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))

    def fail(config, note_id):
        raise ServiceError(f"no note found with id '{note_id}'.")

    monkeypatch.setattr("notetaker.cli.service.get_note_body", fail)

    result = runner.invoke(app, ["show", "nonexistent"])

    assert result.exit_code == 1
