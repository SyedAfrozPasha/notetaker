# Dashboard Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/settings` page to the local web dashboard (`notetaker/dashboard.py`) covering the last two items in the design spec's Dashboard feature list — config editing (`notes_dir`/`whisper_model`/`ai_provider`) and Claude API key management (masked view, live-validated set) — using only the already-complete `notetaker/service.py` functions.

**Architecture:** Three new routes on the existing `notetaker/dashboard.py` FastAPI app: `GET /settings` (view), `POST /settings/config`, `POST /settings/credential`. One new full-page Jinja2 template (`settings.html`) extends the existing `base.html`. Both POST routes follow the same shape already established by the notes-editing routes: POST-redirect-GET (303) on success, and — learning directly from the fix the previous plan's final review required — re-render with the user's SUBMITTED values (never a stale re-fetch) plus an inline error on failure. The credential form is the one deliberate exception: it is never pre-filled with a previously-submitted value, successful or not, since it's a secret.

**Tech Stack:** No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-12-notetaker-ui-design.md` (see "Feature scope by surface > Dashboard" — the last two bullets: "Config editing: notes directory, provider choice, whisper model. Explicitly NOT the API key path via env var" and "Provider credential management: view (masked, e.g. `sk-ant-••••1234`) and set the Claude API key, stored via `keyring` in the macOS Keychain... Saving validates the key with a live API check before accepting it." This closes out the Dashboard's full feature list from the original design spec). Domain vocabulary: `CONTEXT.md` (Provider credential).

## Global Constraints

- Config editing covers exactly `notes_dir`, `whisper_model`, `ai_provider` — matching `notetaker/config.py`'s `UPDATABLE_KEYS`. Never `ai_model`, never `api_key_env` — the spec explicitly excludes the API-key-via-env-var path from any UI, dashboard included.
- The Claude API key is never written anywhere but the macOS Keychain (already guaranteed by `service.save_provider_credential`/`credentials.py` — this plan doesn't touch that layer, just calls it).
- The credential form field is never pre-filled with a previously-submitted API key, on success or failure — a `<input type="password">` that always renders empty. This is different from the config form, which DOES redisplay submitted values on failure (config values aren't secrets).
- A route that mutates state (`update_config`, `save_provider_credential`) catches broad `Exception`, not just `service.ServiceError` — matching every other mutating route already in `dashboard.py` (`start_session`, `stop_session`, `cancel_session`, `update_note`, `delete_note`, `resummarize_note` all do this).
- Every route catches `service.ServiceError` from `service.get_config(CONFIG_PATH)` first and renders a `setup_error` message — never a raw `ConfigError` traceback. Identical to every existing route in this file.
- On a failed save, re-render with the values the user just submitted (built as a plain dict — Jinja2's `{{ x.field }}` syntax works identically on a dict via its attribute-then-item-lookup fallback, exactly as already established for `POST /notes/{note_id}/edit`'s failure path) — never a fresh `get_config()`/`get_masked_provider_credential()` re-fetch, which would silently discard what the user typed.
- Do not touch `notetaker/cli.py` or `notetaker/menubar.py` in this plan.
- Run `.venv/bin/pytest -q` after every task; it must stay green (currently 257 passed) with only the new tests added by that task on top.

---

## Task 1: `GET /settings` — view current config and masked credential

**Files:**
- Modify: `notetaker/dashboard.py`
- Modify: `notetaker/templates/base.html`
- Create: `notetaker/templates/settings.html`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `service.get_config(CONFIG_PATH) -> Config`, `service.get_masked_provider_credential(config) -> str | None`.
- Produces: `GET /settings` — renders `settings.html` with `{"config": config, "masked_credential": masked_credential}` on success, or `{"setup_error": str(exc)}` if config can't be loaded. Establishes the `settings.html` template both later tasks' POST routes reuse for their own success/failure renders.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard.py` (the file already imports `datetime`, `pytest`, `TestClient`, `service`, `Config`, `app`, and has the `client` fixture and `_config` helper — reuse them):

