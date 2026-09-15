# Dashboard Skeleton and Session Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first working slice of the local web dashboard — a FastAPI/Jinja2/htmx app, bound to `127.0.0.1` only, that shows live recording status and lets the user start, stop, and cancel a Session from a browser — launched via a new `notetaker dashboard` CLI command.

**Architecture:** A single new module, `notetaker/dashboard.py`, defines the FastAPI `app` and its routes; it calls only the already-complete `notetaker/service.py` surface (never touches recorder/transcriber/notes internals directly), exactly like `cli.py` and `menubar.py` do. One polling endpoint (`GET /status`) and three action endpoints (`POST /start`/`/stop`/`/cancel`) all render the same reusable Jinja2 partial (`_status.html`), so htmx can swap a single `<div id="status">` regardless of which endpoint responded. The outer page (`GET /`) polls `/status` every 2 seconds via `hx-trigger="load, every 2s"` — the dashboard's own orphan-detection cadence, piggybacking `check_and_salvage_orphan` onto that same poll, exactly as the design spec calls for.

**Tech Stack:** FastAPI (new dependency), Jinja2 (new), Uvicorn (new, ASGI server), python-multipart (new, form parsing), htmx 2.0.10 via CDN (`https://cdn.jsdelivr.net/npm/htmx.org@2.0.10/dist/htmx.min.js`) — no vendoring, since the default `claude`/Anthropic Provider already requires internet access, so a CDN script tag adds no new offline-capability regression. Tests use `fastapi.testclient.TestClient` (`httpx`, already a transitive dependency via `anthropic`, added explicitly to `dev` extras for clarity).

**Spec:** `docs/superpowers/specs/2026-09-12-notetaker-ui-design.md` (see "Architecture" and "Feature scope by surface > Dashboard" — this plan covers only the session-lifecycle portion: create/status/live-view/stop/cancel; notes browsing/edit/delete/resummarize and config/credential management are explicitly out of scope, deferred to later dashboard plans). Domain vocabulary: `CONTEXT.md`.

## Global Constraints

