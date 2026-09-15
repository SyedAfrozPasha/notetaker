# Crash Salvage, Cancel, and Transcript Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the crash-safety gap CLAUDE.md has always claimed but `notetaker` never actually implemented: a Recorder that crashes without a matching `stop` currently leaves its Session directory silently orphaned forever. This plan adds automatic detection and Salvage of an Orphaned session (per `CONTEXT.md`), a `cancel_session` operation for discarding an in-progress Session without producing a Note, and persists each Note's Transcript as its own sidecar file so it survives past the Session (needed by a later Resummarize feature).

**Architecture:** All three capabilities live in `notetaker/service.py`, reusing Plan 1's `read_active_session`/`stop_session` split exactly as that plan's docstring anticipated — `check_and_salvage_orphan` is a thin wrapper that detects a dead-PID session and calls the existing `stop_session`, so salvage gets the transcript sidecar for free. `cli.py`'s `start` command calls the new detection function before starting a new session, so CLI-only users get salvage without needing a background poller (a future menu bar app / dashboard will call the same function on its own polling cadence — that's a later plan, not this one).

**Tech Stack:** Python 3.10/3.11, pytest — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-12-notetaker-ui-design.md` (Session lifecycle section) and `CONTEXT.md` (Session, Cancel, Orphaned session, Salvage, Transcript definitions)

## Global Constraints

- Python `>=3.10,<3.12` (per `pyproject.toml`).
- macOS only.
- Run tests with `.venv/bin/pytest -m "not integration" -q` (fast suite).
- No new dependencies.
- `CLAUDE.md`'s architecture summary must stay in sync with the code — the last task updates it.
- Unlike Plan 1, this plan DOES intentionally change CLI-visible behavior: `start` can now print a recovery message and `stop`/salvage now leave a transcript sidecar file on disk. Both are deliberate, spec-required additions, not refactor drift.

---

## File Structure

- Modify: `notetaker/service.py` — add `check_and_salvage_orphan`, `cancel_session`, a shared `_terminate_recorder` helper (extracted from `stop_session`), and the transcript sidecar write in `stop_session`.
- Modify: `tests/test_service.py` — tests for all of the above.
- Modify: `notetaker/cli.py` — wire `check_and_salvage_orphan` into `start`.
- Modify: `tests/test_cli.py` — update the two existing `start` tests to mock the new call; add a test for the recovery message.
- Modify: `CLAUDE.md` — document the new capabilities.

---

### Task 1: Transcript sidecar persistence in `stop_session`

**Files:**
- Modify: `notetaker/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: nothing new — uses `stop_session`'s existing local variables (`note_path`, `transcript`).
- Produces: `stop_session` now also writes `notes_dir / f"{note_path.stem}.transcript.txt"` containing the raw transcript text, for every call (including the empty-transcript case, where it writes an empty file) — establishing the invariant that every Note has a matching sidecar. No signature change.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_service.py (append)
def test_stop_session_writes_transcript_sidecar_alongside_note(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n[00:00:07] world\n")
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="summary text", action_items=[], tags=[]),
    )

    note_path = stop_session(
        _session_info(session_dir),
        Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
        tmp_path,
    )

    sidecar_path = notes_dir / f"{note_path.stem}.transcript.txt"
    assert sidecar_path.exists()
    assert sidecar_path.read_text() == "[00:00:03] hello\n[00:00:07] world\n"


def test_stop_session_writes_empty_transcript_sidecar_when_no_audio(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)

    note_path = stop_session(
        _session_info(session_dir),
        Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
        tmp_path,
    )

    sidecar_path = notes_dir / f"{note_path.stem}.transcript.txt"
    assert sidecar_path.exists()
    assert sidecar_path.read_text() == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -k transcript_sidecar -v`
Expected: FAIL — `sidecar_path.exists()` is False (no sidecar written yet)

- [ ] **Step 3: Write minimal implementation**

In `notetaker/service.py`, inside `stop_session`, right after `note_path = write_note(...)` and before `shutil.rmtree(...)`:

```python
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
Expected: all pass (the three pre-existing `stop_session` tests plus the two new ones)

- [ ] **Step 5: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: persist each note's transcript as a sidecar file"
```

---

### Task 2: `check_and_salvage_orphan`

**Files:**
- Modify: `notetaker/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `read_active_session`, `pid_alive`, `stop_session`, `ServiceError` (all from Task 1/Plan 1, unchanged).
- Produces: `check_and_salvage_orphan(config: Config, config_dir: Path) -> Path | None` — returns the salvaged Note's path if an Orphaned session was found and salvaged, `None` if there was nothing to salvage (no session file, a corrupt one, or one whose Recorder is still alive).

- [ ] **Step 1: Write the failing tests**

