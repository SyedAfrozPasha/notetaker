import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from notetaker import service
from notetaker.config import CONFIG_DIR, CONFIG_PATH, Config
from notetaker.timefmt import format_elapsed as _format_elapsed

_PACKAGE_DIR = Path(__file__).parent

DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8420

app = FastAPI()
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
# htmx is vendored (not loaded from a CDN) so the dashboard works fully offline.
app.mount("/static", StaticFiles(directory=str(_PACKAGE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))


@app.middleware("http")
async def _reject_cross_site_posts(request: Request, call_next):
    """Binding to 127.0.0.1 stops other machines, not the user's own
    browser — any page open in the same browser could otherwise submit a
    cross-origin form straight to this app (e.g. POST /cancel, which
    irreversibly discards an in-progress Session's Transcript with no Note
    produced). Modern browsers attach Sec-Fetch-Site to every request;
    reject only when it explicitly says cross-site, so non-browser local
    tools (which don't send this header at all) are unaffected.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if request.method == "POST" and fetch_site not in (None, "same-origin", "none"):
        # "same-site" covers every other local server (localhost:3000 …), which
        # is exactly the origin a malicious page would use — reject it too.
        return HTMLResponse("Cross-site requests are not allowed.", status_code=403)
    return await call_next(request)


def port_in_use_error(host: str, port: int) -> str | None:
    """A user-facing message if nothing can listen on host:port right now,
    else None. `notetaker dashboard` checks this *before* putting the menu
    bar item up, so a taken port fails the command outright instead of
    leaving a menu bar icon with a dead "Open Dashboard" behind it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as exc:
            reason = exc.strerror or str(exc)
            return (
                f"cannot bind the dashboard to http://{host}:{port} ({reason}). "
                "Is another notetaker dashboard already running?"
            )
    return None


@dataclass
class BackgroundDashboard:
    """The dashboard's uvicorn server running on a daemon thread of the
    current process — so `notetaker dashboard` can hand the main thread to
    the rumps menu bar app (Cocoa insists on the main thread), and the two
    UI surfaces share one process, one `brew services` entry, and one
    lifetime."""

    url: str
    server: uvicorn.Server
    thread: threading.Thread

    def stop(self, timeout: float = 5.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout)


def serve_in_background(host: str, port: int) -> BackgroundDashboard:
    # Default uvicorn logging (startup line + one access-log line per request)
    # so a terminal running `notetaker dashboard` sees the server working.
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))
    # uvicorn only installs its SIGINT/SIGTERM handlers on the main thread, so
    # on this thread the process's default handlers stay in charge — which is
    # what we want: `brew services stop` sends SIGTERM and the whole process
    # (menu bar included) exits.
    thread = threading.Thread(target=server.run, name="notetaker-dashboard", daemon=True)
    thread.start()
    return BackgroundDashboard(url=f"http://{host}:{port}", server=server, thread=thread)


@dataclass
class TranscriptRow:
    """One rendered line of a Transcript. `speaker` is "me", "others",
    "status" (a Recorder `[warning]`/`[note]`/`[transcription failed …]`
    line) or "" (a mono-chunk line, or anything unrecognised)."""

    time: str
    speaker: str
    text: str


_SPEAKER_LINE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s*(?:(Me|Others):\s*)?(.*)$")
_STATUS_LINE_RE = re.compile(r"^\[(warning|note|transcription failed[^\]]*|recording stopped[^\]]*)\]\s*(.*)$")