- The dashboard binds to `127.0.0.1` only — never `0.0.0.0` — per the spec's "Local web dashboard ... bound to `127.0.0.1` only".
- `notetaker/dashboard.py` calls only `notetaker/service.py` (and `notetaker/config.py`'s `CONFIG_DIR`/`CONFIG_PATH` constants) — it never imports from `notetaker/recorder.py`, `notetaker/transcriber.py`, or `notetaker/notes.py` directly, matching the architecture principle that `service.py` is the single seam every UI surface calls through.
- Do not touch `notetaker/cli.py` or `notetaker/menubar.py` in this plan — both are already shipped, reviewed, and hardened (the menu bar app's `rumps.alert`/`rumps.notification` hang-risk work in particular). `notetaker/dashboard.py` defines its own small `_format_elapsed` helper rather than importing `notetaker.menubar.format_elapsed` — the two modules stay independent so a future change to one's display logic can't silently affect the other, and importing from `menubar.py` would be the only reason `dashboard.py` ever needs anything menu-bar-specific.
- Config is loaded fresh on every request via `service.get_config(CONFIG_PATH)` (never cached) — the same "reload every tick" pattern `menubar.py`'s `_on_tick` already uses, and the first UI surface to use `service.get_config`'s `ServiceError`-wrapping (added specifically for this) instead of a raw `load_config` call.
- Every route catches `service.ServiceError` from `get_config` and renders the same `_status.html` partial with a `setup_error` message — never a raw `ConfigError` traceback.
- Use the modern Starlette/FastAPI template-response signature: `templates.TemplateResponse(request, "name.html", context)` (`request` positional, not inside the context dict) — the older `TemplateResponse("name.html", {"request": request, ...})` form is deprecated on the FastAPI/Starlette versions this plan's floor versions install.
- Run `.venv/bin/pytest -q` after every task; it must stay green (currently 201 passed) with only the new tests added by that task on top.

---

## Task 1: FastAPI app skeleton, base layout, and the `/` route

**Files:**
- Create: `notetaker/dashboard.py`
- Create: `notetaker/templates/base.html`
- Create: `notetaker/templates/index.html`
- Modify: `pyproject.toml` (add `fastapi`, `jinja2`, `uvicorn`, `python-multipart` to `dependencies`; add `httpx` to `dev` extras)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Produces: `notetaker.dashboard.app` (a `fastapi.FastAPI` instance) and `notetaker.dashboard.templates` (a `fastapi.templating.Jinja2Templates` instance pointed at `notetaker/templates/`) — later tasks in this plan add routes to this same `app` object and reuse `templates`. `GET /` renders `index.html`, which contains a `<div id="status" hx-get="/status" hx-trigger="load, every 2s" hx-swap="innerHTML">Loading...</div>` — the `/status` endpoint itself doesn't exist until Task 2, so this page's poll will 404 until then; that's expected and this task's own test doesn't call `/status`.

- [ ] **Step 1: Add the new dependencies**

Edit `pyproject.toml`. Change:

```toml
dependencies = [
    "typer>=0.12",
    "pyyaml>=6.0",
    "sounddevice>=0.4",
    "numpy>=1.26",
    "faster-whisper==1.0.3",
    "ctranslate2==4.6.0",
    "av==12.3.0",
    "requests>=2.28",
    "anthropic>=0.34",
    "keyring>=24",
    "rich>=13",
    "rumps>=0.4",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.14"]
```

to:

```toml
dependencies = [
    "typer>=0.12",
    "pyyaml>=6.0",
    "sounddevice>=0.4",
    "numpy>=1.26",
    "faster-whisper==1.0.3",
    "ctranslate2==4.6.0",
    "av==12.3.0",
    "requests>=2.28",
    "anthropic>=0.34",
    "keyring>=24",
    "rich>=13",
    "rumps>=0.4",
    "fastapi>=0.115",
    "jinja2>=3.1",
    "uvicorn>=0.30",
    "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.14", "httpx>=0.27"]
```

Run: `.venv/bin/pip install -e ".[dev]" -q`
Expected: installs `fastapi`, `jinja2`, `uvicorn`, `python-multipart`, `httpx` (some may already be present transitively) with no errors.

- [ ] **Step 2: Write the failing test**

Create `tests/test_dashboard.py`:

```python
import pytest
from fastapi.testclient import TestClient

from notetaker.dashboard import app


@pytest.fixture
def client():
    return TestClient(app)


def test_index_page_loads_and_wires_the_status_poller(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Notetaker" in response.text
    assert 'hx-get="/status"' in response.text
    assert 'hx-trigger="load, every 2s"' in response.text


def test_index_page_loads_htmx_from_cdn(client):
    response = client.get("/")

    assert "htmx.org@2.0.10" in response.text
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'notetaker.dashboard'`.

- [ ] **Step 4: Create the templates**

Create `notetaker/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Notetaker</title>
  <script src="https://cdn.jsdelivr.net/npm/htmx.org@2.0.10/dist/htmx.min.js"></script>
  <style>
    body { font-family: -apple-system, sans-serif; max-width: 640px; margin: 2rem auto; padding: 0 1rem; }
    .idle { color: #666; }
    .recording { color: #c00; font-weight: bold; }
    .error { color: #c00; border: 1px solid #c00; padding: 0.5rem; border-radius: 4px; }
    .success { color: #060; border: 1px solid #060; padding: 0.5rem; border-radius: 4px; }
    textarea { width: 100%; height: 8rem; font-family: monospace; }
    label { display: block; margin: 0.5rem 0; }
  </style>
</head>
<body>
  <h1>Notetaker</h1>
  {% block content %}{% endblock %}
</body>
</html>
```

Create `notetaker/templates/index.html`:

```html
{% extends "base.html" %}
{% block content %}
<div id="status" hx-get="/status" hx-trigger="load, every 2s" hx-swap="innerHTML">
  Loading...
</div>
{% endblock %}
```

- [ ] **Step 5: Create the FastAPI app**

Create `notetaker/dashboard.py`:

```python
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -v`
Expected: PASS, 2 passed.

- [ ] **Step 7: Run the full suite to verify nothing else broke**

Run: `.venv/bin/pytest -q`
Expected: PASS, 203 passed (201 + 2 new).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml notetaker/dashboard.py notetaker/templates/base.html notetaker/templates/index.html tests/test_dashboard.py
git commit -m "feat: add FastAPI dashboard app skeleton"
```

---

## Task 2: `/status` polling endpoint, live view, and orphan-salvage polling

**Files:**
- Modify: `notetaker/dashboard.py`
- Create: `notetaker/templates/_status.html`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `service.get_config`, `service.ServiceError`, `service.check_and_salvage_orphan`, `service.get_current_session_status`, `service.get_live_transcript_preview`, `service.SessionInfo` (all from Task-1-and-earlier `notetaker/service.py`); `CONFIG_DIR`, `CONFIG_PATH` from `notetaker/config.py`.
- Produces: `notetaker.dashboard._format_elapsed(start_time: datetime, now: datetime) -> str` — `MM:SS`, or `H:MM:SS` past an hour. `notetaker.dashboard._status_context(config: Config) -> dict` — returns `{"recording": False}` when idle, or `{"recording": True, "elapsed": str, "transcript": str}` when a session is active; later tasks (Start/Stop/Cancel) call this to render the post-action state. `GET /status` — the polling route; renders `notetaker/templates/_status.html` with `_status_context`'s result, plus a `"success"` key set to a recovery message whenever `check_and_salvage_orphan` finds and salvages an orphan on that poll, plus a `"setup_error"` key (instead of the above) when `service.get_config` raises `ServiceError`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard.py` (the file already has the `client` fixture and imports `TestClient`/`app` — reuse them; add these imports at the top: `from datetime import datetime` and `from notetaker import service` and `from notetaker.config import Config`):

```python
from datetime import datetime

from notetaker import service
from notetaker.config import Config


def _config(tmp_path):
    return Config(tmp_path / "notes", "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY")


def test_status_shows_idle_state_with_start_form(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.check_and_salvage_orphan", lambda config, config_dir: None)
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: None)

    response = client.get("/status")

    assert response.status_code == 200
    assert "Not recording" in response.text
    assert 'hx-post="/start"' in response.text


def test_status_shows_recording_state_with_elapsed_and_transcript(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(999999, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.check_and_salvage_orphan", lambda config, config_dir: None)
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr(
        "notetaker.dashboard.service.get_live_transcript_preview", lambda info: "[00:00:03] hello\n"
    )

    response = client.get("/status")

    assert response.status_code == 200
    assert "Recording" in response.text
    assert "[00:00:03] hello" in response.text
    assert 'hx-post="/stop"' in response.text
    assert 'hx-post="/cancel"' in response.text


def test_status_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.get("/status")

    assert response.status_code == 200
    assert "not set up" in response.text
    assert "Run" in response.text


def test_status_shows_recovery_banner_when_orphan_salvaged(client, monkeypatch, tmp_path):
    salvaged_note_path = tmp_path / "notes" / "2026-09-10-standup.md"

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr(
        "notetaker.dashboard.service.check_and_salvage_orphan", lambda config, config_dir: salvaged_note_path
    )
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: None)

    response = client.get("/status")

    assert response.status_code == 200
    assert "Recovered a crashed session" in response.text
    assert "2026-09-10-standup.md" in response.text


def test_status_continues_when_salvage_raises_unexpectedly(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    def raise_disk_error(config, config_dir):
        raise OSError("disk full")

    monkeypatch.setattr("notetaker.dashboard.service.check_and_salvage_orphan", raise_disk_error)
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: None)

    response = client.get("/status")

    assert response.status_code == 200
    assert "Not recording" in response.text


def test_format_elapsed_under_an_hour():
    from notetaker.dashboard import _format_elapsed

    assert _format_elapsed(datetime(2026, 9, 11, 10, 0, 0), datetime(2026, 9, 11, 10, 5, 30)) == "05:30"


def test_format_elapsed_over_an_hour():
    from notetaker.dashboard import _format_elapsed

    assert _format_elapsed(datetime(2026, 9, 11, 10, 0, 0), datetime(2026, 9, 11, 11, 2, 3)) == "1:02:03"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -v`
Expected: FAIL — the Task 1 tests still pass, but every new test fails: `AttributeError`/404 for `/status` (route doesn't exist) and `ImportError`/`AttributeError` for `_format_elapsed`.

- [ ] **Step 3: Create the `_status.html` partial**

Create `notetaker/templates/_status.html`:

```html
{% if setup_error %}
<p class="error">Notetaker is not set up: {{ setup_error }}</p>
{% elif recording %}
<p class="recording">&#9679; Recording ({{ elapsed }})</p>
{% if success %}<p class="success">{{ success }}</p>{% endif %}
{% if error %}<p class="error">{{ error }}</p>{% endif %}
<textarea readonly>{{ transcript }}</textarea>
<form hx-post="/stop" hx-target="#status" hx-swap="innerHTML">
  <button type="submit">Stop</button>
</form>
<form hx-post="/cancel" hx-target="#status" hx-swap="innerHTML" hx-confirm="Cancel this recording? The transcript will be discarded and no note will be saved.">
  <button type="submit">Cancel</button>
</form>
{% else %}
<p class="idle">Not recording.</p>
{% if success %}<p class="success">{{ success }}</p>{% endif %}
{% if error %}<p class="error">{{ error }}</p>{% endif %}
<form hx-post="/start" hx-target="#status" hx-swap="innerHTML">
  <label>Title <input type="text" name="title" required></label>
  <label>Tags (comma-separated) <input type="text" name="tags"></label>
  <button type="submit">Start Recording</button>
</form>
{% endif %}
```

- [ ] **Step 4: Implement `_format_elapsed`, `_status_context`, and `/status`**

Replace the full contents of `notetaker/dashboard.py` with:

```python
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -v`
Expected: PASS, 9 passed (2 from Task 1 + 7 new).

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 210 passed (203 + 7 new).

- [ ] **Step 7: Commit**

```bash
git add notetaker/dashboard.py notetaker/templates/_status.html tests/test_dashboard.py
git commit -m "feat: add dashboard status polling endpoint with orphan-salvage detection"
```

---

## Task 3: `POST /start`

**Files:**
- Modify: `notetaker/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `service.start_session(title, config, config_dir, tags=None)`, `_status_context`, `service.get_config`.
- Produces: `POST /start` (form fields: `title` required, `tags` optional comma-separated string) — starts a session and re-renders `_status.html` via `_status_context`; on `ServiceError` (e.g. BlackHole not active), re-renders the idle partial with an `"error"` message instead.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard.py`:

```python
def test_start_creates_session_and_shows_recording_state(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = {}

    def fake_start_session(title, config, config_dir, tags=None):
        calls["title"] = title
        calls["tags"] = tags
        return info

    monkeypatch.setattr("notetaker.dashboard.service.start_session", fake_start_session)
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr("notetaker.dashboard.service.get_live_transcript_preview", lambda info: "")

    response = client.post("/start", data={"title": "Standup", "tags": "project-x, planning"})

    assert response.status_code == 200
    assert "Recording" in response.text
    assert calls["title"] == "Standup"
    assert calls["tags"] == ["project-x", "planning"]


def test_start_with_no_tags_passes_empty_list(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = {}

    def fake_start_session(title, config, config_dir, tags=None):
        calls["tags"] = tags
        return info

    monkeypatch.setattr("notetaker.dashboard.service.start_session", fake_start_session)
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr("notetaker.dashboard.service.get_live_transcript_preview", lambda info: "")

    client.post("/start", data={"title": "Standup", "tags": ""})

    assert calls["tags"] == []


def test_start_shows_error_when_service_raises(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    def fail(title, config, config_dir, tags=None):
        raise service.ServiceError("BlackHole is not active. Run `notetaker init` for setup instructions.")

    monkeypatch.setattr("notetaker.dashboard.service.start_session", fail)

    response = client.post("/start", data={"title": "Standup", "tags": ""})

    assert response.status_code == 200
    assert "BlackHole is not active" in response.text
    assert "Not recording" in response.text


def test_start_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.post("/start", data={"title": "Standup", "tags": ""})

    assert response.status_code == 200
    assert "not set up" in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -k start -v`
Expected: FAIL — 404 (no `/start` route yet).

- [ ] **Step 3: Implement `POST /start`**

Add to `notetaker/dashboard.py`. First change the import line `from fastapi import FastAPI, Request` to:

```python
from fastapi import FastAPI, Form, Request
```

Then add, after the `/status` route:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -v`
Expected: PASS, 13 passed (9 from Tasks 1-2 + 4 new).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 214 passed (210 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add notetaker/dashboard.py tests/test_dashboard.py
git commit -m "feat: add dashboard POST /start"
```

---

## Task 4: `POST /stop`

**Files:**
- Modify: `notetaker/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `service.stop_session(info, config, config_dir)`, `service.get_current_session_status`, `_status_context`.
- Produces: `POST /stop` — stops the active session and re-renders the idle partial with a `"success"` message naming the saved note (or noting summarization failure, matching `notetaker/menubar.py`'s `note_summarization_failed` check — duplicated here rather than imported, per this plan's Global Constraints); if there's no active session, or `stop_session` raises, re-renders with an `"error"` message instead.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard.py`:

```python
def test_stop_saves_note_and_shows_success(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)
    note_path = tmp_path / "notes" / "2026-09-11-standup.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text("---\ntitle: Standup\n---\n\n## Summary\nAll good.\n")

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr(
        "notetaker.dashboard.service.stop_session", lambda info, config, config_dir: note_path
    )

    response = client.post("/stop")

    assert response.status_code == 200
    assert "Not recording" in response.text
    assert "2026-09-11-standup.md" in response.text


def test_stop_shows_summarization_failed_note(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)
    note_path = tmp_path / "notes" / "2026-09-11-standup.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text("---\ntitle: Standup\n---\n\n## Summary\nSummarization failed: network down\n")

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr(
        "notetaker.dashboard.service.stop_session", lambda info, config, config_dir: note_path
    )

    response = client.post("/stop")

    assert response.status_code == 200
    assert "summarization failed" in response.text.lower()


def test_stop_shows_error_when_no_active_session(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: None)

    response = client.post("/stop")

    assert response.status_code == 200
    assert "no active session" in response.text


def test_stop_shows_error_when_stop_session_raises(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr("notetaker.dashboard.service.get_live_transcript_preview", lambda info: "")

    def fail(info, config, config_dir):
        raise RuntimeError("disk full")

    monkeypatch.setattr("notetaker.dashboard.service.stop_session", fail)

    response = client.post("/stop")

    assert response.status_code == 200
    assert "disk full" in response.text
    assert "Recording" in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -k stop -v`
Expected: FAIL — 404 (no `/stop` route yet).

- [ ] **Step 3: Implement `POST /stop`**

Add to `notetaker/dashboard.py`, after the `/start` route:

```python
def _note_summarization_failed(note_path) -> bool:
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
    return templates.TemplateResponse(request, "_status.html", {**_status_context(config), "success": success})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -v`
Expected: PASS, 17 passed (13 from Tasks 1-3 + 4 new).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 218 passed (214 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add notetaker/dashboard.py tests/test_dashboard.py
git commit -m "feat: add dashboard POST /stop"
```

---

## Task 5: `POST /cancel`

**Files:**
- Modify: `notetaker/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `service.cancel_session(info, config_dir)`, `service.get_current_session_status`. This is the first UI surface to wire up `cancel_session` at all — it has existed in `service.py` since an earlier plan but nothing has called it until now.
- Produces: `POST /cancel` — discards the active session (no Note is ever produced, per `CONTEXT.md`'s Cancel definition) and re-renders the idle partial with a `"success"` message; if there's no active session, or `cancel_session` raises, re-renders with an `"error"` message instead. The `_status.html` partial's Cancel button already carries `hx-confirm="Cancel this recording? ..."` from Task 2 — htmx shows a native browser confirm dialog before this endpoint is ever called, so no server-side confirmation step is needed here.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard.py`:

```python
def test_cancel_discards_session_and_shows_success(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    calls = []
    monkeypatch.setattr(
        "notetaker.dashboard.service.cancel_session", lambda info, config_dir: calls.append((info, config_dir))
    )

    response = client.post("/cancel")

    assert response.status_code == 200
    assert "Not recording" in response.text
    assert "cancelled" in response.text.lower()
    assert len(calls) == 1


def test_cancel_shows_error_when_no_active_session(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: None)

    response = client.post("/cancel")

    assert response.status_code == 200
    assert "no active session" in response.text


def test_cancel_shows_error_when_cancel_session_raises(client, monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = service.SessionInfo(12345, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr("notetaker.dashboard.service.get_live_transcript_preview", lambda info: "")

    def fail(info, config_dir):
        raise RuntimeError("permission denied")

    monkeypatch.setattr("notetaker.dashboard.service.cancel_session", fail)

    response = client.post("/cancel")

    assert response.status_code == 200
    assert "permission denied" in response.text
    assert "Recording" in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -k cancel -v`
Expected: FAIL — 404 (no `/cancel` route yet).

- [ ] **Step 3: Implement `POST /cancel`**

Add to `notetaker/dashboard.py`, after the `/stop` route:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -v`
Expected: PASS, 20 passed (17 from Tasks 1-4 + 3 new).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 221 passed (218 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add notetaker/dashboard.py tests/test_dashboard.py
git commit -m "feat: add dashboard POST /cancel"
```

---

## Task 6: `notetaker dashboard` CLI command

**Files:**
- Modify: `notetaker/cli.py`
- Modify: `CLAUDE.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `notetaker.dashboard.app` (the Task-1 FastAPI instance) and `uvicorn.run`.
- Produces: `notetaker dashboard` — a new Typer command that launches the dashboard, blocking until killed (matching `notetaker menubar`'s "blocks until quit" shape), bound to `127.0.0.1:8420` only.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py` (the file already has `runner`/`app` set up — reuse them):

```python
def test_dashboard_command_launches_uvicorn_bound_to_localhost(monkeypatch):
    calls = {}

    def fake_run(app, host, port):
        calls["app"] = app
        calls["host"] = host
        calls["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)

    result = runner.invoke(app, ["dashboard"])

    assert result.exit_code == 0
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 8420
    from notetaker.dashboard import app as dashboard_app

    assert calls["app"] is dashboard_app
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py -k dashboard -v`
Expected: FAIL — `AssertionError` (Typer reports "No such command 'dashboard'"), exit code 2.

- [ ] **Step 3: Implement the command**

Add to `notetaker/cli.py`, after the `menubar` command:

```python
@app.command()
def dashboard():
    """Launches the local web dashboard at http://127.0.0.1:8420 (blocks until quit)."""
    import uvicorn

    from notetaker.dashboard import app as dashboard_app

    uvicorn.run(dashboard_app, host="127.0.0.1", port=8420)
```

(Lazy import, same reasoning as `menubar`'s: avoids every other CLI command paying FastAPI/Jinja2/Uvicorn's import cost. This also means tests must patch `uvicorn.run` directly — not `notetaker.cli.uvicorn.run`, since `uvicorn` isn't imported at module level in `cli.py`.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_cli.py -k dashboard -v`
Expected: PASS.

- [ ] **Step 5: Update CLAUDE.md's architecture summary**

In `CLAUDE.md`, find this line in the `cli.py` bullet:

```
- `cli.py` — command dispatch (Typer) for `init`, `start`, `stop`, `list`, `show`, `resummarize`, `set-api-key`, `show-api-key`, `menubar`. `start`/`stop` show a `rich` loading spinner, since both can block for an unpredictable duration (spawning the recorder; the summarization API call).
```

Change it to:

```
- `cli.py` — command dispatch (Typer) for `init`, `start`, `stop`, `list`, `show`, `resummarize`, `set-api-key`, `show-api-key`, `menubar`, `dashboard`. `start`/`stop` show a `rich` loading spinner, since both can block for an unpredictable duration (spawning the recorder; the summarization API call).
```

Then add a new bullet immediately after the existing `menubar.py` bullet:

```
- `dashboard.py` — a `FastAPI`/`Jinja2`/`htmx` local web dashboard, bound to `127.0.0.1` only. One polling endpoint (`GET /status`, polled every 2s by the page itself) renders the same `_status.html` partial that `POST /start`/`/stop`/`/cancel` also render, so htmx can swap a single status `<div>` regardless of which endpoint responded. Piggybacks `check_and_salvage_orphan` onto its own status poll — the dashboard's own crash-detection cadence, independent of (and made safe alongside) the menu bar app's. Notes browsing/edit/delete/resummarize and config/credential management are separate, later work — this first slice covers only the session lifecycle (create/status/live view/stop/cancel).
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 222 passed (221 + 1 new).

- [ ] **Step 7: Commit**

```bash
git add notetaker/cli.py CLAUDE.md tests/test_cli.py
git commit -m "feat: add notetaker dashboard CLI command"
```

---

## Final check (do this after Task 6, before considering the plan done)

Run the full suite one more time and confirm the final count:

```bash
.venv/bin/pytest -q
```

Expected: `222 passed` (plus 1 deselected if run without `-m "not integration"` filtering is not applied — the integration test is unaffected by this plan either way).

Then do a manual smoke test (this plan's automated tests all use `TestClient` against real service-layer mocks — nothing so far has actually run the app under a real ASGI server or opened it in a real browser):

```bash
.venv/bin/notetaker dashboard &
sleep 1
open http://127.0.0.1:8420
```

Confirm in the browser: the page loads, shows "Not recording" within ~2 seconds (the first `hx-trigger="load"` poll), and the Start form is present. Kill the background process (`kill %1` or `pkill -f "notetaker dashboard"`) when done — do not leave it running.
