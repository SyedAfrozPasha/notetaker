# Menu Bar App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the menu bar app from the design spec — a minimal `rumps`-based macOS menu bar item showing live in-progress/elapsed-time status, with one-click Start/Stop and native notifications on save-complete, summarization-failure, and crash-detected. This is the first UI surface (besides the CLI) to call `check_and_salvage_orphan` on its own polling cadence, which makes a real, previously-deferred concurrency gap in that function load-bearing — this plan fixes it first.

**Architecture:** `notetaker/menubar.py` is a new module: a handful of pure, fully-testable helper functions (elapsed-time formatting, menu text, auto-generated titles, detecting a failed summarization from a note's content) plus a `NotetakerMenuBarApp(rumps.App)` class whose methods are thin wrappers calling those helpers and `notetaker.service`. A `rumps.Timer` polls every 2 seconds — matching the polling cadence already established for live status elsewhere in the design — to update the displayed state and to call `check_and_salvage_orphan`, catching a crash before the user ever notices anything was wrong. `notetaker/service.py` gains one new function (`get_current_session_status`, a non-raising status check for display) and one fix (atomic claim-then-salvage in `check_and_salvage_orphan`, so a CLI `start` and the menu bar app's timer can never both salvage the same orphan). A new `notetaker menubar` CLI command launches the app (this is what a `brew services`-managed process will invoke — the actual Homebrew formula/packaging work is a separate, later plan, not this one).

**Tech Stack:** Python 3.10/3.11, `rumps` (new dependency, wraps PyObjC/AppKit).

**Spec:** `docs/superpowers/specs/2026-09-12-notetaker-ui-design.md` (Menu bar app section) and `CONTEXT.md` (Session, Cancel, Orphaned session, Salvage definitions)

## Global Constraints

