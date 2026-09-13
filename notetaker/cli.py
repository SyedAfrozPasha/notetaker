import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from notetaker import service
from notetaker.config import CONFIG_DIR, CONFIG_PATH, ConfigError, load_config
from notetaker.recorder import BlackHoleStatus
from notetaker.service import ServiceError

app = typer.Typer()
console = Console()


def _load_config():
    """Loads the config, turning a missing/invalid file into a clean error
    exit instead of a traceback."""
    try:
        return load_config()
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)


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

    config = _load_config()
    with console.status("Checking audio setup and AI provider..."):
        status = service.check_setup(config)

    if status.system_audio == "tap":
        if status.system_audio_problem:
            typer.echo(f"error: {status.system_audio_problem}", err=True)
            raise typer.Exit(1)
        typer.echo(
            "System audio: Core Audio process tap (nothing to install). macOS will ask for "
            "'System Audio Recording' permission on the first `notetaker start`."
        )
    elif status.blackhole == BlackHoleStatus.NOT_INSTALLED:
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

    # The model download is a one-time multi-hundred-MB fetch that produces
    # no output of its own, so show which phase is running and for how long.
    phases = []

    def on_phase(phase: str) -> None:
        phases.append(phase)
        progress.update(task, description=escape(phase))

    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Preparing Whisper model...", total=None)
        try:
            service.ensure_whisper_model(config, on_phase=on_phase)
        except ServiceError as exc:
            model_error = str(exc)
        else:
            model_error = None
        elapsed = progress.tasks[0].elapsed or 0.0
    for phase in phases:
        typer.echo(phase)
    if model_error is not None:
        typer.echo(f"error: {model_error}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Whisper model ready ({elapsed:.0f}s).")


@app.command()
def start(title: str):
    config = _load_config()
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
    for warning in info.warnings:
        typer.echo(f"warning: {warning}", err=True)
    typer.echo(
        "macOS may ask for Microphone and System Audio Recording access on first run — please allow both."
    )
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
    config = _load_config()
    with console.status("Stopping recorder...") as status:
        try:
            note_path = service.stop_session(
                info, config, CONFIG_DIR, on_phase=lambda phase: status.update(escape(phase))
            )
        except ServiceError as exc:
            stop_error = str(exc)
        else:
            stop_error = None
    if stop_error is not None:
        typer.echo(f"error: {stop_error}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Saved note: {note_path}")


@app.command(name="list")
def list_command():
    config = _load_config()
    for meta in service.list_all_notes(config):
        tags = ", ".join(meta.tags)
        typer.echo(f"{meta.note_id}  {meta.title}  [{tags}]")


@app.command()
def show(note_id: str):
    config = _load_config()
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
    config = _load_config()
    try:
        service.save_provider_credential(config, api_key)
    except ServiceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"{config.api_key_env} saved to the macOS Keychain.")


@app.command("show-api-key")
def show_api_key():
    config = _load_config()
    masked = service.get_masked_provider_credential(config)
    if masked is None:
        typer.echo(f"No credential stored for {config.api_key_env}.")
    else:
        typer.echo(masked)


@app.command()
def resummarize(note_id: str):
    config = _load_config()
    try:
        note_path = service.resummarize_note(config, note_id)
    except ServiceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Resummarized note: {note_path}")


@app.command()
def menubar():
    """Launches the menu bar app on its own, without the web dashboard (blocks until quit)."""
    from notetaker.menubar import NotetakerMenuBarApp

    NotetakerMenuBarApp().run()


@app.command()
def dashboard():
    """Launches the local web dashboard at http://127.0.0.1:8420 and the menu bar app,
    together in one process (blocks until quit)."""
    from notetaker import dashboard as dashboard_module
    from notetaker.menubar import NotetakerMenuBarApp

    host, port = dashboard_module.DASHBOARD_HOST, dashboard_module.DASHBOARD_PORT
    error = dashboard_module.port_in_use_error(host, port)
    if error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1)
    served = dashboard_module.serve_in_background(host, port)
    typer.echo(f"Dashboard: {served.url}")
    typer.echo("Menu bar item is up (⏺ while recording). Press Ctrl+C, or choose Quit in the menu bar, to stop.")
    # rumps needs the main thread (Cocoa); the web server keeps running on
    # its daemon thread until Quit or SIGTERM ends the whole process.
    NotetakerMenuBarApp(dashboard_url=served.url).run()
