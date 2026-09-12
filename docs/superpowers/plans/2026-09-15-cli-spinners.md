# CLI Loading Indicators Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `rich`-based loading indicators to `notetaker start` and `notetaker stop`, so the CLI shows visible progress during operations whose duration is unpredictable (spawning the recorder, or — worse — the summarization API call) instead of sitting silently. This closes out the last piece of the original design spec's CLI-polish scope (Q26 of the original grilling session).

**Architecture:** `stop_session` (in `notetaker/service.py`) gains an optional `on_phase: Callable[[str], None] | None = None` callback parameter — called with a short phase description at two points ("Stopping recorder...", "Summarizing...") — with no other change to its behavior; every existing call site that omits it is unaffected. `cli.py` wraps `start` and `stop` in a `rich.console.Console().status(...)` spinner, wiring `stop`'s spinner text updates directly through `status.update` as the `on_phase` callback. `start_session` is NOT modified — `start`'s spinner shows one static message for the whole operation, matching the original spec's wording ("'Starting recorder...' while verifying BlackHole + spawning the process") rather than multiple phases.

**Tech Stack:** Python 3.10/3.11, `rich` (new dependency).

**Spec:** `docs/superpowers/specs/2026-09-12-notetaker-ui-design.md` (CLI section: "gets `rich`-based spinners...")

## Global Constraints

- Python `>=3.10,<3.12` (per `pyproject.toml`).
- macOS only.
- Run tests with `.venv/bin/pytest -m "not integration" -q` (fast suite).
- New dependency: `rich` (no version pin needed beyond a sane floor — use `rich>=13`).
- **Do not print via `typer.echo` while a `rich.console.Console().status(...)` context is active.** `rich`'s `Live` display (which `Console.status()` uses) assumes it fully controls the terminal's output region; a plain `click`/`typer` echo call bypasses `rich`'s console entirely and can corrupt the spinner's redraw region on a real terminal (this doesn't show up in captured test output, since `rich` disables its live rendering on a non-tty — the bug is real but only visible interactively). Every task below that touches `cli.py` structures the code so all `typer.echo` calls happen after the `with console.status(...)` block exits, never inside it. The one exception is `status.update(...)` itself, which is `rich`'s own API operating on its own `Live` instance — that's always safe to call from inside the block.
- `stop_session`'s existing tests must keep passing unchanged — the new `on_phase` parameter is purely additive with a `None` default.

---

## File Structure

- Modify: `pyproject.toml` — add `rich` dependency.
- Modify: `notetaker/service.py` — add `on_phase` callback parameter to `stop_session`.
- Modify: `tests/test_service.py` — test for the new callback.
- Modify: `notetaker/cli.py` — wrap `start`/`stop` in spinners.
- Modify: `tests/test_cli.py` — update one existing mock that would otherwise break; no other test changes needed.
- Modify: `CLAUDE.md` — document the change.

---

### Task 1: Add the `rich` dependency

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- None — dependency addition only.

- [ ] **Step 1: Add `rich` to `pyproject.toml`'s dependencies**

Change:

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
]
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
]
```

- [ ] **Step 2: Reinstall the project in editable mode**

Run: `.venv/bin/pip install -e ".[dev]"`
Expected: `rich` installs with no errors.

- [ ] **Step 3: Verify the import works**

Run: `.venv/bin/python -c "from rich.console import Console; Console().status('test')"`
Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add rich dependency for CLI loading indicators"
```

---

### Task 2: Add an `on_phase` callback to `stop_session`

**Files:**
- Modify: `notetaker/service.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Produces: `stop_session(info, config, config_dir, end_time=None, on_phase: Callable[[str], None] | None = None) -> Path` — calls `on_phase("Stopping recorder...")` before terminating the recorder, and `on_phase("Summarizing...")` before summarizing, only when `on_phase` is not `None`. No other behavior change.
- Every existing `stop_session` call/test omits `on_phase` and is completely unaffected (the parameter defaults to `None`, and every call is guarded by `if on_phase:`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_service.py`:

```python
def test_stop_session_reports_phases_via_callback(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)

    phases = []
    stop_session(
        _session_info(session_dir),
        Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
        tmp_path,
        on_phase=phases.append,
    )

    assert phases == ["Stopping recorder...", "Summarizing..."]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_service.py -k reports_phases_via_callback -v`
