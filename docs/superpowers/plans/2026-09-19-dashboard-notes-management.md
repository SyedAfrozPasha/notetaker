# Dashboard Notes Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the local web dashboard (`notetaker/dashboard.py`) with a full notes-management surface — browse/search, view, edit, delete, and resummarize a Note — using only the already-complete `notetaker/service.py` functions, so this plan is routes and templates only (with one necessary exception: hardening `find_note_path` against path traversal before it becomes reachable over HTTP).

**Architecture:** Six new routes added to the existing `notetaker/dashboard.py` FastAPI app (no new Python module — the file stays under 300 lines after this plan, still well within the single-file-per-concern style the project already uses for `cli.py`/`menubar.py`). Three new full-page Jinja2 templates (`notes_list.html`, `note_detail.html`, `note_edit.html`) extend the existing `base.html`. Unlike the session-lifecycle routes (which swap partials into a single always-polling `<div>`), these are traditional multi-page navigation: plain links between pages, plain `<form method="post">` submissions with POST-redirect-GET, and `hx-post` used only where htmx's `hx-confirm` buys a real confirmation dialog (Delete) or where a same-page-refresh-via-redirect is the simplest way to show a mutation's result (Resummarize).

**Tech Stack:** No new dependencies — FastAPI, Jinja2, and htmx (already present from the previous dashboard plan) cover everything here.

**Spec:** `docs/superpowers/specs/2026-09-12-notetaker-ui-design.md` (see "Feature scope by surface > Dashboard" — this plan covers "Browse/search notes", "View a note", "Edit a note", "Delete a note", "Resummarize a note on demand", and "Copy buttons"; config editing and provider credential management remain out of scope, deferred to a later dashboard plan). Domain vocabulary: `CONTEXT.md` (Note, Note ID, Delete, Resummarize).

## Global Constraints

- `notetaker/dashboard.py` calls only `notetaker/service.py` (and `notetaker/config.py`'s `CONFIG_DIR`/`CONFIG_PATH` constants) — never `notetaker/notes.py` directly (Task 1 is the one exception: it modifies `notetaker/notes.py` itself, not `dashboard.py`).
- Every route catches `service.ServiceError` from `service.get_config(CONFIG_PATH)` first and renders a `setup_error` message — never a raw `ConfigError` traceback. This project's dashboard has used this pattern in every route since the previous plan; keep it identical here.
- A route that performs a mutation (`delete_note`, `update_note`, `resummarize_note`) catches broad `Exception`, not just `service.ServiceError` — matching the session-lifecycle routes' established convention (a mutating call can raise `OSError`/API errors that aren't `ServiceError`, and a raw exception must never reach the client as an unhandled 500 with no user-facing message).
- Do not touch `notetaker/cli.py` or `notetaker/menubar.py` in this plan.
- The `TrustedHostMiddleware` and the cross-site-POST-rejection middleware added in the previous plan already apply to every route in this file automatically (middleware wraps the whole `app`, not per-route) — no new task needed to "add" this protection to the new routes, but every new POST route's tests must still pass under the existing `client` fixture (`TestClient(app, base_url="http://127.0.0.1")`), which already satisfies `TrustedHostMiddleware`.
- Never build a JSON blob for a client-side copy button by embedding data inside an inline `onclick` attribute's string — that requires escaping HTML/JS-unsafe characters correctly and is easy to get subtly wrong (Jinja2's plain `Jinja2Templates` environment does not even have Flask's `tojson` filter available). Instead, render the value into a hidden, `readonly` `<textarea>` (Jinja2's default autoescaping already makes this safe, exactly like the existing transcript `<textarea readonly>{{ transcript }}</textarea>` pattern in `_status.html`), and have the button's `onclick` read that element's `.value`.
- Run `.venv/bin/pytest -q` after every task; it must stay green (currently 227 passed) with only the new tests added by that task on top.

---

## Task 1: Harden `find_note_path` against path traversal in `note_id`