def parse_transcript_rows(text: str) -> list[TranscriptRow]:
    """Splits the Transcriber's `[hh:mm:ss] Me: …` / `[hh:mm:ss] Others: …` /
    `[hh:mm:ss] …` lines (and the Recorder's status lines) into rows the
    templates can style per speaker. Blank lines are dropped."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        status = _STATUS_LINE_RE.match(line)
        if status:
            kind, rest = status.groups()
            if kind in ("warning", "note"):
                text = rest or kind
            else:  # the whole message lives inside the brackets
                text = f"{kind}: {rest}" if rest else kind
            rows.append(TranscriptRow("", "status", text))
            continue
        spoken = _SPEAKER_LINE_RE.match(line)
        if spoken:
            stamp, speaker, rest = spoken.groups()
            rows.append(TranscriptRow(stamp, (speaker or "").lower(), rest))
            continue
        rows.append(TranscriptRow("", "", line))
    return rows


def count_segments(rows: list[TranscriptRow]) -> int:
    return sum(1 for row in rows if row.speaker != "status")


def seconds_since_transcript_update(transcript_path: Path, now: float | None = None) -> int | None:
    """Whole seconds since the Transcriber last appended to the file, or
    None when no Chunk has been transcribed yet (the file doesn't exist)."""
    try:
        mtime = transcript_path.stat().st_mtime
    except FileNotFoundError:
        return None
    return max(0, int((now if now is not None else time.time()) - mtime))


# Audio-routing warnings from `start_session` are not persisted anywhere
# (see SessionInfo.warnings), so /start remembers them here, keyed by the
# session directory, and every /status poll re-renders them until that
# session ends. In-memory on purpose: a dashboard restart mid-meeting drops
# them, which is rare and harmless.
_start_warnings: dict[Path, list[str]] = {}


def _status_context(config: Config) -> dict:
    info = service.get_current_session_status(CONFIG_DIR)
    if info is None:
        _start_warnings.clear()
        return {"recording": False}
    for session_dir in list(_start_warnings):
        if session_dir != info.session_dir:
            del _start_warnings[session_dir]
    rows = parse_transcript_rows(service.get_live_transcript_preview(info))
    context = {
        "recording": True,
        "title": info.title,
        "tags": list(info.tags or []),
        "elapsed": _format_elapsed(info.start_time, datetime.now()),
        "start_epoch": info.start_time.timestamp(),
        "rows": rows,
        "segment_count": count_segments(rows),
        "seconds_since_update": seconds_since_transcript_update(info.session_dir / "transcript.txt"),
    }
    pinned = _start_warnings.get(info.session_dir)
    if pinned:
        context["warning"] = " ".join(pinned)
    return context


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


NOTES_PAGE_SIZE = 10


def _parse_page(value: str | None) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/status", response_class=HTMLResponse)
def status(request: Request):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "_status.html", {"setup_error": str(exc)})

    running = _running_stop_job()
    if running is not None:
        return templates.TemplateResponse(request, "_status.html", _processing_context(running))
    context = {}
    finished = _take_finished_stop_job()
    if finished is not None and finished.note_path is not None and _finished_recently(finished):
        # htmx follows HX-Redirect on a polled response too: the page that
        # was showing "Processing..." lands on the freshly saved Note.
        return Response(status_code=200, headers={"HX-Redirect": f"/notes/{finished.note_path.stem}?saved=1"})
    if finished is not None and finished.error is not None:
        context["error"] = f"Could not save the recording: {finished.error}"

    try:
        salvaged_path = service.check_and_salvage_orphan(config, CONFIG_DIR)
    except Exception:
        salvaged_path = None
    if salvaged_path is not None:
        context["success"] = f"Recovered a crashed session and saved it as {salvaged_path.name}"

    context.update(_status_context(config))
    return templates.TemplateResponse(request, "_status.html", context)


@app.post("/start", response_class=HTMLResponse)
def start(request: Request, title: str = Form(...), tags: str = Form("")):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "_status.html", {"setup_error": str(exc)})

    running = _running_stop_job()
    if running is not None:
        return templates.TemplateResponse(request, "_status.html", _processing_context(running))

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    context = {}
    try:
        # Same salvage-before-start as the CLI: a Recorder that crashed since
        # the last status poll must be saved, not overwritten.
        salvaged_path = service.check_and_salvage_orphan(config, CONFIG_DIR)
        if salvaged_path is not None:
            context["success"] = f"Recovered a crashed session and saved it as {salvaged_path.name}"
        info = service.start_session(title, config, CONFIG_DIR, tags=tag_list)
    except Exception as exc:
        return templates.TemplateResponse(
            request, "_status.html", {**_status_context(config), **context, "error": str(exc)}
        )
    warnings = list(getattr(info, "warnings", None) or [])
    if warnings:
        _start_warnings[info.session_dir] = warnings  # re-shown by every /status poll while this session lives
        context["warning"] = " ".join(warnings)

    return templates.TemplateResponse(request, "_status.html", {**_status_context(config), **context})


# Stopping runs as a background `service.StopJob` shared with the menu bar
# (same process under `notetaker dashboard`): whichever surface pressed Stop,
# the status poll shows the job's phase while it runs and, once the Note
# exists, sends the browser to it (HX-Redirect). A finished job is acted on
# once — `_handled_stop_job` remembers which one — and only if it finished
# recently, so opening the Record page an hour later doesn't jump to an old note.
_handled_stop_job: service.StopJob | None = None
_REDIRECT_WINDOW_SECONDS = 60


def _running_stop_job() -> service.StopJob | None:
    job = service.current_stop_job()
    return job if job is not None and job.running else None


def _processing_context(job: service.StopJob) -> dict:
    return {
        "processing": True,
        "phase": job.phase,
        "elapsed": _format_elapsed(job.started_at, datetime.now()),
        "start_epoch": job.started_at.timestamp(),
    }