- Python `>=3.10,<3.12` (per `pyproject.toml`).
- macOS only.
- Run tests with `.venv/bin/pytest -m "not integration" -q` (fast suite).
- New dependency: `rumps` (no version pin needed beyond a sane floor — use `rumps>=0.4`).
- **CRITICAL test-isolation requirement, a new category of real-system risk this plan introduces:** `rumps.alert(...)` shows a modal dialog and **blocks the calling thread until a human clicks a button** — verified directly against the installed `rumps` source (it calls `NSAlert`'s `runModal`). If any automated test calls the real `rumps.alert` or `rumps.notification`, the test run will hang waiting for a UI interaction that will never come in a non-interactive test run. Every task below that touches code calling either function explicitly mocks `notetaker.menubar.rumps.alert` / `notetaker.menubar.rumps.notification` — never invoke the real ones in a test.
- **`rumps.App`/`rumps.MenuItem`/`rumps.Timer` can be constructed and manipulated (setting `.title`, calling `.start()`) in a plain Python process without entering `rumps.App.run()`'s blocking event loop** — verified directly. Tests construct a real `NotetakerMenuBarApp()` instance and call its methods directly; they never call `.run()`.
- `check_and_salvage_orphan`'s pre-existing tests must keep passing unchanged — the concurrency fix preserves every current success/no-op case exactly; it only changes what happens when a second concurrent caller loses a race, and what happens when `stop_session` itself fails mid-salvage (see Task 1).
- Homebrew formula / `brew services` packaging for the menu bar app is explicitly OUT of scope for this plan — a later, separate plan.

---

## File Structure

- Modify: `notetaker/service.py` — atomic claim in `check_and_salvage_orphan`; new `get_current_session_status`.
- Modify: `tests/test_service.py` — new tests for both.
- Modify: `pyproject.toml` — add `rumps` dependency.
- Create: `notetaker/menubar.py` — pure helpers + `NotetakerMenuBarApp`.
- Create: `tests/test_menubar.py`.
- Modify: `notetaker/cli.py` — `menubar` command.
- Modify: `tests/test_cli.py` — test for the new command (mocking the app class entirely, since `.run()` blocks forever).
- Modify: `CLAUDE.md` — document the new module and command.

---

### Task 1: Make `check_and_salvage_orphan` safe for concurrent callers

**Files:**
- Modify: `notetaker/service.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Produces: `check_and_salvage_orphan(config, config_dir) -> Path | None` — same signature and same return contract as before (a `Path` when a salvage happened, `None` otherwise), but now claims the session file via an atomic rename before salvaging. A second concurrent caller that loses the race gets `None` (not an exception). If `stop_session` itself fails after the claim, the session file is restored to its original name so a future attempt can retry — matching the pre-fix behavior where a failed salvage never lost the orphan's discoverability.
- Every existing caller and test is unaffected in the success/no-op cases: no session file, a corrupt one, or a live pid all behave exactly as before.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_service.py`:

```python
def test_check_and_salvage_orphan_returns_none_when_claim_loses_race(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
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

    def fake_pid_alive(pid):
        # Simulate a concurrent caller (e.g. the menu bar app's timer) winning
        # the claim race in the window between our pid check and our own
        # rename attempt.
        session_file.rename(session_file.with_suffix(".salvaging"))
        return False

    monkeypatch.setattr("notetaker.service.pid_alive", fake_pid_alive)

    result = check_and_salvage_orphan(
        Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path
    )

    assert result is None


def test_check_and_salvage_orphan_restores_session_file_when_stop_session_fails(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
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

    def raise_error(info, config, config_dir, end_time=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr("notetaker.service.stop_session", raise_error)

    with pytest.raises(RuntimeError, match="disk full"):
        check_and_salvage_orphan(
            Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path
        )

    # The claim must be released so a future attempt can retry — the session
    # file is back under its original name, not lost.
    assert session_file.exists()
    assert not session_file.with_suffix(".salvaging").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -k "claim_loses_race or restores_session_file" -v`
Expected: `test_check_and_salvage_orphan_returns_none_when_claim_loses_race` FAILS — the current code has no rename-and-catch step of its own, so after `fake_pid_alive`'s side-effecting rename returns `False`, the current implementation proceeds straight to `stop_session(...)` and successfully produces a real `Path`, not `None`.

`test_check_and_salvage_orphan_restores_session_file_when_stop_session_fails` is NOT expected to fail against the current code — this is a known, deliberate property of this specific test. The current implementation never touches `session_file` at all before calling `stop_session`, so when `stop_session` raises, `session_file` is trivially still present (never having been moved) and no `.salvaging` file was ever created — both assertions pass vacuously, without the claim/restore mechanism this test is meant to protect actually existing yet. Run it anyway to confirm it does pass at this point (for the trivial reason above, not because the feature exists); its real job is to keep passing for the *right* reason once Step 3's implementation exists, catching a future regression where the restore-on-failure behavior is accidentally dropped.

- [ ] **Step 3: Write minimal implementation**

Replace:

```python
def check_and_salvage_orphan(config: Config, config_dir: Path) -> Path | None:
    """Detects and salvages an Orphaned session: a Session whose Recorder died
    without a matching `stop` (see CONTEXT.md). Not safe for concurrent
    callers — there is no locking between the read and the salvage, so this
    assumes a single caller at a time. Fine for today's single CLI
    invocation; a future poller (e.g. a menu bar app or dashboard) calling
    this on its own cadence will need an atomic claim added here first.
    """
    try:
        info = read_active_session(config_dir)
    except ServiceError:
        return None
    if pid_alive(info.pid):
        return None
    transcript_path = info.session_dir / "transcript.txt"
    if transcript_path.exists():
        end_time = datetime.fromtimestamp(transcript_path.stat().st_mtime)
    else:
        end_time = info.start_time
    return stop_session(info, config, config_dir, end_time=end_time)
```

with:

```python
def check_and_salvage_orphan(config: Config, config_dir: Path) -> Path | None:
    """Detects and salvages an Orphaned session: a Session whose Recorder died
    without a matching `stop` (see CONTEXT.md). Safe for concurrent callers
    (e.g. a menu bar app's poller and a CLI `start` running at the same
    moment): the session file is atomically renamed to claim it before
    salvaging, so a second caller's claim attempt finds nothing to rename and
    returns None instead of double-salvaging the same session. If salvaging
    itself fails, the claim is released (the file restored to its original
    name) so a future attempt can retry rather than losing the orphan.
    """
    try:
        info = read_active_session(config_dir)
    except ServiceError:
        return None
    if pid_alive(info.pid):
        return None
    session_file = session_file_path(config_dir)
    claim_path = session_file.with_suffix(".salvaging")
    try:
        session_file.rename(claim_path)
    except FileNotFoundError:
        return None
    transcript_path = info.session_dir / "transcript.txt"
    if transcript_path.exists():
        end_time = datetime.fromtimestamp(transcript_path.stat().st_mtime)
    else:
        end_time = info.start_time
    try:
        note_path = stop_session(info, config, config_dir, end_time=end_time)
    except Exception:
        claim_path.rename(session_file)
        raise
    claim_path.unlink(missing_ok=True)
    return note_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: all pass, including every pre-existing `check_and_salvage_orphan` test (`test_check_and_salvage_orphan_returns_none_when_no_session_file`, `..._when_session_file_corrupt`, `..._when_pid_alive`, `..._salvages_dead_session_into_note`, `..._computes_duration_from_transcript_mtime`) — none of them should need any changes

- [ ] **Step 5: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "fix: make check_and_salvage_orphan safe for concurrent callers"
```

---

### Task 2: Add the `rumps` dependency

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- None — dependency addition only.

- [ ] **Step 1: Add `rumps` to `pyproject.toml`'s dependencies**

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
    "rich>=13",
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
    "rumps>=0.4",
]
```

- [ ] **Step 2: Reinstall the project in editable mode**

Run: `.venv/bin/pip install -e ".[dev]"`
Expected: `rumps` installs with no errors (it pulls in `pyobjc` transitively).

- [ ] **Step 3: Verify the import works**

Run: `.venv/bin/python -c "import rumps; app = rumps.App('Test'); item = rumps.MenuItem('X'); app.menu = [item]; print('ok')"`
Expected: prints `ok`, no error. (This does not enter the blocking event loop — `rumps.App.run()` is never called.)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add rumps dependency for the menu bar app"
```