Expected: FAIL with `TypeError: stop_session() got an unexpected keyword argument 'on_phase'`

- [ ] **Step 3: Write minimal implementation**

Add `Callable` to the existing `from typing import` — there isn't one yet, so add a new import line near the top of `notetaker/service.py` (alongside `from pathlib import Path`): `from typing import Callable`.

Replace:

```python
def stop_session(info: SessionInfo, config: Config, config_dir: Path, end_time: datetime | None = None) -> Path:
    session_file = session_file_path(config_dir)

    _terminate_recorder(info.pid)

    transcript_path = info.session_dir / "transcript.txt"
    transcript = transcript_path.read_text() if transcript_path.exists() else ""
    transcript_lines = transcript.splitlines()

    if end_time is None:
        end_time = datetime.now()
    duration_minutes = int((end_time - info.start_time).total_seconds() // 60)

    summary = _summarize_or_fallback(transcript, config)

    note_path = write_note(
        config.notes_dir, info.title, info.start_time, duration_minutes, summary, transcript_lines
    )
    transcript_sidecar_path = note_path.parent / f"{note_path.stem}.transcript.txt"
    transcript_sidecar_path.write_text(transcript)

    shutil.rmtree(info.session_dir, ignore_errors=True)
    session_file.unlink(missing_ok=True)

    return note_path
```

with:

```python
def stop_session(
    info: SessionInfo,
    config: Config,
    config_dir: Path,
    end_time: datetime | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> Path:
    session_file = session_file_path(config_dir)

    if on_phase:
        on_phase("Stopping recorder...")
    _terminate_recorder(info.pid)

    transcript_path = info.session_dir / "transcript.txt"
    transcript = transcript_path.read_text() if transcript_path.exists() else ""
    transcript_lines = transcript.splitlines()

    if end_time is None:
        end_time = datetime.now()
    duration_minutes = int((end_time - info.start_time).total_seconds() // 60)

    if on_phase:
        on_phase("Summarizing...")
    summary = _summarize_or_fallback(transcript, config)

    note_path = write_note(
        config.notes_dir, info.title, info.start_time, duration_minutes, summary, transcript_lines
    )
    transcript_sidecar_path = note_path.parent / f"{note_path.stem}.transcript.txt"
    transcript_sidecar_path.write_text(transcript)

    shutil.rmtree(info.session_dir, ignore_errors=True)
    session_file.unlink(missing_ok=True)

    return note_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: all pass, including every pre-existing `stop_session`/`check_and_salvage_orphan`/`resummarize_note` test (none of them pass `on_phase`, so they're unaffected)

- [ ] **Step 5: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: add on_phase progress callback to stop_session"
```

---

### Task 3: Wrap `start` and `stop` in CLI spinners

**Files:**
- Modify: `notetaker/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `stop_session`'s new `on_phase` parameter from Task 2.
- Produces: `start` shows a single "Starting recorder..." spinner around the salvage-check and `start_session` call; `stop` shows a spinner starting at "Stopping recorder..." that updates to "Summarizing..." via `stop_session`'s callback. No change to any existing message text, ordering (relative to each other), or exit codes — only the addition of a spinner and, for `start`, moving the existing `typer.echo` calls to after the spinner closes (required per the Global Constraints note on not echoing inside an active `Console.status()`).

- [ ] **Step 1: Add the `rich` import and console instance**

At the top of `notetaker/cli.py`, add:

```python
from rich.console import Console
```

right after `import typer`, and add a module-level instance right after `app = typer.Typer()`:

```python
console = Console()
```

- [ ] **Step 2: Update `start`**

Replace:

```python
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
```

with:

```python
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
```

This preserves the exact original message text and order (warning, then recovery message, then error-and-exit, then the two reminders, then the confirmation) — only the two `try/except` blocks now run inside the spinner, with their results captured in local variables and echoed afterward.

- [ ] **Step 3: Update `stop`**

Replace:

```python
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
```

with:

```python
@app.command()
def stop():
    try:
        info = service.read_active_session(CONFIG_DIR)
    except ServiceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    config = load_config()
    with console.status("Stopping recorder...") as status:
        note_path = service.stop_session(info, config, CONFIG_DIR, on_phase=status.update)
    typer.echo(f"Saved note: {note_path}")
