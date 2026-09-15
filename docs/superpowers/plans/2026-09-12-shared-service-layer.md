# Shared Service Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract all operational logic currently inline in `notetaker/cli.py` (start/stop/list/show/init) into a new `notetaker/service.py` module with no `typer`/echo dependencies, so the CLI, the future menu bar app, and the future web dashboard can all call the same implementation instead of three copies drifting apart.

**Architecture:** `notetaker/service.py` becomes the single place business logic lives — it raises `ServiceError` for user-facing failures and returns plain dataclasses, never prints anything. `notetaker/cli.py` becomes a thin adapter: parse args, call a service function, translate the result or `ServiceError` into `typer.echo`/exit codes. No behavior changes are in scope — this is a pure refactor, verified by preserving (and relocating) test coverage.

**Tech Stack:** Python 3.10/3.11, Typer, pytest — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-12-notetaker-ui-design.md` (Architecture section: "Shared service layer")

## Global Constraints

- Python `>=3.10,<3.12` (per `pyproject.toml`) — don't use syntax newer than 3.10 supports.
- macOS only — no cross-platform shims.
- Run tests with `.venv/bin/pytest -m "not integration" -q` (fast suite); there is no ambient install, don't use a bare `pytest`.
- No new dependencies in this plan — it is a pure refactor.
- `CLAUDE.md`'s architecture summary must stay in sync with the code — the last task updates it.

---

## File Structure

- Create: `notetaker/service.py` — all business logic, dataclasses (`SessionInfo`, `SetupStatus`), and `ServiceError`.
- Create: `tests/test_service.py` — unit tests for every service function, replacing the logic-level tests currently in `tests/test_cli.py`.
- Modify: `notetaker/cli.py` — every command becomes a thin wrapper around a `notetaker.service` call.
- Modify: `tests/test_cli.py` — rewritten to test only CLI wiring (argument parsing, output text, exit codes) by mocking `notetaker.service` functions, not the internals those functions used to call directly.
- Modify: `CLAUDE.md` — architecture summary gets a line for `service.py`.

---

### Task 1: `ServiceError`, `SessionInfo`, and the two PID/path helpers

**Files:**
- Create: `notetaker/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Produces: `ServiceError(Exception)`, `SessionInfo` dataclass (`pid: int`, `title: str`, `start_time: datetime`, `session_dir: Path`), `session_file_path(config_dir: Path) -> Path`, `pid_alive(pid: int) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_service.py
import os

from notetaker.service import pid_alive, session_file_path


def test_session_file_path_is_under_config_dir(tmp_path):
    assert session_file_path(tmp_path) == tmp_path / "current_session.json"


def test_pid_alive_true_for_current_process():
    assert pid_alive(os.getpid()) is True


def test_pid_alive_false_for_nonexistent_pid():
    assert pid_alive(999999) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notetaker.service'`

- [ ] **Step 3: Write minimal implementation**

```python
# notetaker/service.py
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SESSION_FILE_NAME = "current_session.json"


class ServiceError(Exception):
    """A user-facing failure in a service operation; str(exc) is the display message."""


@dataclass
class SessionInfo:
    pid: int
    title: str
    start_time: datetime
    session_dir: Path


def session_file_path(config_dir: Path) -> Path:
    return config_dir / SESSION_FILE_NAME


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: add service module skeleton with ServiceError and PID helpers"
```

---

### Task 2: `start_session`

