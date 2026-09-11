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
