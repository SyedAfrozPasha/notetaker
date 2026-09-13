from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from notetaker import service
from notetaker.config import CONFIG_DIR, CONFIG_PATH, Config

app = FastAPI()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _format_elapsed(start_time: datetime, now: datetime) -> str:
    """Formats elapsed time as MM:SS, or H:MM:SS past an hour."""
    total_seconds = int((now - start_time).total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _status_context(config: Config) -> dict:
    info = service.get_current_session_status(CONFIG_DIR)
    if info is None:
        return {"recording": False}
    return {
        "recording": True,
        "elapsed": _format_elapsed(info.start_time, datetime.now()),
        "transcript": service.get_live_transcript_preview(info),
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/status", response_class=HTMLResponse)
async def status(request: Request):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "_status.html", {"setup_error": str(exc)})

    context = {}
    try:
        salvaged_path = service.check_and_salvage_orphan(config, CONFIG_DIR)
    except Exception:
        salvaged_path = None
    if salvaged_path is not None:
        context["success"] = f"Recovered a crashed session and saved it as {salvaged_path.name}"

    context.update(_status_context(config))
    return templates.TemplateResponse(request, "_status.html", context)


@app.post("/start", response_class=HTMLResponse)
async def start(request: Request, title: str = Form(...), tags: str = Form("")):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "_status.html", {"setup_error": str(exc)})

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    try:
        service.start_session(title, config, CONFIG_DIR, tags=tag_list)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "_status.html", {"recording": False, "error": str(exc)})

    return templates.TemplateResponse(request, "_status.html", _status_context(config))


def _note_summarization_failed(note_path: Path) -> bool:
    """Whether a just-saved Note's Summary indicates summarization failed —
    matches the exact fallback text `service._summarize_or_fallback` writes.
    Duplicated from notetaker.menubar's identical helper — see this plan's
    Global Constraints on why dashboard.py doesn't import from menubar.py.
    """
    return "Summarization failed:" in note_path.read_text()


@app.post("/stop", response_class=HTMLResponse)
async def stop(request: Request):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "_status.html", {"setup_error": str(exc)})

    info = service.get_current_session_status(CONFIG_DIR)
    if info is None:
        return templates.TemplateResponse(
            request, "_status.html", {"recording": False, "error": "no active session."}
        )

    try:
        note_path = service.stop_session(info, config, CONFIG_DIR)
    except Exception as exc:
        return templates.TemplateResponse(
            request, "_status.html", {**_status_context(config), "error": f"Could not save the recording: {exc}"}
        )

    if _note_summarization_failed(note_path):
        success = f"Recording saved as {note_path.name} — summarization failed."
    else:
        success = f"Recording saved as {note_path.name}."
    return templates.TemplateResponse(request, "_status.html", {"recording": False, "success": success})


@app.post("/cancel", response_class=HTMLResponse)
async def cancel(request: Request):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "_status.html", {"setup_error": str(exc)})

    info = service.get_current_session_status(CONFIG_DIR)
    if info is None:
        return templates.TemplateResponse(
            request, "_status.html", {"recording": False, "error": "no active session."}
        )

    try:
        service.cancel_session(info, CONFIG_DIR)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "_status.html",
            {**_status_context(config), "error": f"Could not cancel the recording: {exc}"},
        )

    return templates.TemplateResponse(request, "_status.html", {"recording": False, "success": "Recording cancelled."})
