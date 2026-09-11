import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import typer

from notetaker.config import CONFIG_DIR, load_config, write_default_config
from notetaker.recorder import BlackHoleStatus, check_blackhole, find_blackhole_device_index
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


SESSION_FILE = CONFIG_DIR / SESSION_FILE_NAME


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@app.command()
def start(title: str):
    if SESSION_FILE.exists():
        try:
            session = json.loads(SESSION_FILE.read_text())
            if _pid_alive(session["pid"]):
                typer.echo("error: a session is already running. Run `notetaker stop` first.", err=True)
                raise typer.Exit(1)
        except (json.JSONDecodeError, KeyError, OSError):
            pass

    status = check_blackhole()
    if status != BlackHoleStatus.ACTIVE:
        typer.echo("error: BlackHole is not active. Run `notetaker init` for setup instructions.", err=True)
        raise typer.Exit(1)
    device_index = find_blackhole_device_index()

    typer.echo("macOS will ask for microphone access to read the BlackHole device — please allow it.")
    typer.echo(
        "Reminder: Teams will not show its own recording indicator for this. "
        "Let participants know you're recording."
    )

    config = load_config()
    start_time = datetime.now()
    session_dir = CONFIG_DIR / "sessions" / start_time.strftime("%Y%m%d-%H%M%S")
    session_dir.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [sys.executable, "-m", "notetaker.recorder", str(session_dir), str(device_index), config.whisper_model],
        start_new_session=True,
    )
    SESSION_FILE.write_text(
        json.dumps(
            {
                "pid": proc.pid,
                "title": title,
                "start_time": start_time.isoformat(),
                "session_dir": str(session_dir),
            }
        )
    )
    typer.echo(f"Recording started: {title}")