**Files:**
- Modify: `notetaker/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `Config` (`notetaker.config.Config`), `BlackHoleStatus`/`check_blackhole`/`find_blackhole_device_index` (`notetaker.recorder`), `SessionInfo`, `ServiceError`, `session_file_path`, `pid_alive` from Task 1.
- Produces: `start_session(title: str, config: Config, config_dir: Path) -> SessionInfo` — raises `ServiceError` if a session is already running, BlackHole isn't active, or the recorder process exits immediately.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_service.py (append)
import json
import sys
from unittest.mock import MagicMock

import pytest

from notetaker.config import Config
from notetaker.recorder import BlackHoleStatus
from notetaker.service import ServiceError, start_session


def _config(tmp_path):
    return Config(tmp_path / "notes", "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY")


def test_start_session_fails_when_blackhole_not_active(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.NOT_INSTALLED)
    with pytest.raises(ServiceError, match="BlackHole is not active"):
        start_session("Standup", _config(tmp_path), tmp_path)


def test_start_session_fails_when_already_running(monkeypatch, tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps({"pid": os.getpid(), "title": "x", "start_time": "2026-09-11T10:00:00", "session_dir": str(tmp_path)})
    )
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    with pytest.raises(ServiceError, match="already running"):
        start_session("Standup", _config(tmp_path), tmp_path)


def test_start_session_ignores_corrupt_session_file_and_starts_new_one(monkeypatch, tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text("{invalid json")
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.find_blackhole_device_index", lambda: 2)
    fake_proc = MagicMock(pid=54321)
    fake_proc.poll.return_value = None
    monkeypatch.setattr("notetaker.service.subprocess.Popen", lambda *a, **k: fake_proc)

    info = start_session("Weekly", _config(tmp_path), tmp_path)

    assert info.pid == 54321
    assert info.title == "Weekly"
    assert json.loads(session_file.read_text())["pid"] == 54321


def test_start_session_writes_session_file_and_spawns_recorder(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.find_blackhole_device_index", lambda: 2)
    fake_proc = MagicMock(pid=12345)
    fake_proc.poll.return_value = None
    captured_cmd = {}

    def fake_popen(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        return fake_proc

    monkeypatch.setattr("notetaker.service.subprocess.Popen", fake_popen)

    info = start_session("Standup", _config(tmp_path), tmp_path)

    assert info.pid == 12345
    assert info.title == "Standup"
    assert captured_cmd["cmd"] == [
        sys.executable, "-m", "notetaker.recorder", str(info.session_dir), "2", "tiny",
    ]
    session = json.loads((tmp_path / "current_session.json").read_text())
    assert session == {
        "pid": 12345,
        "title": "Standup",
        "start_time": info.start_time.isoformat(),
        "session_dir": str(info.session_dir),
    }


def test_start_session_fails_when_recorder_exits_immediately(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.find_blackhole_device_index", lambda: 2)
    fake_proc = MagicMock(pid=99999)
    fake_proc.poll.return_value = 0
    monkeypatch.setattr("notetaker.service.subprocess.Popen", lambda *a, **k: fake_proc)
    monkeypatch.setattr("notetaker.service.time.sleep", lambda s: None)

    with pytest.raises(ServiceError, match="recorder failed to start"):
        start_session("Standup", _config(tmp_path), tmp_path)

    assert not (tmp_path / "current_session.json").exists()
```

Add `import os` to the top of `tests/test_service.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'start_session'`

- [ ] **Step 3: Write minimal implementation**

```python
# notetaker/service.py (append imports at top, function at bottom)
import json
import subprocess
import sys
import time

from notetaker.config import Config
from notetaker.recorder import BlackHoleStatus, check_blackhole, find_blackhole_device_index


def start_session(title: str, config: Config, config_dir: Path) -> SessionInfo:
    session_file = session_file_path(config_dir)
    if session_file.exists():
        try:
            raw = json.loads(session_file.read_text())
            if pid_alive(raw["pid"]):
                raise ServiceError("a session is already running. Run `notetaker stop` first.")
        except (json.JSONDecodeError, KeyError, OSError):
            pass

    status = check_blackhole()
    if status != BlackHoleStatus.ACTIVE:
        raise ServiceError("BlackHole is not active. Run `notetaker init` for setup instructions.")
    device_index = find_blackhole_device_index()

    start_time = datetime.now()
    session_dir = config_dir / "sessions" / start_time.strftime("%Y%m%d-%H%M%S")
    session_dir.mkdir(parents=True, exist_ok=True)

    log_path = session_dir / "recorder.log"
    log_file = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "notetaker.recorder", str(session_dir), str(device_index), config.whisper_model],
            start_new_session=True,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    finally:
        log_file.close()

    time.sleep(0.5)
    if proc.poll() is not None:
        raise ServiceError(f"recorder failed to start — see {log_path} for details")

    session_file.write_text(
        json.dumps(
            {
                "pid": proc.pid,
                "title": title,
                "start_time": start_time.isoformat(),
                "session_dir": str(session_dir),
            }
        )
    )
    return SessionInfo(pid=proc.pid, title=title, start_time=start_time, session_dir=session_dir)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: add start_session to the service layer"
```

---

### Task 3: `read_active_session` and `stop_session`

