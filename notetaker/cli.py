import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import typer

from notetaker.config import CONFIG_DIR, load_config, write_default_config
from notetaker.notes import find_note_path, list_notes, read_note_body, write_note
from notetaker.recorder import BlackHoleStatus, check_blackhole, find_blackhole_device_index
from notetaker.summarizer import Summary, check_apple_local_preflight, get_provider, summarize_transcript
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


@app.command()
def stop():
    if not SESSION_FILE.exists():
        typer.echo("error: no active session.", err=True)
        raise typer.Exit(1)

    session = json.loads(SESSION_FILE.read_text())
    pid = session["pid"]
    session_dir = Path(session["session_dir"])

    if _pid_alive(pid):
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            if not _pid_alive(pid):
                break
            time.sleep(1)

    transcript_path = session_dir / "transcript.txt"
    transcript = transcript_path.read_text() if transcript_path.exists() else ""
    transcript_lines = transcript.splitlines()

    start_time = datetime.fromisoformat(session["start_time"])
    duration_minutes = int((datetime.now() - start_time).total_seconds() // 60)

    config = load_config()
    try:
        provider = get_provider(config)
        summary = summarize_transcript(transcript, provider)
    except Exception as exc:
        summary = Summary(text=f"Summarization failed: {exc}", action_items=[], tags=[])

    note_path = write_note(
        config.notes_dir, session["title"], start_time, duration_minutes, summary, transcript_lines
    )

    shutil.rmtree(session_dir, ignore_errors=True)
    SESSION_FILE.unlink()

    typer.echo(f"Saved note: {note_path}")


@app.command(name="list")
def list_command():
    config = load_config()
    for meta in list_notes(config.notes_dir):
        tags = ", ".join(meta.tags)
        typer.echo(f"{meta.note_id}  {meta.title}  [{tags}]")


@app.command()
def show(note_id: str):
    config = load_config()
    path = find_note_path(config.notes_dir, note_id)
    if path is None:
        typer.echo(f"error: no note found with id '{note_id}'.", err=True)
        raise typer.Exit(1)
    typer.echo(read_note_body(path))