---

### Task 3: `get_current_session_status` in `service.py`

**Files:**
- Modify: `notetaker/service.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Produces: `get_current_session_status(config_dir: Path) -> SessionInfo | None` — returns the active `SessionInfo` only if a session file exists, parses, and its recorder process is genuinely alive; returns `None` for every other case (no session, corrupt file, dead pid). Never raises. This is distinct from `read_active_session` (which raises for the "nothing active" case, appropriate for `stop`) and from `check_and_salvage_orphan` (which only returns non-`None` when it actually salvages something) — this one is for a poller that just wants to know "is something currently recording, for display purposes," with no side effects.

- [ ] **Step 1: Write the failing tests**

Add `get_current_session_status` to the existing grouped `from notetaker.service import (...)` block at the top of `tests/test_service.py`, alphabetically alongside the other names.

```python
# tests/test_service.py (append below the existing tests)
def test_get_current_session_status_returns_none_when_no_session_file(tmp_path):
    assert get_current_session_status(tmp_path) is None


def test_get_current_session_status_returns_none_when_session_file_corrupt(tmp_path):
    (tmp_path / "current_session.json").write_text("{not valid json")
    assert get_current_session_status(tmp_path) is None


def test_get_current_session_status_returns_none_when_pid_dead(tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {"pid": 999999, "title": "Standup", "start_time": "2026-09-11T10:00:00", "session_dir": str(tmp_path)}
        )
    )
    assert get_current_session_status(tmp_path) is None