**Files:**
- Modify: `notetaker/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `Summary`/`get_provider`/`summarize_transcript` (`notetaker.summarizer`), `write_note` (`notetaker.notes`), `SessionInfo`, `ServiceError`, `session_file_path`, `pid_alive`.
- Produces: `read_active_session(config_dir: Path) -> SessionInfo` — raises `ServiceError` ("no active session." / "session file ... is corrupt or unreadable ..."); `stop_session(info: SessionInfo, config: Config, config_dir: Path) -> Path` — returns the written note's path. Splitting these two lets a later crash-salvage feature reuse `read_active_session`-style parsing without duplicating it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_service.py (append)
from datetime import datetime

from notetaker.notes import write_note
from notetaker.service import SessionInfo, read_active_session, stop_session
from notetaker.summarizer import Summary


def test_read_active_session_fails_when_no_session_file(tmp_path):
    with pytest.raises(ServiceError, match="no active session"):
        read_active_session(tmp_path)


def test_read_active_session_fails_on_corrupt_file(tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text("{not valid json")
    with pytest.raises(ServiceError, match="corrupt or unreadable"):
        read_active_session(tmp_path)


def test_read_active_session_parses_valid_file(tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {"pid": 999999, "title": "Standup", "start_time": "2026-09-11T10:00:00", "session_dir": str(tmp_path)}
        )
    )
    info = read_active_session(tmp_path)
    assert info == SessionInfo(999999, "Standup", datetime(2026, 9, 11, 10, 0), tmp_path)


def _session_info(session_dir):
    return SessionInfo(pid=999999, title="Standup", start_time=datetime(2026, 9, 11, 10, 0), session_dir=session_dir)


def test_stop_session_skips_provider_call_when_transcript_empty(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    session_file = tmp_path / "current_session.json"
    session_file.write_text("{}")  # presence is all stop_session checks for cleanup
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)

    def fail_if_called(*a, **k):
        raise AssertionError("get_provider should not be called for an empty transcript")

    monkeypatch.setattr("notetaker.service.get_provider", fail_if_called)
    monkeypatch.setattr("notetaker.service.summarize_transcript", fail_if_called)

    note_path = stop_session(_session_info(session_dir), Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path)

    assert "No audio was captured" in note_path.read_text()
    assert not session_dir.exists()
    assert not session_file.exists()


def test_stop_session_salvages_transcript_and_writes_note(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n[00:00:07] world\n")
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="summary text", action_items=["a"], tags=["t"]),
    )

    note_path = stop_session(_session_info(session_dir), Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path)

    assert "summary text" in note_path.read_text()
    assert not session_dir.exists()


def test_stop_session_saves_note_with_error_when_summarization_fails(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n")
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())

    def raise_error(transcript, provider):
        raise RuntimeError("network down")

    monkeypatch.setattr("notetaker.service.summarize_transcript", raise_error)

    note_path = stop_session(_session_info(session_dir), Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path)

    text = note_path.read_text()
    assert "Summarization failed" in text
    assert "hello" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'read_active_session'`

- [ ] **Step 3: Write minimal implementation**

```python
# notetaker/service.py (append imports at top, functions at bottom)
import shutil
import signal

from notetaker.notes import write_note
from notetaker.summarizer import Summary, get_provider, summarize_transcript


def read_active_session(config_dir: Path) -> SessionInfo:
    session_file = session_file_path(config_dir)
    if not session_file.exists():
        raise ServiceError("no active session.")
    try:
        raw = json.loads(session_file.read_text())
        return SessionInfo(
            pid=raw["pid"],
            title=raw["title"],
            start_time=datetime.fromisoformat(raw["start_time"]),
            session_dir=Path(raw["session_dir"]),
        )
    except (json.JSONDecodeError, KeyError, OSError) as exc:
        raise ServiceError(
            f"session file at {session_file} is corrupt or unreadable. "
            f"Check ~/.notetaker/sessions/ manually for a salvageable transcript, "
            f"then remove {session_file} to reset."
        ) from exc


def stop_session(info: SessionInfo, config: Config, config_dir: Path) -> Path:
    session_file = session_file_path(config_dir)

    if pid_alive(info.pid):
        os.kill(info.pid, signal.SIGTERM)
        for _ in range(30):
            if not pid_alive(info.pid):
                break
            time.sleep(1)

    transcript_path = info.session_dir / "transcript.txt"
    transcript = transcript_path.read_text() if transcript_path.exists() else ""
    transcript_lines = transcript.splitlines()

    duration_minutes = int((datetime.now() - info.start_time).total_seconds() // 60)

    if not transcript.strip():
        summary = Summary(text="No audio was captured for this session.", action_items=[], tags=[])
    else:
        try:
            provider = get_provider(config)
            summary = summarize_transcript(transcript, provider)
        except Exception as exc:
            summary = Summary(text=f"Summarization failed: {exc}", action_items=[], tags=[])

    note_path = write_note(
        config.notes_dir, info.title, info.start_time, duration_minutes, summary, transcript_lines
    )

    shutil.rmtree(info.session_dir, ignore_errors=True)
    session_file.unlink()

    return note_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: add read_active_session and stop_session to the service layer"
```

