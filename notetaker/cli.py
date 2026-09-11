import os
from pathlib import Path

import typer

from notetaker.config import CONFIG_DIR, load_config, write_default_config
from notetaker.recorder import BlackHoleStatus, check_blackhole
from notetaker.summarizer import check_apple_local_preflight
from notetaker.transcriber import Transcriber

app = typer.Typer()

SESSION_FILE_NAME = "current_session.json"


@app.callback(invoke_without_command=True)
def main():
    """Notetaker CLI - record and summarize meetings."""
    pass


@app.command("init")
def init():
    config_path = CONFIG_DIR / "config.yaml"
    if write_default_config():
        typer.echo(f"Wrote default config to {config_path}")
    else:
        typer.echo(f"Config already exists at {config_path}, skipping.")

    config = load_config()

    status = check_blackhole()
    if status == BlackHoleStatus.NOT_INSTALLED:
        typer.echo("BlackHole not found. Install it with: brew install blackhole-2ch")
    elif status == BlackHoleStatus.INSTALLED_NOT_ACTIVE:
        typer.echo(
            "BlackHole is installed but not active yet — reboot your Mac, then re-run `notetaker init`."
        )
    else:
        typer.echo("BlackHole is installed and active.")

    if config.ai_provider == "claude":
        if not os.environ.get(config.api_key_env):
            typer.echo(
                f"error: {config.api_key_env} is not set. Export it in your shell profile, "
                "then re-run `notetaker init`.",
                err=True,
            )
            raise typer.Exit(1)
        typer.echo(f"{config.api_key_env} is set.")
    elif config.ai_provider == "apple_local":
        problems = check_apple_local_preflight()
        if problems:
            for problem in problems:
                typer.echo(f"error: {problem}", err=True)
            raise typer.Exit(1)
        typer.echo("apfel is installed and running.")

    typer.echo(f"Loading Whisper model '{config.whisper_model}' (downloads on first run)...")
    Transcriber(config.whisper_model)
    typer.echo("Whisper model ready.")