Add `check_and_salvage_orphan` to the existing grouped `from notetaker.service import (...)` block at the top of `tests/test_service.py` (it already imports `ServiceError`, `SessionInfo`, `start_session`, etc. — add this name alphabetically alongside them, don't add a second import statement).

```python
# tests/test_service.py (append below the existing tests)
def test_check_and_salvage_orphan_returns_none_when_no_session_file(tmp_path):
    assert check_and_salvage_orphan(_config(tmp_path), tmp_path) is None


def test_check_and_salvage_orphan_returns_none_when_session_file_corrupt(tmp_path):
    (tmp_path / "current_session.json").write_text("{not valid json")
    assert check_and_salvage_orphan(_config(tmp_path), tmp_path) is None


def test_check_and_salvage_orphan_returns_none_when_pid_alive(tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {"pid": os.getpid(), "title": "x", "start_time": "2026-09-11T10:00:00", "session_dir": str(tmp_path)}
        )
    )
    assert check_and_salvage_orphan(_config(tmp_path), tmp_path) is None
    assert session_file.exists()  # untouched — a live session must not be disturbed


def test_check_and_salvage_orphan_salvages_dead_session_into_note(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n")
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {
                "pid": 999999,
                "title": "Standup",
                "start_time": "2026-09-11T10:00:00",
                "session_dir": str(session_dir),
            }
        )
    )
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="summary text", action_items=[], tags=[]),
    )

    note_path = check_and_salvage_orphan(
        Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path
    )

    assert note_path is not None
    assert "summary text" in note_path.read_text()
    assert not session_file.exists()
    assert not session_dir.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -k check_and_salvage_orphan -v`
Expected: FAIL with `ImportError: cannot import name 'check_and_salvage_orphan'`

- [ ] **Step 3: Write minimal implementation**

```python
# notetaker/service.py (append)
def check_and_salvage_orphan(config: Config, config_dir: Path) -> Path | None:
    try:
        info = read_active_session(config_dir)
    except ServiceError:
        return None
    if pid_alive(info.pid):
        return None
    return stop_session(info, config, config_dir)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: add check_and_salvage_orphan to detect and recover a crashed session"
```

---

### Task 3: Extract `_terminate_recorder`, add `cancel_session`

**Files:**
- Modify: `notetaker/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Produces: a private `_terminate_recorder(pid: int) -> None` helper (the SIGTERM-then-poll-up-to-30s logic, extracted verbatim from `stop_session` so `stop_session` and the new `cancel_session` share one implementation instead of duplicating it); `cancel_session(info: SessionInfo, config_dir: Path) -> None` — terminates the Recorder if it's alive, then discards the Session directory and session file without writing a Note.
- This extraction is a behavior-preserving refactor of `stop_session`: its existing tests must keep passing unchanged.

- [ ] **Step 1: Write the failing tests**

Add `cancel_session` to the existing grouped `from notetaker.service import (...)` block at the top of `tests/test_service.py`, alphabetically alongside the other names. Add a plain top-level `import signal` too (used by the second test below).

```python
# tests/test_service.py (append below the existing tests)
def test_cancel_session_discards_without_writing_note(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n")
    session_file = tmp_path / "current_session.json"
    session_file.write_text("{}")
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)

    cancel_session(_session_info(session_dir), tmp_path)

    assert not session_dir.exists()
    assert not session_file.exists()


def test_cancel_session_sends_sigterm_and_waits_for_live_pid(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    session_file = tmp_path / "current_session.json"
    session_file.write_text("{}")

    calls = {"kill": None, "sleeps": 0}
    pid_states = iter([True, True, False])
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: next(pid_states))
    monkeypatch.setattr("notetaker.service.os.kill", lambda pid, sig: calls.__setitem__("kill", (pid, sig)))
    monkeypatch.setattr(
        "notetaker.service.time.sleep", lambda s: calls.__setitem__("sleeps", calls["sleeps"] + 1)
    )

    cancel_session(_session_info(session_dir), tmp_path)

    assert calls["kill"] == (999999, signal.SIGTERM)
    assert calls["sleeps"] == 1
    assert not session_dir.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -k cancel_session -v`
Expected: FAIL with `ImportError: cannot import name 'cancel_session'`

- [ ] **Step 3: Write minimal implementation**

In `notetaker/service.py`, extract the termination logic and add `cancel_session`:

```python
def _terminate_recorder(pid: int) -> None:
    if pid_alive(pid):
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            if not pid_alive(pid):
                break
            time.sleep(1)
```

Then replace `stop_session`'s existing termination block:

```python
    if pid_alive(info.pid):
        os.kill(info.pid, signal.SIGTERM)
        for _ in range(30):
            if not pid_alive(info.pid):
                break
            time.sleep(1)
```

with:

```python
    _terminate_recorder(info.pid)
```

And add:

```python
def cancel_session(info: SessionInfo, config_dir: Path) -> None:
    session_file = session_file_path(config_dir)

    _terminate_recorder(info.pid)

    shutil.rmtree(info.session_dir, ignore_errors=True)
    session_file.unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: all pass, including the pre-existing `stop_session` tests (confirming the extraction didn't change `stop_session`'s behavior)

- [ ] **Step 5: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: extract _terminate_recorder helper and add cancel_session"
```

---

### Task 4: Wire salvage detection into `cli.py`'s `start`

**Files:**
- Modify: `notetaker/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `service.check_and_salvage_orphan(config, config_dir) -> Path | None` from Task 2.
- Produces: `start` now calls this before `service.start_session`, printing a recovery message when a Path comes back. No change to `start`'s exit codes or its existing success/error message text — only an additional line that appears before them when salvage occurs.

- [ ] **Step 1: Update `notetaker/cli.py`'s `start` command**

Replace:

```python
@app.command()
def start(title: str):
    config = load_config()
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
    salvaged_path = service.check_and_salvage_orphan(config, CONFIG_DIR)
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

- [ ] **Step 2: Update the two existing `start` tests to mock the new call**

In `tests/test_cli.py`, both `test_start_reports_service_error` and `test_start_prints_reminders_and_confirmation_on_success` currently only mock `notetaker.cli.load_config` and `notetaker.cli.service.start_session`. Without mocking `check_and_salvage_orphan` too, `start` would call it with the real `notetaker.cli.CONFIG_DIR` (this machine's actual `~/.notetaker`) — never do this in a test. Add a mock to both:

```python
def test_start_reports_service_error(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.cli.service.check_and_salvage_orphan", lambda config, config_dir: None)

    def fail(*a, **k):
        raise ServiceError("BlackHole is not active. Run `notetaker init` for setup instructions.")

    monkeypatch.setattr("notetaker.cli.service.start_session", fail)

    result = runner.invoke(app, ["start", "Standup"])

    assert result.exit_code == 1
    assert "BlackHole is not active" in result.output


def test_start_prints_reminders_and_confirmation_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.cli.service.check_and_salvage_orphan", lambda config, config_dir: None)
    monkeypatch.setattr(
        "notetaker.cli.service.start_session",
        lambda title, config, config_dir: SessionInfo(12345, title, datetime.now(), tmp_path),
    )

    result = runner.invoke(app, ["start", "Standup"])

    assert result.exit_code == 0
    assert "microphone access" in result.output
    assert "Teams will not show" in result.output
    assert "Recording started: Standup" in result.output
```

- [ ] **Step 3: Add a new test for the recovery message**

```python
def test_start_prints_recovery_message_when_orphan_salvaged(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    salvaged_note_path = tmp_path / "notes" / "2026-09-10-standup.md"
    monkeypatch.setattr(
        "notetaker.cli.service.check_and_salvage_orphan", lambda config, config_dir: salvaged_note_path
    )
    monkeypatch.setattr(
        "notetaker.cli.service.start_session",
        lambda title, config, config_dir: SessionInfo(12345, title, datetime.now(), tmp_path),
    )

    result = runner.invoke(app, ["start", "Standup"])

    assert result.exit_code == 0
    assert f"Recovered a crashed session and saved it as a note: {salvaged_note_path}" in result.output
    assert "Recording started: Standup" in result.output
```

- [ ] **Step 4: Run the full fast suite**

Run: `.venv/bin/pytest -m "not integration" -q`
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add notetaker/cli.py tests/test_cli.py
git commit -m "feat: salvage a crashed session automatically before starting a new one"
```

---

### Task 5: Update `CLAUDE.md`'s architecture summary

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- None — documentation only.

- [ ] **Step 1: Replace the `service.py` bullet**

In `CLAUDE.md`'s "Planned architecture" module list, replace:

```markdown
- `service.py` — all operational logic (start/stop/list/show/init), used by `cli.py` and (from here on) every other UI surface. Raises `ServiceError` for user-facing failures; never prints anything itself — `cli.py` is a thin adapter that translates its results into `typer.echo` calls and exit codes.
```

with:

```markdown
- `service.py` — all operational logic (start/stop/list/show/init/cancel), used by `cli.py` and (from here on) every other UI surface. Raises `ServiceError` for user-facing failures; never prints anything itself — `cli.py` is a thin adapter that translates its results into `typer.echo` calls and exit codes. Detects and salvages an Orphaned session (a crashed Recorder from a previous run) via `check_and_salvage_orphan`, called before every `start`; `cancel_session` discards an in-progress Session without producing a Note. Every `stop`/salvage also writes a `<note-id>.transcript.txt` sidecar next to the note, preserving the raw transcript for future resummarization.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document salvage, cancel, and transcript sidecar in the architecture summary"
```

---

## Self-Review Notes

- **Spec coverage**: This plan implements the spec's "Session lifecycle" section in full (Stop/Cancel/Salvage as three distinct endings, transcript persistence) — it does not touch Resummarize (needs the sidecar this plan creates, but is a separate plan), the menu bar app, or the dashboard's own polling loop, all of which are later plans that will call `check_and_salvage_orphan` themselves.
- **Type consistency checked**: `check_and_salvage_orphan(config: Config, config_dir: Path) -> Path | None` and `cancel_session(info: SessionInfo, config_dir: Path) -> None` are used identically in every task and in the `cli.py` dispatch in Task 4.
- **No placeholders**: every step shows the exact code to write.
- **Behavior-change note carried over from Plan 1's review**: this plan deliberately reuses `stop_session` unchanged for the salvage path — Plan 1's final review already confirmed `stop_session`'s `unlink(missing_ok=True)` and widened exception handling make it safe to call on a session whose file may already be gone or malformed, which is exactly what `check_and_salvage_orphan` needed.
