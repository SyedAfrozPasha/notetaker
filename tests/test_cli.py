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
    assert "Microphone" in result.output
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


def test_start_continues_when_salvage_raises_unexpectedly(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))

    def raise_disk_error(config, config_dir):
        raise OSError("disk full")

    monkeypatch.setattr("notetaker.cli.service.check_and_salvage_orphan", raise_disk_error)
    monkeypatch.setattr(
        "notetaker.cli.service.start_session",
        lambda title, config, config_dir: SessionInfo(12345, title, datetime.now(), tmp_path),
    )

    result = runner.invoke(app, ["start", "Standup"])

    assert result.exit_code == 0
    assert "could not recover a possibly crashed session" in result.output
    assert "Recording started: Standup" in result.output
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
    monkeypatch.setattr(
        "notetaker.cli.service.stop_session", lambda info, config, config_dir, on_phase=None: note_path
    )

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


def test_set_api_key_reports_service_error(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))

    def fail(config, api_key):
        raise ServiceError("That API key was rejected by Anthropic's API — check it and try again.")

    monkeypatch.setattr("notetaker.cli.service.save_provider_credential", fail)

    result = runner.invoke(app, ["set-api-key", "sk-ant-bad-key"])

    assert result.exit_code == 1
    assert "rejected by Anthropic's API" in result.output


def test_set_api_key_prints_confirmation_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.cli.service.save_provider_credential", lambda config, api_key: None)

    result = runner.invoke(app, ["set-api-key", "sk-ant-good-key"])

    assert result.exit_code == 0
    assert "ANTHROPIC_API_KEY saved to the macOS Keychain." in result.output


def test_set_api_key_prompts_for_hidden_input_when_argument_omitted(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))

    calls = {}

    def fake_save(config, api_key):
        calls["api_key"] = api_key

    monkeypatch.setattr("notetaker.cli.service.save_provider_credential", fake_save)

    result = runner.invoke(app, ["set-api-key"], input="sk-ant-prompted-key\n")

    assert result.exit_code == 0
    assert calls["api_key"] == "sk-ant-prompted-key"


def test_show_api_key_prints_masked_value(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.cli.service.get_masked_provider_credential", lambda config: "sk-ant••••1234")

    result = runner.invoke(app, ["show-api-key"])

    assert result.exit_code == 0
    assert "sk-ant••••1234" in result.output


def test_show_api_key_reports_when_unset(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.cli.service.get_masked_provider_credential", lambda config: None)

    result = runner.invoke(app, ["show-api-key"])

    assert result.exit_code == 0
    assert "No credential stored for ANTHROPIC_API_KEY." in result.output


def test_resummarize_reports_service_error(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))

    def fail(config, note_id):
        raise ServiceError(f"no note found with id '{note_id}'.")

    monkeypatch.setattr("notetaker.cli.service.resummarize_note", fail)

    result = runner.invoke(app, ["resummarize", "nonexistent"])

    assert result.exit_code == 1
    assert "no note found" in result.output


def test_resummarize_prints_confirmation_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    note_path = tmp_path / "notes" / "2026-09-11-standup.md"
    monkeypatch.setattr("notetaker.cli.service.resummarize_note", lambda config, note_id: note_path)

    result = runner.invoke(app, ["resummarize", "2026-09-11-standup"])

    assert result.exit_code == 0
    assert f"Resummarized note: {note_path}" in result.output


def test_stop_escapes_rich_markup_in_phase_callback(monkeypatch, tmp_path):
    from rich.markup import escape

    info = SessionInfo(999999, "Standup", datetime.now(), tmp_path)
    monkeypatch.setattr("notetaker.cli.service.read_active_session", lambda config_dir: info)
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    note_path = tmp_path / "notes" / "2026-09-11-standup.md"
    captured = {}

    def fake_stop_session(info, config, config_dir, on_phase=None):
        captured["on_phase"] = on_phase
        return note_path

    monkeypatch.setattr("notetaker.cli.service.stop_session", fake_stop_session)

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
    # Confirm the callback cli.py actually passed applies escape() before updating the spinner —
    # calling it with bracketed text must not raise, and must route through escape().
    calls = []
    monkeypatch.setattr("rich.status.Status.update", lambda self, phase: calls.append(phase))
    captured["on_phase"]("Summarizing [note]...")
    assert calls == [escape("Summarizing [note]...")]


def test_menubar_command_launches_the_app(monkeypatch):
    calls = []

    class FakeApp:
        def __init__(self):
            calls.append("constructed")

        def run(self):
            calls.append("ran")

    monkeypatch.setattr("notetaker.menubar.NotetakerMenuBarApp", FakeApp)

    result = runner.invoke(app, ["menubar"])

    assert result.exit_code == 0
    assert calls == ["constructed", "ran"]


def test_dashboard_command_serves_on_localhost_and_runs_the_menu_bar_in_the_same_process(monkeypatch):
    """One process: the web server runs on a background thread, the rumps
    menu bar app owns the main thread — so a recording started from the
    browser is always visible in the menu bar too."""
    calls = {}

    class FakeHandle:
        url = "http://127.0.0.1:8420"

    def fake_serve(host, port):
        calls["serve"] = (host, port)
        return FakeHandle()

    class FakeApp:
        def __init__(self, dashboard_url=None):
            calls["dashboard_url"] = dashboard_url

        def run(self):
            calls["ran"] = True

    monkeypatch.setattr("notetaker.dashboard.port_in_use_error", lambda host, port: None)
    monkeypatch.setattr("notetaker.dashboard.serve_in_background", fake_serve)
    monkeypatch.setattr("notetaker.menubar.NotetakerMenuBarApp", FakeApp)

    result = runner.invoke(app, ["dashboard"])

    assert result.exit_code == 0
    assert calls["serve"] == ("127.0.0.1", 8420)
    assert calls["dashboard_url"] == "http://127.0.0.1:8420"
    assert calls["ran"] is True


def test_dashboard_command_exits_before_showing_the_menu_bar_when_the_port_is_taken(monkeypatch):
    monkeypatch.setattr(
        "notetaker.dashboard.port_in_use_error", lambda host, port: f"cannot bind to http://{host}:{port}"
    )
    monkeypatch.setattr(
        "notetaker.dashboard.serve_in_background", lambda host, port: pytest.fail("must not serve")
    )
    monkeypatch.setattr(
        "notetaker.menubar.NotetakerMenuBarApp", lambda dashboard_url=None: pytest.fail("must not show")
    )

    result = runner.invoke(app, ["dashboard"])

    assert result.exit_code == 1
    assert "cannot bind to http://127.0.0.1:8420" in result.output