---

### Task 4: `list_all_notes` and `get_note_body`

**Files:**
- Modify: `notetaker/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `NoteMeta`/`list_notes`/`find_note_path`/`read_note_body` (`notetaker.notes`).
- Produces: `list_all_notes(config: Config) -> list[NoteMeta]`; `get_note_body(config: Config, note_id: str) -> str` — raises `ServiceError` if the note doesn't exist.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_service.py (append)
from notetaker.service import get_note_body, list_all_notes


def test_list_all_notes_returns_notes_sorted_by_date(tmp_path):
    notes_dir = tmp_path / "notes"
    write_note(notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], ["proj"]), [])
    metas = list_all_notes(Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"))
    assert len(metas) == 1
    assert metas[0].title == "Standup"


def test_get_note_body_returns_body_text(tmp_path):
    notes_dir = tmp_path / "notes"
    write_note(notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("Summary text", [], []), ["[00:00:01] hi"])
    body = get_note_body(Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), "2026-09-11-standup")
    assert "Summary text" in body


def test_get_note_body_raises_for_missing_note(tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    with pytest.raises(ServiceError, match="no note found"):
        get_note_body(Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), "nonexistent")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'list_all_notes'`

- [ ] **Step 3: Write minimal implementation**

```python
# notetaker/service.py (append imports at top, functions at bottom)
from notetaker.notes import NoteMeta, find_note_path, list_notes, read_note_body


def list_all_notes(config: Config) -> list[NoteMeta]:
    return list_notes(config.notes_dir)


def get_note_body(config: Config, note_id: str) -> str:
    path = find_note_path(config.notes_dir, note_id)
    if path is None:
        raise ServiceError(f"no note found with id '{note_id}'.")
    return read_note_body(path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: add list_all_notes and get_note_body to the service layer"
```

---

### Task 5: `initialize_config`, `check_setup`, `ensure_whisper_model`

**Files:**
- Modify: `notetaker/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `write_default_config`/`CONFIG_PATH` (`notetaker.config`), `check_apple_local_preflight` (`notetaker.summarizer`), `Transcriber` (`notetaker.transcriber`).
- Produces: `SetupStatus` dataclass (`blackhole: BlackHoleStatus`, `provider_ready: bool`, `provider_problems: list[str]`); `initialize_config(config_path: Path) -> bool` (True if written, False if it already existed); `check_setup(config: Config) -> SetupStatus` — raises `ServiceError` for an unknown `ai_provider` (mirrors `get_provider`'s existing check); `ensure_whisper_model(config: Config) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_service.py (append)
from notetaker.service import SetupStatus, check_setup, ensure_whisper_model, initialize_config


def test_initialize_config_writes_when_missing(tmp_path):
    config_path = tmp_path / "config.yaml"
    assert initialize_config(config_path) is True
    assert config_path.exists()


