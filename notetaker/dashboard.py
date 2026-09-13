from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from notetaker import service
from notetaker.config import CONFIG_DIR, CONFIG_PATH, Config

app = FastAPI()
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


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


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/status", response_class=HTMLResponse)
def status(request: Request):
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
def start(request: Request, title: str = Form(...), tags: str = Form("")):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "_status.html", {"setup_error": str(exc)})

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
        context["warning"] = " ".join(warnings)

    return templates.TemplateResponse(request, "_status.html", {**_status_context(config), **context})


def _note_summarization_failed(note_path: Path) -> bool:
    """Whether a just-saved Note's Summary indicates summarization failed —
    matches the exact fallback text `service._summarize_or_fallback` writes.
    Duplicated from notetaker.menubar's identical helper — see this plan's
    Global Constraints on why dashboard.py doesn't import from menubar.py.
    """
    return "Summarization failed:" in note_path.read_text()


@app.post("/stop", response_class=HTMLResponse)
def stop(request: Request):
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

    return templates.TemplateResponse(
        request,
        "notes_list.html",
        {"notes": notes, "query": query, "tag": tag, "start_date": start_date, "end_date": end_date},
    )


@app.get("/notes/{note_id}", response_class=HTMLResponse)
def notes_detail(request: Request, note_id: str):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "note_detail.html", {"setup_error": str(exc)})

    try:
        detail = service.get_note_detail(config, note_id)
        full_markdown = service.get_note_body(config, note_id)
    except Exception as exc:
        return templates.TemplateResponse(request, "note_detail.html", {"not_found": str(exc)})

    return templates.TemplateResponse(request, "note_detail.html", {"detail": detail, "full_markdown": full_markdown})


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
            request, "note_detail.html", {"detail": detail, "error": str(exc), "full_markdown": full_markdown}
        )

    return RedirectResponse(f"/notes/{note_id}", status_code=303)


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
    whisper_model: str = Form(...),
    ai_provider: str = Form(...),
    ai_model: str = Form(""),
    whisper_model_path: str = Form(""),
    capture_microphone: str = Form(""),
):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "settings.html", {"setup_error": str(exc)})

    updates = {
        "notes_dir": notes_dir,
        "whisper_model": whisper_model,
        "ai_provider": ai_provider,
        "whisper_model_path": whisper_model_path.strip() or None,
        "capture_microphone": capture_microphone == "on",
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