```python
def test_settings_page_shows_current_config_and_masked_credential(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr(
        "notetaker.dashboard.service.get_masked_provider_credential", lambda config: "sk-ant••••1234"
    )

    response = client.get("/settings")

    assert response.status_code == 200
    assert "sk-ant••••1234" in response.text
    assert 'value="tiny"' in response.text
    assert '<option value="claude" selected>' in response.text


def test_settings_page_shows_not_set_when_no_credential(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_masked_provider_credential", lambda config: None)

    response = client.get("/settings")

    assert response.status_code == 200
    assert "Not set" in response.text


def test_settings_page_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.get("/settings")

    assert response.status_code == 200
    assert "not set up" in response.text


def test_base_layout_has_settings_nav_link(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/settings"' in response.text
```

(`_config(tmp_path)` returns `Config(tmp_path / "notes", "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY")` — `whisper_model="tiny"`, `ai_provider="claude"` — that's why the first test asserts `value="tiny"` and the `claude` option is `selected`.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "settings_page or settings_nav_link" -v`
Expected: FAIL — 404 for every `/settings` request (route doesn't exist yet), and the nav-link test fails since `base.html` has no Settings link yet.

- [ ] **Step 3: Add the nav link**

In `notetaker/templates/base.html`, change:

```html
  <nav><a href="/">Dashboard</a> | <a href="/notes">Notes</a></nav>
```

to:

```html
  <nav><a href="/">Dashboard</a> | <a href="/notes">Notes</a> | <a href="/settings">Settings</a></nav>
```

- [ ] **Step 4: Create `settings.html`**

Create `notetaker/templates/settings.html`:

```html
{% extends "base.html" %}
{% block content %}
{% if setup_error %}
<p class="error">Notetaker is not set up: {{ setup_error }}</p>
{% else %}
<h2>Settings</h2>

<h3>Configuration</h3>
{% if config_error %}<p class="error">{{ config_error }}</p>{% endif %}
<form method="post" action="/settings/config">
  <label>Notes directory <input type="text" name="notes_dir" value="{{ config.notes_dir }}" required></label>
  <label>Whisper model <input type="text" name="whisper_model" value="{{ config.whisper_model }}" required></label>
  <label>AI provider
    <select name="ai_provider">
      <option value="claude" {% if config.ai_provider == "claude" %}selected{% endif %}>Claude</option>
      <option value="apple_local" {% if config.ai_provider == "apple_local" %}selected{% endif %}>Apple Local (apfel)</option>
    </select>
  </label>
  <button type="submit">Save Configuration</button>
</form>

<h3>Claude API Key</h3>
{% if credential_error %}<p class="error">{{ credential_error }}</p>{% endif %}
<p>Current key: {{ masked_credential or "Not set" }}</p>
<form method="post" action="/settings/credential">
  <label>New API Key <input type="password" name="api_key" required></label>
  <button type="submit">Save API Key</button>
</form>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Implement `GET /settings`**

Add to `notetaker/dashboard.py`, after the `/notes/{note_id}/resummarize` route (at the end of the file):

```python
@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "settings.html", {"setup_error": str(exc)})

    masked_credential = service.get_masked_provider_credential(config)
    return templates.TemplateResponse(
        request, "settings.html", {"config": config, "masked_credential": masked_credential}
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "settings_page or settings_nav_link" -v`
Expected: PASS, 4 passed.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 261 passed (257 + 4 new).

- [ ] **Step 8: Commit**

```bash
git add notetaker/dashboard.py notetaker/templates/base.html notetaker/templates/settings.html tests/test_dashboard.py
git commit -m "feat: add dashboard settings page showing config and masked credential"
```

---

## Task 2: `POST /settings/config`

**Files:**
- Modify: `notetaker/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `service.update_config(updates: dict, config_path: Path) -> Config`.
- Produces: `POST /settings/config` (form fields `notes_dir`, `whisper_model`, `ai_provider`, all required) — calls `service.update_config({"notes_dir": ..., "whisper_model": ..., "ai_provider": ...}, CONFIG_PATH)`; redirects (303) to `GET /settings` on success; on any `Exception`, re-renders `settings.html` with the SUBMITTED values (a plain dict, not a re-fetched `Config`) plus a `config_error` message — the masked credential still comes from a fresh `get_masked_provider_credential(config)` call using the `config` already fetched at the top of this request (credentials aren't affected by a failed config save, so no need to re-fetch that separately).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard.py`:

```python
def test_settings_update_config_saves_and_redirects(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = {}

    def fake_update_config(updates, path):
        calls.update(updates)
        return _config(tmp_path)

    monkeypatch.setattr("notetaker.dashboard.service.update_config", fake_update_config)

    response = client.post(
        "/settings/config",
        data={"notes_dir": "/new/notes", "whisper_model": "small", "ai_provider": "apple_local"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    assert calls == {"notes_dir": "/new/notes", "whisper_model": "small", "ai_provider": "apple_local"}


def test_settings_update_config_shows_error_and_preserves_submitted_values_on_failure(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_masked_provider_credential", lambda config: None)

    def fail(updates, path):
        raise service.ServiceError("Unknown ai_provider 'bogus' — expected one of ('claude', 'apple_local').")

    monkeypatch.setattr("notetaker.dashboard.service.update_config", fail)

    response = client.post(
        "/settings/config",
        data={"notes_dir": "/attempted/notes", "whisper_model": "small", "ai_provider": "bogus"},
    )

    assert response.status_code == 200
    assert "Unknown ai_provider" in response.text
    assert 'value="/attempted/notes"' in response.text
    assert 'value="small"' in response.text


def test_settings_update_config_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.post(
        "/settings/config",
        data={"notes_dir": "/x", "whisper_model": "tiny", "ai_provider": "claude"},
    )

    assert response.status_code == 200
    assert "not set up" in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "settings_update_config" -v`
Expected: FAIL — 404 (no `/settings/config` route yet).

- [ ] **Step 3: Implement `POST /settings/config`**

Add to `notetaker/dashboard.py`, after the `/settings` route:

```python
@app.post("/settings/config", response_class=HTMLResponse)
async def settings_update_config(
    request: Request,
    notes_dir: str = Form(...),
    whisper_model: str = Form(...),
    ai_provider: str = Form(...),
):
    try:
        config = service.get_config(CONFIG_PATH)
    except service.ServiceError as exc:
        return templates.TemplateResponse(request, "settings.html", {"setup_error": str(exc)})

    try:
        service.update_config(
            {"notes_dir": notes_dir, "whisper_model": whisper_model, "ai_provider": ai_provider}, CONFIG_PATH
        )
    except Exception as exc:
        submitted = {"notes_dir": notes_dir, "whisper_model": whisper_model, "ai_provider": ai_provider}
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
```

Change the `from fastapi.responses import HTMLResponse, RedirectResponse` import line — it already imports both names, no change needed here (this route reuses the same import Task 4 of the previous plan added).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "settings_update_config" -v`
Expected: PASS, 3 passed.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 264 passed (261 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add notetaker/dashboard.py tests/test_dashboard.py
git commit -m "feat: add dashboard config update action"
```

---

## Task 3: `POST /settings/credential`, and CLAUDE.md update

**Files:**
- Modify: `notetaker/dashboard.py`
- Modify: `CLAUDE.md`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `service.save_provider_credential(config, api_key) -> None`.
- Produces: `POST /settings/credential` (form field `api_key`, required) — calls `service.save_provider_credential(config, api_key)`; redirects (303) to `GET /settings` on success; on any `Exception` (e.g. the key is rejected by Anthropic's live validation, or the provider isn't `claude`), re-renders `settings.html` with the ALREADY-FETCHED `config` (config values are never wrong here since this route never mutates them) plus a `credential_error` message — the submitted `api_key` itself is never placed back into the response in any form.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard.py`:

```python
def test_settings_update_credential_saves_and_redirects(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    calls = {}

    def fake_save_provider_credential(config, api_key):
        calls["api_key"] = api_key

    monkeypatch.setattr("notetaker.dashboard.service.save_provider_credential", fake_save_provider_credential)

    response = client.post(
        "/settings/credential", data={"api_key": "sk-ant-new-key"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    assert calls["api_key"] == "sk-ant-new-key"


def test_settings_update_credential_shows_error_and_never_echoes_the_submitted_key(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.dashboard.service.get_config", lambda path: _config(tmp_path))
    monkeypatch.setattr("notetaker.dashboard.service.get_masked_provider_credential", lambda config: None)

    def fail(config, api_key):
        raise service.ServiceError("That API key was rejected by Anthropic's API — check it and try again.")

    monkeypatch.setattr("notetaker.dashboard.service.save_provider_credential", fail)

    response = client.post("/settings/credential", data={"api_key": "sk-ant-rejected-key"})

    assert response.status_code == 200
    assert "rejected by Anthropic" in response.text
    assert "sk-ant-rejected-key" not in response.text


def test_settings_update_credential_shows_setup_error_when_config_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.dashboard.CONFIG_PATH", tmp_path / "config.yaml")

    def fail(path):
        raise service.ServiceError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.dashboard.service.get_config", fail)

    response = client.post("/settings/credential", data={"api_key": "sk-ant-x"})

    assert response.status_code == 200
    assert "not set up" in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "settings_update_credential" -v`
Expected: FAIL — 404 (no `/settings/credential` route yet).

- [ ] **Step 3: Implement `POST /settings/credential`**

Add to `notetaker/dashboard.py`, after the `/settings/config` route:

```python
@app.post("/settings/credential", response_class=HTMLResponse)
async def settings_update_credential(request: Request, api_key: str = Form(...)):
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -k "settings_update_credential" -v`
Expected: PASS, 3 passed.

- [ ] **Step 5: Update CLAUDE.md's architecture summary**

In `CLAUDE.md`, find the `dashboard.py` bullet (it currently ends with something like `"...Also serves a notes-management surface (...) as plain multi-page navigation over service.py. Config and credential management remain later work."`). Replace the final sentence — the exact text to remove is:

```
Config and credential management remain later work.
```

Replace it with:

```
Also serves a `/settings` page for config editing (`notes_dir`/`whisper_model`/`ai_provider`, via `POST /settings/config`) and Claude API key management (masked view, live-validated set via `POST /settings/credential`, Keychain-backed) — this closes out the design spec's full Dashboard feature list.
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, 267 passed (264 + 3 new).

- [ ] **Step 7: Commit**

```bash
git add notetaker/dashboard.py CLAUDE.md tests/test_dashboard.py
git commit -m "feat: add dashboard credential update action"
```

---

## Final check (do this after Task 3, before considering the plan done)

Run the full suite one more time and confirm the final count:

```bash
.venv/bin/pytest -q
```

Expected: `267 passed` (plus 1 deselected if run without `-m "not integration"` filtering is not applied — the integration test is unaffected by this plan either way).

Then do a manual smoke test with a real running server (this plan's automated tests all use `TestClient`, which can't run the actual `validate_claude_api_key` live-API-check path end to end):

```bash
.venv/bin/notetaker dashboard &
sleep 1
open http://127.0.0.1:8420/settings
```

Confirm in the browser: the Settings page loads showing the current config values and either a masked key or "Not set"; changing the whisper model and saving redirects back to Settings showing the new value; submitting an invalid `ai_provider` some other way than the dropdown (e.g. via curl) shows a clean inline error with the config form still showing what was submitted; entering a real (or deliberately wrong) API key and saving shows either a masked confirmation or a rejection message, and the key itself is never visible in the page source afterward either way. Kill the background process (`kill %1` or `pkill -f "notetaker dashboard"`) when done — do not leave it running.
