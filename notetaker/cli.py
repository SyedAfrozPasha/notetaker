import typer
from rich.console import Console
from rich.markup import escape

from notetaker import service
from notetaker.config import CONFIG_DIR, CONFIG_PATH, load_config
from notetaker.recorder import BlackHoleStatus
from notetaker.service import ServiceError

app = typer.Typer()
console = Console()


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
    with console.status("Starting recorder..."):
        try:
            salvaged_path = service.check_and_salvage_orphan(config, CONFIG_DIR)
            salvage_error = None
        except Exception as exc:
            salvaged_path = None
            salvage_error = str(exc)
        try:
            info = service.start_session(title, config, CONFIG_DIR)
            start_error = None
        except ServiceError as exc:
            info = None
            start_error = str(exc)

    if salvage_error is not None:
        typer.echo(f"warning: could not recover a possibly crashed session: {salvage_error}", err=True)
    if salvaged_path is not None:
        typer.echo(f"Recovered a crashed session and saved it as a note: {salvaged_path}")
    if start_error is not None:
        typer.echo(f"error: {start_error}", err=True)
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
    with console.status("Stopping recorder...") as status:
        note_path = service.stop_session(
            info, config, CONFIG_DIR, on_phase=lambda phase: status.update(escape(phase))
        )
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


@app.command("set-api-key")
def set_api_key(api_key: str = typer.Argument(None)):
    if api_key is None:
        api_key = typer.prompt("Enter your Claude API key", hide_input=True)
    config = load_config()
    try:
        service.save_provider_credential(config, api_key)
    except ServiceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"{config.api_key_env} saved to the macOS Keychain.")


@app.command("show-api-key")
def show_api_key():
    config = load_config()
    masked = service.get_masked_provider_credential(config)
    if masked is None:
        typer.echo(f"No credential stored for {config.api_key_env}.")
    else:
        typer.echo(masked)


@app.command()
def resummarize(note_id: str):
    config = load_config()
    try:
        note_path = service.resummarize_note(config, note_id)
    except ServiceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Resummarized note: {note_path}")


@app.command()
def menubar():
    """Launches the menu bar app (blocks until quit)."""
    from notetaker.menubar import NotetakerMenuBarApp

    NotetakerMenuBarApp().run()


@app.command()
def dashboard():
    """Launches the local web dashboard at http://127.0.0.1:8420 (blocks until quit)."""
    import uvicorn

    from notetaker.dashboard import app as dashboard_app

    uvicorn.run(dashboard_app, host="127.0.0.1", port=8420)