`note_id` has only ever been a CLI argument until this plan — a local user typing `notetaker show ../../etc/passwd` isn't an escalation (they already have shell access to the same files). Wiring `note_id` into an HTTP path parameter changes that: combined with the dashboard's known CSRF-adjacent surface (any web page open in the same browser can submit a same-origin-looking POST while `notetaker dashboard` is running — the cross-site check added in the previous plan only blocks requests carrying an explicit `Sec-Fetch-Site: cross-site` header, and older/non-browser clients send no such header at all), an unvalidated `note_id` reaching `find_note_path(notes_dir, note_id)` — which builds `notes_dir / f"{note_id}.md"` with no traversal check — would let `note_id="../../../Users/me/.ssh/id_rsa%00"`-shaped input escape `notes_dir` entirely. `delete_note`, `get_note_body`, `update_note`, `resummarize_note`, and `get_note_detail` all route through `find_note_path`, so fixing it once here protects every future dashboard route (and the CLI) uniformly — the correct place for this fix, per this project's "service layer is the single seam" architecture.

**Files:**
- Modify: `notetaker/notes.py`
- Test: `tests/test_notes.py`

**Interfaces:**
- Produces: `find_note_path(notes_dir: Path, note_id: str) -> Path | None` — same signature and same return value shape as before (an unresolved `Path`, so existing exact-path-equality tests and callers are unaffected) for a legitimate single-component `note_id`; now returns `None` (instead of an escaping path) for any `note_id` whose resolved path would land outside `notes_dir`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_notes.py` (the file already imports `find_note_path` — reuse it, don't re-import):

```python
def test_find_note_path_rejects_path_traversal(tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    secret = tmp_path / "secret.md"
    secret.write_text("do not touch")

    assert find_note_path(notes_dir, "../secret") is None


def test_find_note_path_rejects_embedded_slash(tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "subdir").mkdir()
    (notes_dir / "subdir" / "note.md").write_text("x")

    assert find_note_path(notes_dir, "subdir/note") is None


def test_find_note_path_still_finds_a_normal_note(tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    note_path = notes_dir / "2026-09-11-standup.md"
    note_path.write_text("content")

    found = find_note_path(notes_dir, "2026-09-11-standup")

    assert found == note_path
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_notes.py -k "find_note_path" -v`
Expected: FAIL — `test_find_note_path_rejects_path_traversal` and `test_find_note_path_rejects_embedded_slash` fail because the current implementation returns a real (escaping) path instead of `None`. `test_find_note_path_still_finds_a_normal_note` passes already (it's here to lock in the no-regression behavior once the fix lands — include it in this run for a complete picture, but it isn't expected to fail).

- [ ] **Step 3: Implement the fix**

In `notetaker/notes.py`, replace:

```python
def find_note_path(notes_dir: Path, note_id: str) -> Path | None:
    candidate = notes_dir / f"{note_id}.md"
    return candidate if candidate.exists() else None
```

with:

```python
def find_note_path(notes_dir: Path, note_id: str) -> Path | None:
    candidate = notes_dir / f"{note_id}.md"
    if candidate.resolve().parent != notes_dir.resolve():
        return None
    return candidate if candidate.exists() else None
```

(The returned `candidate` is deliberately still the *unresolved* path — only the safety check uses `.resolve()`. Returning the resolved path instead would break existing tests that compare against an unresolved `tmp_path`-based path, since `tmp_path` on macOS resolves through a `/private` symlink.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_notes.py -k "find_note_path" -v`
Expected: PASS, 4 passed (the 3 new tests plus the pre-existing `test_find_note_path`).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 230 passed (227 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add notetaker/notes.py tests/test_notes.py
git commit -m "fix: harden find_note_path against path traversal before note_id is HTTP-reachable"
```

---

## Task 2: Notes list page (`GET /notes`)

**Files:**
- Modify: `notetaker/dashboard.py`
- Modify: `notetaker/templates/base.html`
- Create: `notetaker/templates/notes_list.html`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `service.get_config`, `service.ServiceError`, `service.search_notes(config, *, query=None, tag=None, start_date=None, end_date=None)`.
- Produces: `notetaker.dashboard._parse_date(value: str | None) -> date | None` — parses an ISO date string from a query parameter, returning `None` for an empty/missing/malformed value (never raises). `GET /notes` — accepts optional query parameters `query`, `tag`, `start_date`, `end_date` (all plain strings from the URL); renders `notes_list.html` with the filtered results plus the current filter values (so the search form redisplays what was searched for).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard.py` (the file already imports `datetime`, `pytest`, `TestClient`, `service`, `Config`, `app`, and has the `client` fixture and `_config` helper — reuse them):

```python
def test_notes_list_shows_all_notes_when_no_filters(client, monkeypatch, tmp_path):
    from notetaker.notes import NoteMeta

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    metas = [
        NoteMeta("2026-09-11-standup", "Standup", datetime(2026, 9, 11, 10, 0), 5, ["proj"], tmp_path / "2026-09-11-standup.md"),
    ]
    monkeypatch.setattr("notetaker.dashboard.service.search_notes", lambda config, **kw: metas)

    response = client.get("/notes")

    assert response.status_code == 200
    assert "Standup" in response.text
    assert "/notes/2026-09-11-standup" in response.text


def test_notes_list_filters_by_query(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = {}

    def fake_search(config, **kw):
        calls.update(kw)
        return []

    monkeypatch.setattr("notetaker.dashboard.service.search_notes", fake_search)

    client.get("/notes", params={"query": "roadmap"})

    assert calls["query"] == "roadmap"
    assert calls["tag"] is None


def test_notes_list_filters_by_tag(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = {}

    def fake_search(config, **kw):
        calls.update(kw)
        return []

    monkeypatch.setattr("notetaker.dashboard.service.search_notes", fake_search)

    client.get("/notes", params={"tag": "project-x"})

    assert calls["tag"] == "project-x"
    assert calls["query"] is None


def test_notes_list_filters_by_date_range(client, monkeypatch, tmp_path):
    from datetime import date

    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = {}

    def fake_search(config, **kw):
        calls.update(kw)
        return []

    monkeypatch.setattr("notetaker.dashboard.service.search_notes", fake_search)

    client.get("/notes", params={"start_date": "2026-09-10", "end_date": "2026-09-15"})

    assert calls["start_date"] == date(2026, 9, 10)
    assert calls["end_date"] == date(2026, 9, 15)


def test_notes_list_shows_no_notes_message_when_empty(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.search_notes", lambda config, **kw: [])

    response = client.get("/notes")

    assert response.status_code == 200
    assert "No notes found" in response.text


def test_notes_list_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.get("/notes")

    assert response.status_code == 200
    assert "not set up" in response.text


def test_base_layout_has_notes_nav_link(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/notes"' in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "notes_list or nav_link" -v`
Expected: FAIL — 404 for every `/notes` request (route doesn't exist yet), and `test_base_layout_has_notes_nav_link` fails since `base.html` has no nav yet.

- [ ] **Step 3: Add the nav link**

In `notetaker/templates/base.html`, change:

```html
<body>
  <h1>Notetaker</h1>
  {% block content %}{% endblock %}
</body>
```

to:

```html
<body>
  <h1>Notetaker</h1>
  <nav><a href="/">Dashboard</a> | <a href="/notes">Notes</a></nav>
  {% block content %}{% endblock %}
</body>
```

- [ ] **Step 4: Create `notes_list.html`**

Create `notetaker/templates/notes_list.html`:

```html
{% extends "base.html" %}
{% block content %}
{% if setup_error %}
<p class="error">Notetaker is not set up: {{ setup_error }}</p>
{% else %}
<form method="get" action="/notes">
  <label>Search <input type="text" name="query" value="{{ query or '' }}"></label>
  <label>Tag <input type="text" name="tag" value="{{ tag or '' }}"></label>
  <label>From <input type="date" name="start_date" value="{{ start_date or '' }}"></label>
  <label>To <input type="date" name="end_date" value="{{ end_date or '' }}"></label>
  <button type="submit">Search</button>
</form>
{% if notes %}
<ul>
  {% for note in notes %}
  <li>
    <a href="/notes/{{ note.note_id }}">{{ note.title }}</a>
    — {{ note.date.strftime('%Y-%m-%d') }}
    {% if note.tags %}[{{ note.tags | join(', ') }}]{% endif %}
  </li>
  {% endfor %}
</ul>
{% else %}
<p>No notes found.</p>
{% endif %}
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Implement `_parse_date` and `GET /notes`**

Add to `notetaker/dashboard.py`. First change the `from datetime import datetime` import line to:

```python
from datetime import date, datetime
```

Then add, after `_status_context` and before the `/` route:

```python
def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
```

Then add, after the `/cancel` route (at the end of the file):

```python
@app.get("/notes", response_class=HTMLResponse)
async def notes_list(
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

    notes = service.search_notes(
        config,
        query=query or None,
        tag=tag or None,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
    )
    return templates.TemplateResponse(
        request,
        "notes_list.html",
        {"notes": notes, "query": query, "tag": tag, "start_date": start_date, "end_date": end_date},
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "notes_list or nav_link" -v`
Expected: PASS, 7 passed.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 237 passed (230 + 7 new).

- [ ] **Step 8: Commit**

```bash
git add notetaker/dashboard.py notetaker/templates/base.html notetaker/templates/notes_list.html tests/test_dashboard.py
git commit -m "feat: add dashboard notes list page with search/tag/date filters"
```

---

## Task 3: Note detail page (`GET /notes/{note_id}`)

**Files:**
- Modify: `notetaker/dashboard.py`
- Create: `notetaker/templates/note_detail.html`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `service.get_note_detail(config, note_id) -> NoteDetail`, `service.get_note_body(config, note_id) -> str`.
- Produces: `GET /notes/{note_id}` — renders `note_detail.html` with the note's structured fields (`detail`) plus `full_markdown` (the note's body text, for the "Copy Full Note as Markdown" button); renders a `not_found` message on `service.ServiceError` (note doesn't exist or can't be parsed).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard.py`:

```python
def test_notes_detail_shows_structured_fields(client, monkeypatch, tmp_path):
    from notetaker.service import NoteDetail

    detail = NoteDetail(
        note_id="2026-09-11-standup", title="Standup", date=datetime(2026, 9, 11, 10, 0),
        duration_minutes=18, tags=["project-x"], summary_text="We discussed X.",
        action_items=["Follow up with Bob"], transcript="[00:00:03] hello",
        path=tmp_path / "2026-09-11-standup.md",
    )
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", lambda config, note_id: detail)
    monkeypatch.setattr("notetaker.dashboard.service.get_note_body", lambda config, note_id: "raw body text")

    response = client.get("/notes/2026-09-11-standup")

    assert response.status_code == 200
    assert "Standup" in response.text
    assert "We discussed X." in response.text
    assert "Follow up with Bob" in response.text
    assert "[00:00:03] hello" in response.text
    assert "project-x" in response.text


def test_notes_detail_shows_none_for_empty_action_items(client, monkeypatch, tmp_path):
    from notetaker.service import NoteDetail

    detail = NoteDetail(
        note_id="2026-09-11-standup", title="Standup", date=datetime(2026, 9, 11, 10, 0),
        duration_minutes=5, tags=[], summary_text="s", action_items=[], transcript="",
        path=tmp_path / "2026-09-11-standup.md",
    )
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", lambda config, note_id: detail)
    monkeypatch.setattr("notetaker.dashboard.service.get_note_body", lambda config, note_id: "")

    response = client.get("/notes/2026-09-11-standup")

    assert response.status_code == 200
    assert "(none)" in response.text


def test_notes_detail_shows_not_found_for_missing_note(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    def fail(config, note_id):
        raise service.ServiceError(f"no note found with id '{note_id}'.")

    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", fail)

    response = client.get("/notes/nonexistent")

    assert response.status_code == 200
    assert "no note found" in response.text


def test_notes_detail_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.get("/notes/2026-09-11-standup")

    assert response.status_code == 200
    assert "not set up" in response.text


def test_notes_detail_has_copy_summary_button(client, monkeypatch, tmp_path):
    from notetaker.service import NoteDetail

    detail = NoteDetail(
        note_id="2026-09-11-standup", title="Standup", date=datetime(2026, 9, 11, 10, 0),
        duration_minutes=5, tags=[], summary_text="We discussed X.", action_items=[], transcript="",
        path=tmp_path / "2026-09-11-standup.md",
    )
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", lambda config, note_id: detail)
    monkeypatch.setattr("notetaker.dashboard.service.get_note_body", lambda config, note_id: "raw")

    response = client.get("/notes/2026-09-11-standup")

    assert 'id="summary-text"' in response.text
    assert "navigator.clipboard.writeText" in response.text


def test_notes_detail_has_copy_full_markdown_button(client, monkeypatch, tmp_path):
    from notetaker.service import NoteDetail

    detail = NoteDetail(
        note_id="2026-09-11-standup", title="Standup", date=datetime(2026, 9, 11, 10, 0),
        duration_minutes=5, tags=[], summary_text="s", action_items=[], transcript="",
        path=tmp_path / "2026-09-11-standup.md",
    )
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", lambda config, note_id: detail)
    monkeypatch.setattr("notetaker.dashboard.service.get_note_body", lambda config, note_id: "the raw markdown body")

    response = client.get("/notes/2026-09-11-standup")

    assert 'id="full-markdown"' in response.text
    assert "the raw markdown body" in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "notes_detail" -v`
Expected: FAIL — 404 (no `/notes/{note_id}` route yet).

- [ ] **Step 3: Create `note_detail.html`**

Create `notetaker/templates/note_detail.html`:

```html
{% extends "base.html" %}
{% block content %}
{% if setup_error %}
<p class="error">Notetaker is not set up: {{ setup_error }}</p>
{% elif not_found %}
<p class="error">{{ not_found }}</p>
{% else %}
<h2>{{ detail.title }}</h2>
<p>{{ detail.date.strftime('%Y-%m-%d %H:%M') }} — {{ detail.duration_minutes }} min{% if detail.tags %} — [{{ detail.tags | join(', ') }}]{% endif %}</p>
{% if success %}<p class="success">{{ success }}</p>{% endif %}
{% if error %}<p class="error">{{ error }}</p>{% endif %}

<h3>Summary</h3>
<p id="summary-text">{{ detail.summary_text }}</p>
<button onclick="navigator.clipboard.writeText(document.getElementById('summary-text').innerText)">Copy Summary</button>

<h3>Action Items</h3>
<ul>
  {% for item in detail.action_items %}
  <li><input type="checkbox" disabled> {{ item }}</li>
  {% else %}
  <li>(none)</li>
  {% endfor %}
</ul>

<h3>Transcript</h3>
<textarea readonly>{{ detail.transcript }}</textarea>

<textarea id="full-markdown" readonly hidden>{{ full_markdown }}</textarea>
<p>
  <a href="/notes/{{ detail.note_id }}/edit">Edit</a>
  <button onclick="navigator.clipboard.writeText(document.getElementById('full-markdown').value)">Copy Full Note as Markdown</button>
  <form style="display:inline" method="post" action="/notes/{{ detail.note_id }}/resummarize">
    <button type="submit">Resummarize</button>
  </form>
  <form style="display:inline" hx-post="/notes/{{ detail.note_id }}/delete" hx-confirm="Delete this note permanently? This cannot be undone.">
    <button type="submit">Delete</button>
  </form>
</p>

<p><a href="/notes">&larr; Back to Notes</a></p>
{% endif %}
{% endblock %}
```

(The Resummarize form is a plain `method="post"` submission — Task 6 makes it redirect back to this page. The Delete form uses `hx-post` specifically so `hx-confirm` shows a real confirmation dialog before anything happens — Task 5 makes it respond with an `HX-Redirect` header, which htmx follows client-side.)

- [ ] **Step 4: Implement `GET /notes/{note_id}`**

Add to `notetaker/dashboard.py`, after the `/notes` route:

```python
@app.get("/notes/{note_id}", response_class=HTMLResponse)
async def notes_detail(request: Request, note_id: str):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "note_detail.html", {"setup_error": str(exc)})

    try:
        detail = service.get_note_detail(config, note_id)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "note_detail.html", {"not_found": str(exc)})

    full_markdown = service.get_note_body(config, note_id)
    return templates.TemplateResponse(request, "note_detail.html", {"detail": detail, "full_markdown": full_markdown})
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "notes_detail" -v`
Expected: PASS, 6 passed.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 243 passed (237 + 6 new).

- [ ] **Step 7: Commit**

```bash
git add notetaker/dashboard.py notetaker/templates/note_detail.html tests/test_dashboard.py
git commit -m "feat: add dashboard note detail page with copy buttons"
```

---

## Task 4: Edit a note (`GET`/`POST /notes/{note_id}/edit`)

**Files:**
- Modify: `notetaker/dashboard.py`
- Create: `notetaker/templates/note_edit.html`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `service.get_note_detail`, `service.update_note(config, note_id, *, title=None, tags=None, summary_text=None, action_items=None)`.
- Produces: `GET /notes/{note_id}/edit` — renders `note_edit.html` pre-filled with the note's current fields. `POST /notes/{note_id}/edit` (form fields `title`, `tags` comma-separated, `summary_text`, `action_items` newline-separated) — calls `service.update_note` with all four fields always set (never `None` — this form always submits every field, so there's no "leave unchanged" case to support here, unlike `update_note`'s own general contract) and redirects (303) to `GET /notes/{note_id}` on success; re-renders the edit form with an inline error on failure.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard.py`:

```python
def test_notes_edit_form_prefills_current_fields(client, monkeypatch, tmp_path):
    from notetaker.service import NoteDetail

    detail = NoteDetail(
        note_id="2026-09-11-standup", title="Standup", date=datetime(2026, 9, 11, 10, 0),
        duration_minutes=5, tags=["proj"], summary_text="old summary",
        action_items=["item one", "item two"], transcript="t",
        path=tmp_path / "2026-09-11-standup.md",
    )
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", lambda config, note_id: detail)

    response = client.get("/notes/2026-09-11-standup/edit")

    assert response.status_code == 200
    assert 'value="Standup"' in response.text
    assert "old summary" in response.text
    assert "item one" in response.text
    assert "item two" in response.text
    assert "proj" in response.text


def test_notes_edit_form_shows_not_found_for_missing_note(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    def fail(config, note_id):
        raise service.ServiceError(f"no note found with id '{note_id}'.")

    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", fail)

    response = client.get("/notes/nonexistent/edit")

    assert response.status_code == 200
    assert "no note found" in response.text


def test_notes_edit_form_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.get("/notes/2026-09-11-standup/edit")

    assert response.status_code == 200
    assert "not set up" in response.text


def test_notes_edit_submit_updates_note_and_redirects(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = {}

    def fake_update_note(config, note_id, *, title, tags, summary_text, action_items):
        calls["note_id"] = note_id
        calls["title"] = title
        calls["tags"] = tags
        calls["summary_text"] = summary_text
        calls["action_items"] = action_items
        return tmp_path / f"{note_id}.md"

    monkeypatch.setattr("notetaker.dashboard.service.update_note", fake_update_note)

    response = client.post(
        "/notes/2026-09-11-standup/edit",
        data={
            "title": "New Title",
            "tags": "proj, planning",
            "summary_text": "new summary",
            "action_items": "item one\nitem two",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/notes/2026-09-11-standup"
    assert calls["note_id"] == "2026-09-11-standup"
    assert calls["title"] == "New Title"
    assert calls["tags"] == ["proj", "planning"]
    assert calls["summary_text"] == "new summary"
    assert calls["action_items"] == ["item one", "item two"]


def test_notes_edit_submit_shows_error_on_failure(client, monkeypatch, tmp_path):
    from notetaker.service import NoteDetail

    detail = NoteDetail(
        note_id="2026-09-11-standup", title="Standup", date=datetime(2026, 9, 11, 10, 0),
        duration_minutes=5, tags=[], summary_text="s", action_items=[], transcript="",
        path=tmp_path / "2026-09-11-standup.md",
    )
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", lambda config, note_id: detail)

    def fail(config, note_id, *, title, tags, summary_text, action_items):
        raise RuntimeError("disk full")

    monkeypatch.setattr("notetaker.dashboard.service.update_note", fail)

    response = client.post(
        "/notes/2026-09-11-standup/edit",
        data={"title": "New Title", "tags": "", "summary_text": "s", "action_items": ""},
    )

    assert response.status_code == 200
    assert "disk full" in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "notes_edit" -v`
Expected: FAIL — 404 (no `/notes/{note_id}/edit` routes yet).

- [ ] **Step 3: Create `note_edit.html`**

Create `notetaker/templates/note_edit.html`:

```html
{% extends "base.html" %}
{% block content %}
{% if setup_error %}
<p class="error">Notetaker is not set up: {{ setup_error }}</p>
{% elif not_found %}
<p class="error">{{ not_found }}</p>
{% else %}
<h2>Edit: {{ detail.title }}</h2>
{% if error %}<p class="error">{{ error }}</p>{% endif %}
<form method="post" action="/notes/{{ detail.note_id }}/edit">
  <label>Title <input type="text" name="title" value="{{ detail.title }}" required></label>
  <label>Tags (comma-separated) <input type="text" name="tags" value="{{ detail.tags | join(', ') }}"></label>
  <label>Summary <textarea name="summary_text">{{ detail.summary_text }}</textarea></label>
  <label>Action Items (one per line) <textarea name="action_items">{{ detail.action_items | join('\n') }}</textarea></label>
  <button type="submit">Save</button>
</form>
<p><a href="/notes/{{ detail.note_id }}">Cancel</a></p>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Implement `GET`/`POST /notes/{note_id}/edit`**

Add to `notetaker/dashboard.py`. First change the `from fastapi.responses import HTMLResponse` import line to:

```python
from fastapi.responses import HTMLResponse, RedirectResponse
```

Then add, after the `/notes/{note_id}` route:

```python
@app.get("/notes/{note_id}/edit", response_class=HTMLResponse)
async def notes_edit_form(request: Request, note_id: str):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "note_edit.html", {"setup_error": str(exc)})

    try:
        detail = service.get_note_detail(config, note_id)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "note_edit.html", {"not_found": str(exc)})

    return templates.TemplateResponse(request, "note_edit.html", {"detail": detail})


@app.post("/notes/{note_id}/edit", response_class=HTMLResponse)
async def notes_edit_submit(
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
        try:
            detail = service.get_note_detail(config, note_id)
        except Exception:
            return templates.TemplateResponse(request, "note_edit.html", {"not_found": str(exc)})
        return templates.TemplateResponse(request, "note_edit.html", {"detail": detail, "error": str(exc)})

    return RedirectResponse(f"/notes/{note_id}", status_code=303)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "notes_edit" -v`
Expected: PASS, 5 passed.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 248 passed (243 + 5 new).

- [ ] **Step 7: Commit**

```bash
git add notetaker/dashboard.py notetaker/templates/note_edit.html tests/test_dashboard.py
git commit -m "feat: add dashboard note edit page"
```

---

## Task 5: Delete a note (`POST /notes/{note_id}/delete`)

**Files:**
- Modify: `notetaker/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `service.delete_note(config, note_id)`.
- Produces: `POST /notes/{note_id}/delete` — deletes the note and responds with an `HX-Redirect: /notes` header (htmx reads this and navigates the browser there — the Delete button's `hx-confirm`, already in `note_detail.html` from Task 3, means this only ever fires after the user confirms) on success; on any exception, re-renders `note_detail.html` with a `not_found`-style error message (the only realistic failure — the note is already gone or unreadable — so there's nothing else to show).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard.py`:

```python
def test_notes_delete_removes_note_and_redirects(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = []
    monkeypatch.setattr(
        "notetaker.dashboard.service.delete_note", lambda config, note_id: calls.append(note_id)
    )

    response = client.post("/notes/2026-09-11-standup/delete")

    assert response.status_code == 200
    assert response.headers["hx-redirect"] == "/notes"
    assert calls == ["2026-09-11-standup"]


def test_notes_delete_shows_error_for_missing_note(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    def fail(config, note_id):
        raise service.ServiceError(f"no note found with id '{note_id}'.")

    monkeypatch.setattr("notetaker.dashboard.service.delete_note", fail)

    response = client.post("/notes/nonexistent/delete")

    assert response.status_code == 200
    assert "no note found" in response.text


def test_notes_delete_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.post("/notes/2026-09-11-standup/delete")

    assert response.status_code == 200
    assert "not set up" in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "notes_delete" -v`
Expected: FAIL — 404 (no `/notes/{note_id}/delete` route yet).

- [ ] **Step 3: Implement `POST /notes/{note_id}/delete`**

Add to `notetaker/dashboard.py`. First change the `from fastapi import FastAPI, Form, Request` import line to:

```python
from fastapi import FastAPI, Form, Request, Response
```

Then add, after the `/notes/{note_id}/edit` POST route:

```python
@app.post("/notes/{note_id}/delete", response_class=HTMLResponse)
async def notes_delete(request: Request, note_id: str):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "note_detail.html", {"setup_error": str(exc)})

    try:
        service.delete_note(config, note_id)
    except Exception as exc:
        return templates.TemplateResponse(request, "note_detail.html", {"not_found": str(exc)})

    return Response(status_code=200, headers={"HX-Redirect": "/notes"})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "notes_delete" -v`
Expected: PASS, 3 passed.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 251 passed (248 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add notetaker/dashboard.py tests/test_dashboard.py
git commit -m "feat: add dashboard note delete action"
```

---

## Task 6: Resummarize a note (`POST /notes/{note_id}/resummarize`)

**Files:**
- Modify: `notetaker/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `service.resummarize_note(config, note_id) -> Path`.
- Produces: `POST /notes/{note_id}/resummarize` — a plain (non-htmx) form submission; redirects (303) to `GET /notes/{note_id}` on success (which shows the newly-resummarized content); on failure, re-renders `note_detail.html` with the note's current (unchanged) detail plus an inline error, or a bare `not_found` message if the note is no longer readable at all.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard.py`:

```python
def test_notes_resummarize_updates_summary_and_redirects(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = []
    monkeypatch.setattr(
        "notetaker.dashboard.service.resummarize_note",
        lambda config, note_id: calls.append(note_id) or (tmp_path / f"{note_id}.md"),
    )

    response = client.post("/notes/2026-09-11-standup/resummarize", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/notes/2026-09-11-standup"
    assert calls == ["2026-09-11-standup"]


def test_notes_resummarize_shows_error_and_detail_on_failure(client, monkeypatch, tmp_path):
    from notetaker.service import NoteDetail

    detail = NoteDetail(
        note_id="2026-09-11-standup", title="Standup", date=datetime(2026, 9, 11, 10, 0),
        duration_minutes=5, tags=[], summary_text="old summary", action_items=[], transcript="t",
        path=tmp_path / "2026-09-11-standup.md",
    )
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", lambda config, note_id: detail)
    monkeypatch.setattr("notetaker.dashboard.service.get_note_body", lambda config, note_id: "raw")

    def fail(config, note_id):
        raise service.ServiceError("resummarization failed: network down")

    monkeypatch.setattr("notetaker.dashboard.service.resummarize_note", fail)

    response = client.post("/notes/2026-09-11-standup/resummarize")

    assert response.status_code == 200
    assert "network down" in response.text
    assert "old summary" in response.text


def test_notes_resummarize_shows_not_found_when_note_missing_entirely(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))

    def fail(config, note_id):
        raise service.ServiceError(f"no note found with id '{note_id}'.")

    monkeypatch.setattr("notetaker.dashboard.service.resummarize_note", fail)
    monkeypatch.setattr("notetaker.dashboard.service.get_note_detail", fail)

    response = client.post("/notes/nonexistent/resummarize")

    assert response.status_code == 200
    assert "no note found" in response.text


def test_notes_resummarize_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.post("/notes/2026-09-11-standup/resummarize")

    assert response.status_code == 200
    assert "not set up" in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "notes_resummarize" -v`
Expected: FAIL — 404 (no `/notes/{note_id}/resummarize` route yet).

- [ ] **Step 3: Implement `POST /notes/{note_id}/resummarize`**

Add to `notetaker/dashboard.py`, after the `/notes/{note_id}/delete` route:

```python
@app.post("/notes/{note_id}/resummarize", response_class=HTMLResponse)
async def notes_resummarize(request: Request, note_id: str):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "note_detail.html", {"setup_error": str(exc)})

    try:
        service.resummarize_note(config, note_id)
    except Exception as exc:
        try:
            detail = service.get_note_detail(config, note_id)
        except Exception:
            return templates.TemplateResponse(request, "note_detail.html", {"not_found": str(exc)})
        full_markdown = service.get_note_body(config, note_id)
        return templates.TemplateResponse(
            request, "note_detail.html", {"detail": detail, "error": str(exc), "full_markdown": full_markdown}
        )

    return RedirectResponse(f"/notes/{note_id}", status_code=303)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "notes_resummarize" -v`
Expected: PASS, 4 passed.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 255 passed (251 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add notetaker/dashboard.py tests/test_dashboard.py
git commit -m "feat: add dashboard note resummarize action"
```

---

## Final check (do this after Task 6, before considering the plan done)

Run the full suite one more time and confirm the final count:

```bash
.venv/bin/pytest -q
```

Expected: `255 passed` (plus 1 deselected if run without `-m "not integration"` filtering is not applied — the integration test is unaffected by this plan either way).

Then do a manual smoke test with a real running server (this plan's automated tests all use `TestClient` — nothing so far has exercised the `hx-confirm`/`HX-Redirect` delete flow in an actual browser, or clicked a real copy button):

```bash
.venv/bin/notetaker dashboard &
sleep 1
open http://127.0.0.1:8420/notes
```

Confirm in the browser: the Notes page loads and lists any existing notes (or shows "No notes found." on a fresh setup); clicking a note opens its detail page with working Copy Summary / Copy Full Note as Markdown buttons (paste somewhere to confirm); Edit opens a pre-filled form and Save returns to the detail page with the new values visible; Delete shows a confirmation dialog and, once confirmed, returns to the notes list with the note gone; Resummarize (if a Provider credential is configured) updates the summary and reloads the detail page. Kill the background process (`kill %1` or `pkill -f "notetaker dashboard"`) when done — do not leave it running.