def test_get_current_session_status_returns_info_when_pid_alive(tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {"pid": os.getpid(), "title": "Standup", "start_time": "2026-09-11T10:00:00", "session_dir": str(tmp_path)}
        )
    )
    info = get_current_session_status(tmp_path)
    assert info == SessionInfo(os.getpid(), "Standup", datetime(2026, 9, 11, 10, 0), tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -k get_current_session_status -v`
Expected: FAIL with `ImportError: cannot import name 'get_current_session_status'`

- [ ] **Step 3: Write minimal implementation**

Append to `notetaker/service.py`, near `check_and_salvage_orphan`:

```python
def get_current_session_status(config_dir: Path) -> SessionInfo | None:
    """Returns the currently active Session's info if one is genuinely
    running (a live Recorder process), or None otherwise (no session file, a
    corrupt one, or a dead pid). Never raises — for a poller (e.g. a menu bar
    app) that wants to display status without handling exceptions for the
    common "nothing is recording" case.
    """
    try:
        info = read_active_session(config_dir)
    except ServiceError:
        return None
    if not pid_alive(info.pid):
        return None
    return info
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: add get_current_session_status for non-raising status checks"
```

---

### Task 4: `notetaker/menubar.py` — pure helper functions

**Files:**
- Create: `notetaker/menubar.py`
- Create: `tests/test_menubar.py`

**Interfaces:**
- Produces: `format_elapsed(start_time: datetime, now: datetime) -> str` (e.g. `"05:23"`, or `"1:05:23"` past an hour); `menu_bar_title(info: SessionInfo | None, now: datetime) -> str` (the text shown in the actual macOS menu bar strip — `"Notetaker"` when idle, `"⏺ 05:23"` while recording); `toggle_item_title(info: SessionInfo | None) -> str` (`"Start Recording"` or `"Stop Recording"`); `auto_generated_title(now: datetime) -> str` (e.g. `"Meeting 2026-09-16 14:30"`); `note_summarization_failed(note_path: Path) -> bool` (checks a saved Note's content for the exact fallback text `_summarize_or_fallback` writes on failure).
- These are pure functions with no `rumps` dependency at all — fully testable with plain assertions.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_menubar.py
from datetime import datetime
from pathlib import Path

from notetaker.menubar import (
    auto_generated_title,
    format_elapsed,
    menu_bar_title,
    note_summarization_failed,
    toggle_item_title,
)
from notetaker.service import SessionInfo


def test_format_elapsed_under_an_hour():
    start = datetime(2026, 9, 16, 10, 0, 0)
    now = datetime(2026, 9, 16, 10, 5, 23)
    assert format_elapsed(start, now) == "05:23"


def test_format_elapsed_over_an_hour():
    start = datetime(2026, 9, 16, 10, 0, 0)
    now = datetime(2026, 9, 16, 11, 5, 23)
    assert format_elapsed(start, now) == "1:05:23"


def test_menu_bar_title_idle():
    assert menu_bar_title(None, datetime(2026, 9, 16, 10, 0, 0)) == "Notetaker"


def test_menu_bar_title_recording():
    info = SessionInfo(123, "Standup", datetime(2026, 9, 16, 10, 0, 0), Path("/tmp"))
    now = datetime(2026, 9, 16, 10, 5, 23)
    assert menu_bar_title(info, now) == "⏺ 05:23"


def test_toggle_item_title_idle():
    assert toggle_item_title(None) == "Start Recording"


def test_toggle_item_title_recording():
    info = SessionInfo(123, "Standup", datetime(2026, 9, 16, 10, 0, 0), Path("/tmp"))
    assert toggle_item_title(info) == "Stop Recording"


def test_auto_generated_title():
    now = datetime(2026, 9, 16, 14, 30, 0)
    assert auto_generated_title(now) == "Meeting 2026-09-16 14:30"


def test_note_summarization_failed_true(tmp_path):
    note_path = tmp_path / "note.md"
    note_path.write_text("## Summary\nSummarization failed: network down\n")
    assert note_summarization_failed(note_path) is True


def test_note_summarization_failed_false(tmp_path):
    note_path = tmp_path / "note.md"
    note_path.write_text("## Summary\nEverything went great.\n")
    assert note_summarization_failed(note_path) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_menubar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notetaker.menubar'`

- [ ] **Step 3: Write minimal implementation**

```python
# notetaker/menubar.py
from datetime import datetime
from pathlib import Path

from notetaker.service import SessionInfo

IDLE_TITLE = "Notetaker"


def format_elapsed(start_time: datetime, now: datetime) -> str:
    """Formats elapsed time as MM:SS, or H:MM:SS past an hour, for display."""
    total_seconds = int((now - start_time).total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def menu_bar_title(info: SessionInfo | None, now: datetime) -> str:
    """The text shown directly in the macOS menu bar strip."""
    if info is None:
        return IDLE_TITLE
    return f"⏺ {format_elapsed(info.start_time, now)}"


def toggle_item_title(info: SessionInfo | None) -> str:
    return "Stop Recording" if info is not None else "Start Recording"


def auto_generated_title(now: datetime) -> str:
    return f"Meeting {now:%Y-%m-%d %H:%M}"


def note_summarization_failed(note_path: Path) -> bool:
    """Whether a just-saved Note's Summary indicates summarization failed —
    matches the exact fallback text `_summarize_or_fallback` writes.
    """
    return "Summarization failed:" in note_path.read_text()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_menubar.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add notetaker/menubar.py tests/test_menubar.py
git commit -m "feat: add pure display/formatting helpers for the menu bar app"
```

---

### Task 5: `NotetakerMenuBarApp`

**Files:**
- Modify: `notetaker/menubar.py`
- Modify: `tests/test_menubar.py`

**Interfaces:**
- Produces: `NotetakerMenuBarApp(rumps.App)` — a menu bar app with one toggle menu item ("Start Recording"/"Stop Recording") and a 2-second `rumps.Timer` that updates the displayed title/item text and calls `check_and_salvage_orphan`, firing a notification if it salvages something. Starting/stopping calls `service.start_session`/`service.stop_session` directly, with `ServiceError`/unexpected-exception cases shown via `rumps.alert` instead of crashing the app. A save always fires a notification — a different one if the note's summary indicates summarization failed.
- **CRITICAL:** every test constructs a real `NotetakerMenuBarApp()` (confirmed safe — see Global Constraints) and mocks `notetaker.menubar.rumps.alert` / `notetaker.menubar.rumps.notification` / `notetaker.menubar.load_config` / `notetaker.menubar.service.*` as needed. No test calls the real `rumps.alert` or `rumps.notification` under any circumstances — a missed mock here means a hanging test suite, not just a slow or flaky one.

- [ ] **Step 1: Write the failing tests**

Add these imports to the top of `tests/test_menubar.py` (alongside the existing ones): `import pytest`, `from notetaker.config import ConfigError`, `from notetaker import service`, `from notetaker.menubar import NotetakerMenuBarApp`.

```python
# tests/test_menubar.py (append)
@pytest.fixture
def app():
    instance = NotetakerMenuBarApp()
    yield instance
    instance._timer.stop()


def _config(tmp_path):
    from notetaker.config import Config

    return Config(tmp_path / "notes", "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY")


def test_on_tick_shows_warning_when_config_missing(app, monkeypatch):
    def raise_config_error():
        raise ConfigError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.menubar.load_config", raise_config_error)

    app._on_tick(None)

    assert "⚠️" in app.title


def test_on_tick_updates_title_when_idle(app, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.menubar.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.menubar.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.menubar.service.check_and_salvage_orphan", lambda config, config_dir: None)
    monkeypatch.setattr("notetaker.menubar.service.get_current_session_status", lambda config_dir: None)

    app._on_tick(None)

    assert app.title == "Notetaker"
    assert app._toggle_item.title == "Start Recording"


def test_on_tick_notifies_when_orphan_salvaged(app, monkeypatch, tmp_path):
    salvaged_path = tmp_path / "notes" / "2026-09-15-standup.md"
    monkeypatch.setattr("notetaker.menubar.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.menubar.CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        "notetaker.menubar.service.check_and_salvage_orphan", lambda config, config_dir: salvaged_path
    )
    monkeypatch.setattr("notetaker.menubar.service.get_current_session_status", lambda config_dir: None)
    calls = []
    monkeypatch.setattr(
        "notetaker.menubar.rumps.notification",
        lambda title, subtitle, message: calls.append((title, subtitle, message)),
    )

    app._on_tick(None)

    assert len(calls) == 1
    assert calls[0][0] == "Recovered a crashed session"


def test_on_toggle_shows_alert_when_config_missing(app, monkeypatch):
    def raise_config_error():
        raise ConfigError("No config found. Run `notetaker init` first.")

    monkeypatch.setattr("notetaker.menubar.load_config", raise_config_error)
    calls = []
    monkeypatch.setattr(
        "notetaker.menubar.rumps.alert", lambda title, message: calls.append((title, message))
    )

    app._on_toggle(None)

    assert len(calls) == 1
    assert calls[0][0] == "Notetaker is not set up"


def test_on_toggle_starts_when_idle(app, monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.menubar.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.menubar.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.menubar.service.get_current_session_status", lambda config_dir: None)
    calls = []
    monkeypatch.setattr(
        "notetaker.menubar.service.start_session",
        lambda title, config, config_dir: calls.append(title),
    )

    app._on_toggle(None)

    assert len(calls) == 1
    assert calls[0].startswith("Meeting ")


def test_on_toggle_shows_alert_when_start_fails(app, monkeypatch, tmp_path):
    from notetaker.service import ServiceError

    monkeypatch.setattr("notetaker.menubar.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.menubar.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.menubar.service.get_current_session_status", lambda config_dir: None)

    def fail(title, config, config_dir):
        raise ServiceError("BlackHole is not active.")

    monkeypatch.setattr("notetaker.menubar.service.start_session", fail)
    calls = []
    monkeypatch.setattr(
        "notetaker.menubar.rumps.alert", lambda title, message: calls.append((title, message))
    )

    app._on_toggle(None)

    assert len(calls) == 1
    assert "BlackHole is not active." in calls[0][1]


def test_on_toggle_stops_and_notifies_when_recording(app, monkeypatch, tmp_path):
    from datetime import datetime

    info = service.SessionInfo(123, "Standup", datetime(2026, 9, 16, 10, 0, 0), tmp_path)
    note_path = tmp_path / "notes" / "2026-09-16-standup.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text("## Summary\nAll good.\n")

    monkeypatch.setattr("notetaker.menubar.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.menubar.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.menubar.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr(
        "notetaker.menubar.service.stop_session", lambda info, config, config_dir: note_path
    )
    calls = []
    monkeypatch.setattr(
        "notetaker.menubar.rumps.notification",
        lambda title, subtitle, message: calls.append((title, subtitle, message)),
    )

    app._on_toggle(None)

    assert len(calls) == 1
    assert calls[0] == ("Recording saved", "", "2026-09-16-standup.md")


def test_on_toggle_notifies_summarization_failure_variant(app, monkeypatch, tmp_path):
    from datetime import datetime

    info = service.SessionInfo(123, "Standup", datetime(2026, 9, 16, 10, 0, 0), tmp_path)
    note_path = tmp_path / "notes" / "2026-09-16-standup.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text("## Summary\nSummarization failed: network down\n")

    monkeypatch.setattr("notetaker.menubar.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.menubar.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.menubar.service.get_current_session_status", lambda config_dir: info)
    monkeypatch.setattr(
        "notetaker.menubar.service.stop_session", lambda info, config, config_dir: note_path
    )
    calls = []
    monkeypatch.setattr(
        "notetaker.menubar.rumps.notification",
        lambda title, subtitle, message: calls.append((title, subtitle, message)),
    )

    app._on_toggle(None)

    assert calls[0][1] == "Summarization failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_menubar.py -v`
Expected: FAIL — `AttributeError` or `ImportError`, since `NotetakerMenuBarApp` doesn't exist yet

- [ ] **Step 3: Write minimal implementation**

Append to `notetaker/menubar.py`:

```python
import rumps

from notetaker import service
from notetaker.config import CONFIG_DIR, ConfigError, load_config

POLL_INTERVAL_SECONDS = 2
SETUP_WARNING_TITLE = "⚠️ Notetaker"


class NotetakerMenuBarApp(rumps.App):
    def __init__(self):
        super().__init__(IDLE_TITLE, quit_button="Quit")
        self._toggle_item = rumps.MenuItem("Start Recording", callback=self._on_toggle)
        self.menu = [self._toggle_item]
        self._timer = rumps.Timer(self._on_tick, POLL_INTERVAL_SECONDS)
        self._timer.start()

    def _on_tick(self, _timer):
        try:
            config = load_config()
        except ConfigError:
            self.title = SETUP_WARNING_TITLE
            self._toggle_item.title = "Start Recording"
            return
        try:
            salvaged_path = service.check_and_salvage_orphan(config, CONFIG_DIR)
        except Exception:
            salvaged_path = None
        if salvaged_path is not None:
            self._notify("Recovered a crashed session", "Saved as a note", salvaged_path.name)
        info = service.get_current_session_status(CONFIG_DIR)
        self.title = menu_bar_title(info, datetime.now())
        self._toggle_item.title = toggle_item_title(info)

    def _on_toggle(self, _sender):
        try:
            config = load_config()
        except ConfigError as exc:
            rumps.alert(title="Notetaker is not set up", message=str(exc))
            return
        info = service.get_current_session_status(CONFIG_DIR)
        if info is None:
            self._start(config)
        else:
            self._stop(config, info)

    def _start(self, config):
        title = auto_generated_title(datetime.now())
        try:
            service.start_session(title, config, CONFIG_DIR)
        except service.ServiceError as exc:
            rumps.alert(title="Could not start recording", message=str(exc))

    def _stop(self, config, info):
        try:
            note_path = service.stop_session(info, config, CONFIG_DIR)
        except Exception as exc:
            rumps.alert(title="Could not save the recording", message=str(exc))
            return
        if note_summarization_failed(note_path):
            self._notify("Recording saved", "Summarization failed", note_path.name)
        else:
            self._notify("Recording saved", "", note_path.name)

    @staticmethod
    def _notify(title: str, subtitle: str, message: str) -> None:
        try:
            rumps.notification(title=title, subtitle=subtitle, message=message)
        except Exception:
            pass  # best-effort — notification delivery for an unbundled script isn't guaranteed on modern macOS
```

Note: this appends to the file after Task 4's helper functions — the `from datetime import datetime` and `from pathlib import Path` imports are already at the top from Task 4, so no duplicate import is needed for `datetime.now()` used here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_menubar.py -v`
Expected: all pass. Double-check explicitly: no test in this run takes more than a second or two — if any test hangs, stop immediately and check whether `rumps.alert` or `rumps.notification` is being called for real somewhere (a missing mock).

- [ ] **Step 5: Commit**

```bash
git add notetaker/menubar.py tests/test_menubar.py
git commit -m "feat: add NotetakerMenuBarApp"
```

---

### Task 6: CLI `menubar` command

**Files:**
- Modify: `notetaker/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `notetaker menubar` — a new top-level command that constructs and runs `NotetakerMenuBarApp`. The import is lazy (inside the function body), so no other CLI command pays the cost of importing `rumps`/PyObjC.
- **CRITICAL:** `NotetakerMenuBarApp.run()` blocks forever until the app quits. A test that invokes this command for real would hang the test suite. The test mocks `notetaker.menubar.NotetakerMenuBarApp` itself (not `notetaker.cli.NotetakerMenuBarApp` — since the import is lazy, patching the name where it actually lives, in the `notetaker.menubar` module, is what the lazy `from ... import ...` picks up at call time) with a fake class whose `run()` returns immediately.

- [ ] **Step 1: Add the command to `notetaker/cli.py`**

Append after the `resummarize` command:

```python
@app.command()
def menubar():
    """Launches the menu bar app (blocks until quit)."""
    from notetaker.menubar import NotetakerMenuBarApp

    NotetakerMenuBarApp().run()
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_menubar_command_launches_the_app(monkeypatch):
    calls = []

    class FakeApp:
        def __init__(self):
            calls.append("constructed")

        def run(self):
            calls.append("ran")

    monkeypatch.setattr("notetaker.menubar.NotetakerMenuBarApp", FakeApp)

    result = runner.invoke(app, ["menubar"])

    assert result.exit_code == 0
    assert calls == ["constructed", "ran"]
```

- [ ] **Step 3: Run the full fast suite**

Run: `.venv/bin/pytest -m "not integration" -q`
Expected: all pass, and the new test completes quickly (confirming it never entered the real blocking event loop)

- [ ] **Step 4: Commit**

```bash
git add notetaker/cli.py tests/test_cli.py
git commit -m "feat: add menubar CLI command"
```

---

### Task 7: Update `CLAUDE.md`'s architecture summary

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- None — documentation only.

- [ ] **Step 1: Update the module list**

In `CLAUDE.md`'s "Planned architecture" module list, replace:

```markdown
- `cli.py` — command dispatch (Typer) for `init`, `start`, `stop`, `list`, `show`, `resummarize`, `set-api-key`, `show-api-key`. `start`/`stop` show a `rich` loading spinner, since both can block for an unpredictable duration (spawning the recorder; the summarization API call).
```

with:

```markdown
- `cli.py` — command dispatch (Typer) for `init`, `start`, `stop`, `list`, `show`, `resummarize`, `set-api-key`, `show-api-key`, `menubar`. `start`/`stop` show a `rich` loading spinner, since both can block for an unpredictable duration (spawning the recorder; the summarization API call).
- `menubar.py` — a minimal `rumps`-based menu bar app: one-click Start/Stop, live elapsed-time display, and native notifications on save-complete, summarization-failure, and crash-detected. Polls `service.check_and_salvage_orphan`/`get_current_session_status` every 2 seconds — the first UI surface besides the CLI to call these on its own cadence, which is why `check_and_salvage_orphan` now claims the session file atomically before salvaging.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document the menu bar app in the architecture summary"
```

---

## Self-Review Notes

- **Spec coverage**: This plan implements the spec's "Menu bar app" section in full: one-click Start/Stop, auto-generated title, live elapsed-time + in-progress indicator, and notifications on save-complete/summarization-failure/crash-detected. It deliberately excludes Homebrew formula/`brew services` packaging (a separate later plan) and the dashboard (which will reuse `get_current_session_status`/`check_and_salvage_orphan` on its own polling cadence once built).
- **A real, previously-deferred gap closed, not just carried forward again**: Plan 2's final review flagged `check_and_salvage_orphan`'s lack of locking as "not reachable today... a future poller... will need an atomic claim added here first." This plan's Task 1 is that fix, done now because this plan is that future poller.
- **New test-isolation risk identified and handled with the same rigor as prior real-system risks** (the Keychain lesson from the credentials plan, the `~/.notetaker` lesson from the service-layer plan): `rumps.alert` blocks on a modal dialog. Every task touching code that could reach it mocks it explicitly.
- **Type consistency checked**: `get_current_session_status(config_dir) -> SessionInfo | None` and the `menubar.py` helper signatures are used identically across every task.
- **No placeholders**: every step shows the exact code to write.