def _take_finished_stop_job() -> service.StopJob | None:
    """The finished stop job this dashboard has not reacted to yet, or None."""
    global _handled_stop_job
    job = service.current_stop_job()
    if job is None or job.running or job is _handled_stop_job:
        return None
    _handled_stop_job = job
    return job


def _finished_recently(job: service.StopJob) -> bool:
    return job.finished_at is not None and (datetime.now() - job.finished_at).total_seconds() <= _REDIRECT_WINDOW_SECONDS


@app.post("/stop", response_class=HTMLResponse)
def stop(request: Request):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "_status.html", {"setup_error": str(exc)})

    running = _running_stop_job()
    if running is not None:
        return templates.TemplateResponse(request, "_status.html", _processing_context(running))

    info = service.get_current_session_status(CONFIG_DIR)
    if info is None:
        return templates.TemplateResponse(
            request, "_status.html", {"recording": False, "error": "no active session."}
        )

    job = service.begin_stop(info, config, CONFIG_DIR)
    return templates.TemplateResponse(request, "_status.html", _processing_context(job))


@app.post("/cancel", response_class=HTMLResponse)
def cancel(request: Request):
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


@app.get("/notes", response_class=HTMLResponse)
def notes_list(
    request: Request,
    query: str | None = None,
    tag: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: str | None = None,
):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "notes_list.html", {"setup_error": str(exc)})

    try:
        notes = service.search_notes(
            config,
            query=query or None,
            tag=tag or None,
            start_date=_parse_date(start_date),
            end_date=_parse_date(end_date),
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "notes_list.html",
            {"error": str(exc), "query": query, "tag": tag, "start_date": start_date, "end_date": end_date},
        )

    total_pages = max(1, -(-len(notes) // NOTES_PAGE_SIZE))  # ceil division
    current_page = min(_parse_page(page), total_pages)
    start = (current_page - 1) * NOTES_PAGE_SIZE
    page_notes = notes[start : start + NOTES_PAGE_SIZE]

    return templates.TemplateResponse(
        request,
        "notes_list.html",
        {
            "notes": page_notes,
            "total_notes": len(notes),
            "page": current_page,
            "total_pages": total_pages,
            "query": query,
            "tag": tag,
            "start_date": start_date,
            "end_date": end_date,
        },
    )


@app.get("/notes/{note_id}", response_class=HTMLResponse)
def notes_detail(request: Request, note_id: str, saved: str | None = None):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "note_detail.html", {"setup_error": str(exc)})

    try:
        detail = service.get_note_detail(config, note_id)
        full_markdown = service.get_note_body(config, note_id)
    except Exception as exc:
        return templates.TemplateResponse(request, "note_detail.html", {"not_found": str(exc)})

    context = {"detail": detail, "full_markdown": full_markdown, "rows": parse_transcript_rows(detail.transcript)}
    if saved:  # just arrived here from the Record page's stop flow
        if "Summarization failed:" in detail.summary_text:
            context["warning"] = "Recording saved, but summarization failed — the transcript is intact. Try Resummarize."
        else:
            context["success"] = "Recording saved."
    return templates.TemplateResponse(request, "note_detail.html", context)


@app.get("/notes/{note_id}/edit", response_class=HTMLResponse)
def notes_edit_form(request: Request, note_id: str):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "note_edit.html", {"setup_error": str(exc)})

    try:
        detail = service.get_note_detail(config, note_id)
    except Exception as exc:
        return templates.TemplateResponse(request, "note_edit.html", {"not_found": str(exc)})

    return templates.TemplateResponse(request, "note_edit.html", {"detail": detail})


@app.post("/notes/{note_id}/edit", response_class=HTMLResponse)
def notes_edit_submit(
    request: Request,
    note_id: str,
    title: str = Form(...),
    tags: str = Form(""),
    summary_text: str = Form(""),
    action_items: str = Form(""),
):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "note_edit.html", {"setup_error": str(exc)})

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    item_list = [line.strip() for line in action_items.splitlines() if line.strip()]
    try:
        service.update_note(
            config, note_id, title=title, tags=tag_list, summary_text=summary_text, action_items=item_list
        )
    except Exception as exc:
        # Re-render with what the user just submitted, not a fresh re-fetch
        # from disk — the save failed, so the on-disk copy is still the OLD
        # content, and showing that instead would silently discard whatever
        # the user just typed. Jinja2's `detail.field` syntax works on a
        # plain dict exactly like it does on a NoteDetail (attribute lookup
        # falls back to item lookup), so no dataclass is needed here.
        submitted = {
            "note_id": note_id,
            "title": title,
            "tags": tag_list,
            "summary_text": summary_text,
            "action_items": item_list,
        }
        return templates.TemplateResponse(request, "note_edit.html", {"detail": submitted, "error": str(exc)})

    return RedirectResponse(f"/notes/{note_id}", status_code=303)