def test_initialize_config_skips_when_present(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("existing: true\n")
    assert initialize_config(config_path) is False
    assert config_path.read_text() == "existing: true\n"


def test_check_setup_reports_blackhole_and_ready_claude_provider(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    status = check_setup(_config(tmp_path))
    assert status == SetupStatus(blackhole=BlackHoleStatus.ACTIVE, provider_ready=True, provider_problems=[])


def test_check_setup_reports_missing_claude_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status = check_setup(_config(tmp_path))
    assert status.provider_ready is False
    assert "ANTHROPIC_API_KEY is not set" in status.provider_problems[0]


def test_check_setup_reports_apple_local_problems(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.check_apple_local_preflight", lambda: ["apfel is not installed"])
    status = check_setup(Config(tmp_path, "tiny", "apple_local", "apple-foundationmodel", "UNUSED"))
    assert status.provider_ready is False
    assert status.provider_problems == ["apfel is not installed"]


def test_ensure_whisper_model_loads_transcriber(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("notetaker.service.Transcriber", lambda model: calls.append(model))
    ensure_whisper_model(_config(tmp_path))
    assert calls == ["tiny"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'SetupStatus'`

- [ ] **Step 3: Write minimal implementation**

```python
# notetaker/service.py (append imports at top, functions/dataclass at bottom)
from notetaker.config import write_default_config
from notetaker.summarizer import check_apple_local_preflight
from notetaker.transcriber import Transcriber


@dataclass
class SetupStatus:
    blackhole: BlackHoleStatus
    provider_ready: bool
    provider_problems: list[str]


def initialize_config(config_path: Path) -> bool:
    return write_default_config(config_path)


def check_setup(config: Config) -> SetupStatus:
    blackhole = check_blackhole()
    if config.ai_provider == "claude":
        if not os.environ.get(config.api_key_env):
            return SetupStatus(
                blackhole=blackhole,
                provider_ready=False,
                provider_problems=[
                    f"{config.api_key_env} is not set. Export it in your shell profile, "
                    "then re-run `notetaker init`."
                ],
            )
        return SetupStatus(blackhole=blackhole, provider_ready=True, provider_problems=[])
    if config.ai_provider == "apple_local":
        problems = check_apple_local_preflight()
        return SetupStatus(blackhole=blackhole, provider_ready=not problems, provider_problems=problems)
    raise ServiceError(f"Unknown ai_provider '{config.ai_provider}'.")


def ensure_whisper_model(config: Config) -> None:
    Transcriber(config.whisper_model)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: 23 passed

- [ ] **Step 5: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: add initialize_config, check_setup, ensure_whisper_model to the service layer"
```

---

### Task 6: Rewire `cli.py` onto the service layer

**Files:**
- Modify: `notetaker/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: every function/dataclass produced in Tasks 1-5 (`notetaker.service.*`).
- Produces: `notetaker/cli.py` with identical user-visible behavior (same output text, same exit codes) for `init`, `start`, `stop`, `list`, `show`.

- [ ] **Step 1: Replace `notetaker/cli.py` with the thin-wrapper version**

```python
# notetaker/cli.py
import typer

from notetaker import service
from notetaker.config import CONFIG_DIR, CONFIG_PATH, load_config
from notetaker.recorder import BlackHoleStatus
from notetaker.service import ServiceError

app = typer.Typer()


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

    config = load_config()
    status = service.check_setup(config)

    if status.blackhole == BlackHoleStatus.NOT_INSTALLED:
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

    typer.echo(f"Loading Whisper model '{config.whisper_model}' (downloads on first run)...")
    service.ensure_whisper_model(config)
    typer.echo("Whisper model ready.")


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


@app.command(name="list")
def list_command():
    config = load_config()
    for meta in service.list_all_notes(config):
        tags = ", ".join(meta.tags)
        typer.echo(f"{meta.note_id}  {meta.title}  [{tags}]")


@app.command()
def show(note_id: str):
    config = load_config()
    try:
        body = service.get_note_body(config, note_id)
    except ServiceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(body)
```

- [ ] **Step 2: Replace `tests/test_cli.py` with wiring-only tests**

```python
# tests/test_cli.py
from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from notetaker.cli import app
from notetaker.config import Config
from notetaker.recorder import BlackHoleStatus
from notetaker.service import ServiceError, SessionInfo, SetupStatus

runner = CliRunner()


def _config(tmp_path):
    return Config(tmp_path / "notes", "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY")


def test_init_writes_config_and_reports_blackhole_active(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.service.initialize_config", lambda path: True)
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        "notetaker.cli.service.check_setup",
        lambda config: SetupStatus(BlackHoleStatus.ACTIVE, True, []),
    )
    monkeypatch.setattr("notetaker.cli.service.ensure_whisper_model", lambda config: None)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "BlackHole is installed and active" in result.output
    assert "ANTHROPIC_API_KEY is set." in result.output


def test_init_fails_when_provider_not_ready(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.service.initialize_config", lambda path: True)
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        "notetaker.cli.service.check_setup",
        lambda config: SetupStatus(BlackHoleStatus.ACTIVE, False, ["ANTHROPIC_API_KEY is not set."]),
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY is not set." in result.output


def test_start_reports_service_error(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))

    def fail(*a, **k):
        raise ServiceError("BlackHole is not active. Run `notetaker init` for setup instructions.")

    monkeypatch.setattr("notetaker.cli.service.start_session", fail)

    result = runner.invoke(app, ["start", "Standup"])

    assert result.exit_code == 1
    assert "BlackHole is not active" in result.output


def test_start_prints_reminders_and_confirmation_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        "notetaker.cli.service.start_session",
        lambda title, config, config_dir: SessionInfo(12345, title, datetime.now(), tmp_path),
    )

    result = runner.invoke(app, ["start", "Standup"])

    assert result.exit_code == 0
    assert "microphone access" in result.output
    assert "Teams will not show" in result.output
    assert "Recording started: Standup" in result.output


def test_stop_reports_service_error_for_no_active_session(monkeypatch):
    def fail(config_dir):
        raise ServiceError("no active session.")

    monkeypatch.setattr("notetaker.cli.service.read_active_session", fail)

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 1
    assert "no active session" in result.output


def test_stop_prints_note_path_on_success(monkeypatch, tmp_path):
    info = SessionInfo(999999, "Standup", datetime.now(), tmp_path)
    monkeypatch.setattr("notetaker.cli.service.read_active_session", lambda config_dir: info)
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    note_path = tmp_path / "notes" / "2026-09-11-standup.md"
    monkeypatch.setattr("notetaker.cli.service.stop_session", lambda info, config, config_dir: note_path)

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
    assert str(note_path) in result.output


def test_list_prints_notes(monkeypatch, tmp_path):
    from notetaker.notes import NoteMeta

    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    meta = NoteMeta("2026-09-11-standup", "Standup", datetime(2026, 9, 11, 10, 0), 5, ["proj"], Path("x.md"))
    monkeypatch.setattr("notetaker.cli.service.list_all_notes", lambda config: [meta])

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "Standup" in result.output
    assert "proj" in result.output


def test_show_prints_note_body(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.cli.service.get_note_body", lambda config, note_id: "Summary text")

    result = runner.invoke(app, ["show", "2026-09-11-standup"])

    assert result.exit_code == 0
    assert "Summary text" in result.output


def test_show_missing_note_fails(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))

    def fail(config, note_id):
        raise ServiceError(f"no note found with id '{note_id}'.")

    monkeypatch.setattr("notetaker.cli.service.get_note_body", fail)

    result = runner.invoke(app, ["show", "nonexistent"])

    assert result.exit_code == 1
```

- [ ] **Step 3: Run the full fast suite**

Run: `.venv/bin/pytest -m "not integration" -q`
Expected: all tests pass (23 in `test_service.py` + 9 in `test_cli.py`, plus the unchanged `test_config.py`, `test_notes.py`, `test_recorder.py`, `test_summarizer.py`, `test_transcriber.py` files)

- [ ] **Step 4: Manually smoke-test the CLI end to end**

Run: `.venv/bin/notetaker list` (with a real or already-initialized `~/.notetaker/config.yaml`)
Expected: same output as before the refactor — this catches anything the mocked unit tests can't (e.g. a real `load_config()` call failing).

- [ ] **Step 5: Commit**

```bash
git add notetaker/cli.py tests/test_cli.py
git commit -m "refactor: rewire cli.py onto the service layer"
```

---

### Task 7: Update `CLAUDE.md`'s architecture summary

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- None — documentation only.

- [ ] **Step 1: Add `service.py` to the "Planned architecture" module list**

In `CLAUDE.md`, find the bullet list starting with `- \`cli.py\` — command dispatch...` and add a new bullet after it:

```markdown
- `service.py` — all operational logic (start/stop/list/show/init), used by `cli.py` and (from here on) every other UI surface. Raises `ServiceError` for user-facing failures; never prints anything itself — `cli.py` is a thin adapter that translates its results into `typer.echo` calls and exit codes.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document the service.py module in the architecture summary"
```

---

## Self-Review Notes

- **Spec coverage**: This plan implements only the "Shared service layer" architecture bullet from the spec — it deliberately does not touch Cancel, Delete, Resummarize, Salvage, Provider credential, the menu bar app, the dashboard, or `brew services` packaging. Those are separate, later plans that will build on `notetaker/service.py` as the seam this plan creates.
- **Type consistency checked**: `SessionInfo`, `SetupStatus`, and `ServiceError` are defined once in Task 1/5 and referenced with identical field names/order in every later task and in `test_cli.py`.
- **No placeholders**: every step shows the exact code to write; no task defers detail to "similar to Task N."
