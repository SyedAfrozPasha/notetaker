import typer

from notetaker import service
from notetaker.config import CONFIG_DIR, CONFIG_PATH, load_config
from notetaker.recorder import BlackHoleStatus
from notetaker.service import ServiceError

app = typer.Typer()


@app.callback(invoke_without_command=True)
def main():
    """Notetaker CLI - record and summarize meetings."""
    pass


@app.command("init")
def init():
    if service.initialize_config(CONFIG_PATH):
        typer.echo(f"Wrote default config to {CONFIG_PATH}")
    else:
        typer.echo(f"Config already exists at {CONFIG_PATH}, skipping.")

    config = load_config()
    status = service.check_setup(config)

    if status.blackhole == BlackHoleStatus.NOT_INSTALLED:
        typer.echo("BlackHole not found. Install it with: brew install blackhole-2ch")
    elif status.blackhole == BlackHoleStatus.INSTALLED_NOT_ACTIVE:
        typer.echo(
            "BlackHole is installed but not active yet — reboot your Mac, then re-run `notetaker init`."
        )
    else:
        typer.echo("BlackHole is installed and active.")

    if not status.provider_ready:
        for problem in status.provider_problems:
            typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(1)

    if config.ai_provider == "claude":
        typer.echo(f"{config.api_key_env} is set.")
    elif config.ai_provider == "apple_local":
        typer.echo("apfel is installed and running.")

    typer.echo(f"Loading Whisper model '{config.whisper_model}' (downloads on first run)...")
    service.ensure_whisper_model(config)
    typer.echo("Whisper model ready.")


@app.command()
def start(title: str):
    config = load_config()
    try:
        salvaged_path = service.check_and_salvage_orphan(config, CONFIG_DIR)
    except Exception as exc:
        typer.echo(f"warning: could not recover a possibly crashed session: {exc}", err=True)
        salvaged_path = None
    if salvaged_path is not None:
        typer.echo(f"Recovered a crashed session and saved it as a note: {salvaged_path}")
    try:
        info = service.start_session(title, config, CONFIG_DIR)
    except ServiceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo("macOS will ask for microphone access to read the BlackHole device — please allow it.")
    typer.echo(
        "Reminder: Teams will not show its own recording indicator for this. "
        "Let participants know you're recording."
    )
    typer.echo(f"Recording started: {info.title}")


@app.command()
def stop():
    try:
        info = service.read_active_session(CONFIG_DIR)
    except ServiceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    config = load_config()
    note_path = service.stop_session(info, config, CONFIG_DIR)
    typer.echo(f"Saved note: {note_path}")


@app.command(name="list")
def list_command():
    config = load_config()
    for meta in service.list_all_notes(config):
        tags = ", ".join(meta.tags)
        typer.echo(f"{meta.note_id}  {meta.title}  [{tags}]")


@app.command()
def show(note_id: str):
    config = load_config()
    try:
        body = service.get_note_body(config, note_id)
    except ServiceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(body)