@app.post("/notes/{note_id}/delete", response_class=HTMLResponse)
def notes_delete(request: Request, note_id: str):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "note_detail.html", {"setup_error": str(exc)})

    try:
        service.delete_note(config, note_id)
    except Exception as exc:
        return templates.TemplateResponse(request, "note_detail.html", {"not_found": str(exc)})

    return Response(status_code=200, headers={"HX-Redirect": "/notes"})


@app.post("/notes/{note_id}/resummarize", response_class=HTMLResponse)
def notes_resummarize(request: Request, note_id: str):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "note_detail.html", {"setup_error": str(exc)})

    try:
        service.resummarize_note(config, note_id)
    except Exception as exc:
        try:
            detail = service.get_note_detail(config, note_id)
            full_markdown = service.get_note_body(config, note_id)
        except Exception:
            return templates.TemplateResponse(request, "note_detail.html", {"not_found": str(exc)})
        return templates.TemplateResponse(
            request,
            "note_detail.html",
            {
                "detail": detail,
                "error": str(exc),
                "full_markdown": full_markdown,
                "rows": parse_transcript_rows(detail.transcript),
            },
        )

    return Response(status_code=200, headers={"HX-Redirect": f"/notes/{note_id}"})


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "settings.html", {"setup_error": str(exc)})

    masked_credential = service.get_masked_provider_credential(config)
    return templates.TemplateResponse(
        request, "settings.html", {"config": config, "masked_credential": masked_credential}
    )


@app.post("/settings/config", response_class=HTMLResponse)
def settings_update_config(
    request: Request,
    notes_dir: str = Form(...),
    ai_provider: str = Form(...),
    ai_model: str = Form(""),
    capture_microphone: str = Form(""),
    tap_process: str = Form(""),
):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "settings.html", {"setup_error": str(exc)})

    updates = {
        "notes_dir": notes_dir,
        "ai_provider": ai_provider,
        "capture_microphone": capture_microphone == "on",
        "tap_process": tap_process.strip() or None,
    }
    if ai_model.strip():
        updates["ai_model"] = ai_model.strip()
    try:
        service.update_config(updates, CONFIG_PATH)
    except Exception as exc:
        submitted = {**updates, "ai_model": ai_model or config.ai_model}
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "config": submitted,
                "masked_credential": service.get_masked_provider_credential(config),
                "config_error": str(exc),
            },
        )

    return RedirectResponse("/settings", status_code=303)


def _applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def pick_folder_with_finder(start_dir: Path | None = None) -> Path | None:
    """Opens macOS's native folder picker (the Finder-style `choose folder`
    dialog, via osascript) and returns the directory the user picked, or
    None if they cancelled. Runs as a subprocess on purpose: the picker
    must not run on the web server thread, and osascript puts the dialog
    on the main thread of its own process, whatever process hosts us.
    Raises RuntimeError with a readable message on any other failure."""
    script = 'tell me to activate\nPOSIX path of (choose folder with prompt "Choose where Notetaker saves notes"'
    if start_dir is not None and start_dir.is_dir():
        script += f' default location (POSIX file "{_applescript_string(str(start_dir))}")'
    script += ")"
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=600)
    except FileNotFoundError as exc:
        raise RuntimeError("osascript not found — the Finder picker needs macOS.") from exc
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        if "User canceled" in result.stderr or "(-128)" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip() or f"osascript exited with status {result.returncode}")
    chosen = result.stdout.strip()
    if not chosen:
        return None
    return Path(chosen.rstrip("/") or "/")


@app.post("/settings/browse", response_class=HTMLResponse)
def settings_browse_notes_dir(request: Request, notes_dir: str = Form("")):
    """Re-renders just the notes-directory field with the folder picked in
    Finder (nothing is saved until the user submits the settings form)."""
    context = {"value": notes_dir}
    start = Path(notes_dir).expanduser() if notes_dir.strip() else None
    try:
        chosen = pick_folder_with_finder(start)
    except RuntimeError as exc:
        return templates.TemplateResponse(request, "_notes_dir_field.html", {**context, "error": str(exc)})
    if chosen is not None:
        context = {"value": str(chosen), "picked": True}
    return templates.TemplateResponse(request, "_notes_dir_field.html", context)


@app.post("/settings/credential", response_class=HTMLResponse)
def settings_update_credential(request: Request, api_key: str = Form(...)):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "settings.html", {"setup_error": str(exc)})

    try:
        service.save_provider_credential(config, api_key)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "config": config,
                "masked_credential": service.get_masked_provider_credential(config),
                "credential_error": str(exc),
            },
        )

    return RedirectResponse("/settings", status_code=303)