```

- [ ] **Step 4: Fix the one existing test whose mock would otherwise break**

`tests/test_cli.py`'s `test_stop_prints_note_path_on_success` mocks `service.stop_session` with a lambda that doesn't accept the new `on_phase` keyword argument `stop()` now passes — without this fix, the test would fail with `TypeError: <lambda>() got an unexpected keyword argument 'on_phase'`. Replace:

```python
def test_stop_prints_note_path_on_success(monkeypatch, tmp_path):
    info = SessionInfo(999999, "Standup", datetime.now(), tmp_path)
    monkeypatch.setattr("notetaker.cli.service.read_active_session", lambda config_dir: info)
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    note_path = tmp_path / "notes" / "2026-09-11-standup.md"
    monkeypatch.setattr("notetaker.cli.service.stop_session", lambda info, config, config_dir: note_path)

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
```

with:

```python
def test_stop_prints_note_path_on_success(monkeypatch, tmp_path):
    info = SessionInfo(999999, "Standup", datetime.now(), tmp_path)
    monkeypatch.setattr("notetaker.cli.service.read_active_session", lambda config_dir: info)
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    note_path = tmp_path / "notes" / "2026-09-11-standup.md"
    monkeypatch.setattr(
        "notetaker.cli.service.stop_session", lambda info, config, config_dir, on_phase=None: note_path
    )

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
```

Every other existing `start`/`stop` test in `tests/test_cli.py` (`test_start_reports_service_error`, `test_start_prints_reminders_and_confirmation_on_success`, `test_start_prints_recovery_message_when_orphan_salvaged`, `test_start_continues_when_salvage_raises_unexpectedly`, `test_stop_reports_service_error_for_no_active_session`) needs no changes — none of them mock `stop_session` with a signature-sensitive lambda, and `rich`'s spinner renders as a no-op under `CliRunner`'s captured (non-tty) output, so none of their output assertions are affected.

- [ ] **Step 5: Run the full fast suite**

Run: `.venv/bin/pytest -m "not integration" -q`
Expected: all tests pass

- [ ] **Step 6: Manually smoke-test in a real terminal**

Run `.venv/bin/notetaker stop` (with no active session, or against a real one if you have BlackHole set up) and visually confirm the spinner renders and updates without any garbled or duplicated output. This is the one thing the automated test suite cannot verify (`rich` disables live rendering under `CliRunner`'s captured output), so it needs a manual look.

- [ ] **Step 7: Commit**

```bash
git add notetaker/cli.py tests/test_cli.py
git commit -m "feat: add loading spinners to start and stop"
```

---

### Task 4: Update `CLAUDE.md`'s architecture summary

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- None — documentation only.

- [ ] **Step 1: Add a note to the `cli.py` bullet**

In `CLAUDE.md`'s "Planned architecture" module list, replace:

```markdown
- `cli.py` — command dispatch (Typer) for `init`, `start`, `stop`, `list`, `show`, `resummarize`, `set-api-key`, `show-api-key`.
```

with:

```markdown
- `cli.py` — command dispatch (Typer) for `init`, `start`, `stop`, `list`, `show`, `resummarize`, `set-api-key`, `show-api-key`. `start`/`stop` show a `rich` loading spinner, since both can block for an unpredictable duration (spawning the recorder; the summarization API call).
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document CLI loading spinners in the architecture summary"
```

---

## Self-Review Notes

- **Spec coverage**: This plan implements the CLI-polish item from the design spec's original grilling session (Q26) — the last piece of that bundled scope not yet delivered. It does not touch `init`'s Whisper-model-download step, which the original grilling scope never called for.
- **Type consistency checked**: `stop_session`'s new `on_phase: Callable[[str], None] | None = None` parameter is used identically in Task 2's implementation, its test, and Task 3's `cli.py` call site.
- **No placeholders**: every step shows the exact code to write.
- **Real bug caught during authoring, not left for review to find**: `tests/test_cli.py`'s `test_stop_prints_note_path_on_success` mocks `stop_session` with a lambda that would break once `on_phase` is passed — this plan updates that mock explicitly rather than discovering it during implementation.
