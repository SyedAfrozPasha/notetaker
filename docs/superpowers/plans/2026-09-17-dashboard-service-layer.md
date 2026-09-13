# Dashboard Service-Layer Prerequisites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `notetaker/service.py` (and the modules it wraps) with every backend capability the upcoming web dashboard needs — tags at session creation, note deletion, structured note editing, note search/filter, and safe non-secret config editing — with zero new UI code, so the dashboard plan that follows this one is a thin FastAPI/Jinja2/htmx layer over an already-complete, already-tested service surface.

**Architecture:** This is a pure backend plan, same shape as the original shared-service-layer plan that kicked off this whole UI initiative. No new files beyond tests; every change lands in `notetaker/service.py`, `notetaker/notes.py`, and `notetaker/config.py`, following the existing pattern where `service.py` is the single seam every UI surface calls through and raises `ServiceError` for user-facing failures. Nothing in this plan changes CLI or menu bar *behavior* — `cli.py` and `menubar.py` are untouched.

**Tech Stack:** Python 3.10/3.11, pytest, PyYAML (already used by `notes.py`/`config.py`) — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-12-notetaker-ui-design.md` (see "Feature scope by surface > Dashboard"). Domain vocabulary: `CONTEXT.md`.

## Global Constraints

- `service.py` never prints; it raises `ServiceError` (a plain `Exception` subclass, message is `str(exc)`) for every user-facing failure. Never let a raw `ConfigError`, `KeyError`, `OSError`, etc. escape a new `service.py` function.
- CLI and menu bar behavior must not change. Do not touch `notetaker/cli.py` or `notetaker/menubar.py` in this plan. `start_session`'s new `tags` parameter must default such that existing callers (CLI, menu bar, all existing tests) are unaffected.
- `SessionInfo` is a `@dataclass` used positionally throughout the existing test suite (e.g. `SessionInfo(999999, "Standup", datetime.now(), tmp_path)`) — any new field must be added last, with a default, so every existing 4-positional-argument call site keeps working unchanged.
- Editing a note's title changes only the displayed `title` frontmatter field — the Note ID (and therefore the `.md` filename and its `.transcript.txt` sidecar filename) is fixed at creation and is never renamed by an edit. This matches `CONTEXT.md`'s definition of Note ID as "not a separately stored/generated ID" — it's the filename stem, chosen once.
- Per the spec, dashboard config editing covers exactly `notes_dir`, `whisper_model`, `ai_provider` — never `ai_model` (not mentioned in the spec's scope) and never `api_key_env` (the spec explicitly excludes the API-key-via-env-var path from the UI; that's the separate Keychain-backed credential flow, already implemented).
- Run `.venv/bin/pytest -q` after every task; it must stay green (currently 150 passed) with only the new tests added by that task on top.

---

## Task 1: Session tags — creation-time tags, persisted and merged into the final Note

Lets the dashboard's "create a session" feature accept optional tags up front (per spec: "Create a session (same as `start`, optional title/tags up front)") and have them survive into the finished Note, merged with whatever tags the AI Provider produces.

**Files:**
- Modify: `notetaker/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Produces: `SessionInfo` gains `tags: list[str]` (new last field, `field(default_factory=list)`). `start_session(title, config, config_dir, tags=None)` — new optional 4th parameter. `read_active_session` now populates `SessionInfo.tags` from the session file (defaulting to `[]` if the key is absent, for backward compatibility with a session file written before this change). `_finalize_stop` merges `info.tags` into the final `Summary.tags` before `write_note` is called (session tags first, then Provider tags, de-duplicated, order-preserving).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_service.py` (the file already imports `MagicMock`, `json`, `BlackHoleStatus`, `Config`, `SessionInfo`, `Summary`, `start_session`, `read_active_session`, `stop_session`, `parse_note_meta`, and has a `_config(tmp_path)` helper — reuse all of these, do not re-import):

```python
def test_start_session_persists_tags(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.find_blackhole_device_index", lambda: 2)
    fake_proc = MagicMock(pid=555)
    fake_proc.poll.return_value = None
    monkeypatch.setattr("notetaker.service.subprocess.Popen", lambda *a, **k: fake_proc)

    info = start_session("Standup", _config(tmp_path), tmp_path, tags=["project-x", "planning"])

    assert info.tags == ["project-x", "planning"]
    raw = json.loads((tmp_path / "current_session.json").read_text())
    assert raw["tags"] == ["project-x", "planning"]


def test_start_session_defaults_to_no_tags(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.find_blackhole_device_index", lambda: 2)
    fake_proc = MagicMock(pid=556)
    fake_proc.poll.return_value = None
    monkeypatch.setattr("notetaker.service.subprocess.Popen", lambda *a, **k: fake_proc)

    info = start_session("Standup", _config(tmp_path), tmp_path)

    assert info.tags == []


def test_read_active_session_parses_tags(tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {
                "pid": 999999,
                "title": "Standup",
                "start_time": "2026-09-11T10:00:00",
                "session_dir": str(tmp_path),
                "tags": ["proj"],
            }
        )
    )
    info = read_active_session(tmp_path)
    assert info.tags == ["proj"]


def test_read_active_session_defaults_tags_when_absent(tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {"pid": 999999, "title": "Standup", "start_time": "2026-09-11T10:00:00", "session_dir": str(tmp_path)}
        )
    )
    info = read_active_session(tmp_path)
    assert info.tags == []


def test_stop_session_merges_session_tags_before_provider_tags(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="s", action_items=[], tags=["ai-tag"]),
    )
    info = SessionInfo(
        pid=999999, title="Standup", start_time=datetime(2026, 9, 11, 10, 0),
        session_dir=session_dir, tags=["user-tag"],
    )

    note_path = stop_session(
        info, Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path
    )

    assert parse_note_meta(note_path).tags == ["user-tag", "ai-tag"]


def test_stop_session_dedupes_tags_the_provider_also_produced(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="s", action_items=[], tags=["shared-tag", "ai-only"]),
    )
    info = SessionInfo(
        pid=999999, title="Standup", start_time=datetime(2026, 9, 11, 10, 0),
        session_dir=session_dir, tags=["shared-tag"],
    )

    note_path = stop_session(
        info, Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path
    )

    assert parse_note_meta(note_path).tags == ["shared-tag", "ai-only"]


def test_stop_session_leaves_provider_tags_alone_when_no_session_tags(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (tmp_path / "current_session.json").write_text("{}")
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.service.pid_alive", lambda pid: False)
    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="s", action_items=[], tags=["ai-tag-a", "ai-tag-b"]),
    )
    info = SessionInfo(999999, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)  # no tags — defaults to []

    note_path = stop_session(
        info, Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), tmp_path
    )

    assert parse_note_meta(note_path).tags == ["ai-tag-a", "ai-tag-b"]
```

Also update the existing `test_start_session_writes_session_file_and_spawns_recorder` test (already in `tests/test_service.py`), which currently asserts the exact dict written to `current_session.json`. Its assertion:

```python
    session = json.loads((tmp_path / "current_session.json").read_text())
    assert session == {
        "pid": 12345,
        "title": "Standup",
        "start_time": info.start_time.isoformat(),
        "session_dir": str(info.session_dir),
    }
```

must become:

```python
    session = json.loads((tmp_path / "current_session.json").read_text())
    assert session == {
        "pid": 12345,
        "title": "Standup",
        "start_time": info.start_time.isoformat(),
        "session_dir": str(info.session_dir),
        "tags": [],
    }
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -k "tags" -v`
Expected: FAIL — `SessionInfo() got an unexpected keyword argument 'tags'` / `start_session() got an unexpected keyword argument 'tags'` (the six new tests), plus the modified `test_start_session_writes_session_file_and_spawns_recorder` failing on a dict-equality mismatch (missing `"tags"` key on the actual side).

- [ ] **Step 3: Implement**

In `notetaker/service.py`:

1. Change the dataclass import line (currently `from dataclasses import dataclass`) to also import `field` and `replace`:

```python
from dataclasses import dataclass, field, replace
```

2. Add `tags` as the new last field of `SessionInfo`:

```python
@dataclass
class SessionInfo:
    pid: int
    title: str
    start_time: datetime
    session_dir: Path
    tags: list[str] = field(default_factory=list)
```

3. Change `start_session`'s signature and session-file write to accept and persist `tags`:

```python
def start_session(title: str, config: Config, config_dir: Path, tags: list[str] | None = None) -> SessionInfo:
```

and where it currently does:

```python
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

change to:

```python
    session_file.write_text(
        json.dumps(
            {
                "pid": proc.pid,
                "title": title,
                "start_time": start_time.isoformat(),
                "session_dir": str(session_dir),
                "tags": tags or [],
            }
        )
    )
    return SessionInfo(pid=proc.pid, title=title, start_time=start_time, session_dir=session_dir, tags=tags or [])
```

4. In `read_active_session`, where it currently does:

```python
        return SessionInfo(
            pid=raw["pid"],
            title=raw["title"],
            start_time=datetime.fromisoformat(raw["start_time"]),
            session_dir=Path(raw["session_dir"]),
        )
```

change to:

```python
        return SessionInfo(
            pid=raw["pid"],
            title=raw["title"],
            start_time=datetime.fromisoformat(raw["start_time"]),
            session_dir=Path(raw["session_dir"]),
            tags=raw.get("tags") or [],
        )
```

5. In `_finalize_stop`, right after the line `summary = _summarize_or_fallback(transcript, config)`, add:

```python
    if info.tags:
        merged_tags = list(dict.fromkeys(info.tags + summary.tags))
        summary = replace(summary, tags=merged_tags)
```

- [ ] **Step 4: Run the full suite to verify everything passes**

Run: `.venv/bin/pytest -q`
Expected: PASS, 156 passed (150 + 6 new tests; the modified existing test doesn't add to the count).

- [ ] **Step 5: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: persist session tags and merge them into the final note"
```

---

## Task 2: Delete a note, and read a live session's rolling transcript preview

Two small, independent, same-shape additions — thin `service.py` functions over existing file-path primitives already used elsewhere in the module (`stop_session`'s sidecar path expression, `info.session_dir / "transcript.txt"`). Grouped into one task because neither is big enough to be its own review unit.

**Files:**
- Modify: `notetaker/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `find_note_path` (already imported), `SessionInfo` (Task 1).
- Produces: `delete_note(config: Config, note_id: str) -> None` — raises `ServiceError` if the note doesn't exist; otherwise removes the note file and its `.transcript.txt` sidecar (if present). `get_live_transcript_preview(info: SessionInfo) -> str` — returns the in-progress session's transcript text so far, or `""` if nothing's been transcribed yet.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_service.py`:

```python
def test_delete_note_removes_file_and_sidecar(tmp_path):
    from notetaker.service import delete_note

    notes_dir = tmp_path / "notes"
    note_path = write_note(notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], []), ["hi"])
    sidecar_path = notes_dir / f"{note_path.stem}.transcript.txt"
    sidecar_path.write_text("hi")

    delete_note(_config(tmp_path), "2026-09-11-standup")

    assert not note_path.exists()
    assert not sidecar_path.exists()


def test_delete_note_removes_file_when_sidecar_absent(tmp_path):
    from notetaker.service import delete_note

    notes_dir = tmp_path / "notes"
    note_path = write_note(notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], []), ["hi"])

    delete_note(_config(tmp_path), "2026-09-11-standup")

    assert not note_path.exists()


def test_delete_note_raises_for_missing_note(tmp_path):
    from notetaker.service import delete_note

    with pytest.raises(ServiceError, match="no note found"):
        delete_note(_config(tmp_path), "nonexistent")


def test_get_live_transcript_preview_returns_transcript_text(tmp_path):
    from notetaker.service import get_live_transcript_preview

    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n")
    info = SessionInfo(999999, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    assert get_live_transcript_preview(info) == "[00:00:03] hello\n"


def test_get_live_transcript_preview_returns_empty_string_before_first_chunk(tmp_path):
    from notetaker.service import get_live_transcript_preview

    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    info = SessionInfo(999999, "Standup", datetime(2026, 9, 11, 10, 0), session_dir)

    assert get_live_transcript_preview(info) == ""
```

Note: `write_note`, `Summary`, `pytest`, `ServiceError`, `_config` are already imported/defined in this file — reuse them, don't re-import.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -k "delete_note or live_transcript_preview" -v`
Expected: FAIL — `ImportError: cannot import name 'delete_note'` / `'get_live_transcript_preview'`.

- [ ] **Step 3: Implement**

In `notetaker/service.py`, add both functions near `get_note_body` (they belong in the same "note access" grouping):

```python
def delete_note(config: Config, note_id: str) -> None:
    note_path = find_note_path(config.notes_dir, note_id)
    if note_path is None:
        raise ServiceError(f"no note found with id '{note_id}'.")
    sidecar_path = note_path.parent / f"{note_path.stem}.transcript.txt"
    sidecar_path.unlink(missing_ok=True)
    note_path.unlink()


def get_live_transcript_preview(info: SessionInfo) -> str:
    transcript_path = info.session_dir / "transcript.txt"
    return transcript_path.read_text() if transcript_path.exists() else ""
```

- [ ] **Step 4: Run the full suite to verify everything passes**

Run: `.venv/bin/pytest -q`
Expected: PASS, 161 passed (156 + 5 new tests).

- [ ] **Step 5: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: add delete_note and get_live_transcript_preview"
```

---

## Task 3: Edit a Note's structured fields (title, tags, summary, action items)

Lets the dashboard's note-edit page save changes to title/tags/summary-text/action-items without ever touching the Transcript section, and without renaming the note (see Global Constraints).

**Files:**
- Modify: `notetaker/notes.py`
- Modify: `notetaker/service.py`
- Test: `tests/test_notes.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `parse_note_meta`, `read_note_body` (existing, unchanged).
- Produces: `notetaker.notes.parse_action_items(action_items_md: str) -> list[str]` — parses a rendered action-items Markdown block back into a plain list (empty list for the `"- (none)"` sentinel). `notetaker.notes.update_note_fields(path: Path, *, title: str | None = None, tags: list[str] | None = None, summary_text: str | None = None, action_items: list[str] | None = None) -> None` — rewrites only the given fields, in place, preserving date/duration_minutes/Transcript exactly. `notetaker.notes.parse_note_body(path: Path) -> tuple[str, list[str], str]` — returns `(summary_text, action_items, transcript_text)`, the structured counterpart to `read_note_body`'s single blob. `notetaker.service.update_note(config: Config, note_id: str, *, title=None, tags=None, summary_text=None, action_items=None) -> Path` — looks up the note by ID, raises `ServiceError` if missing, otherwise delegates to `update_note_fields` and returns the note's path. `notetaker.service.NoteDetail` (dataclass: `note_id, title, date, duration_minutes, tags, summary_text, action_items, transcript, path`) and `notetaker.service.get_note_detail(config: Config, note_id: str) -> NoteDetail` — the structured "view a note" accessor the dashboard's note-detail/edit page needs (Summary, Action Items as a list, Transcript, all separated), raising `ServiceError` if the note doesn't exist.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_notes.py`:

```python
def test_parse_action_items_splits_checkbox_lines():
    from notetaker.notes import parse_action_items

    assert parse_action_items("- [ ] Follow up with Bob\n- [ ] Send notes") == [
        "Follow up with Bob",
        "Send notes",
    ]


def test_parse_action_items_none_sentinel_is_empty_list():
    from notetaker.notes import parse_action_items

    assert parse_action_items("- (none)") == []


def test_update_note_fields_changes_only_given_fields(tmp_path):
    from notetaker.notes import update_note_fields

    path = write_note(
        tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 18,
        Summary(text="old summary", action_items=["old item"], tags=["old-tag"]),
        ["[00:00:03] hello"],
    )

    update_note_fields(path, title="Renamed Standup", tags=["new-tag"])

    meta = parse_note_meta(path)
    assert meta.title == "Renamed Standup"
    assert meta.tags == ["new-tag"]
    body = read_note_body(path)
    assert "old summary" in body  # summary_text untouched
    assert "old item" in body  # action_items untouched
    assert "[00:00:03] hello" in body  # transcript untouched
    assert meta.duration_minutes == 18  # unrelated frontmatter untouched


def test_update_note_fields_changes_summary_and_action_items(tmp_path):
    from notetaker.notes import update_note_fields

    path = write_note(
        tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 18,
        Summary(text="old summary", action_items=["old item"], tags=["old-tag"]),
        ["[00:00:03] hello"],
    )

    update_note_fields(path, summary_text="new summary", action_items=["new item one", "new item two"])

    body = read_note_body(path)
    assert "new summary" in body
    assert "old summary" not in body
    assert "new item one" in body
    assert "new item two" in body
    assert "old item" not in body
    meta = parse_note_meta(path)
    assert meta.title == "Standup"  # untouched
    assert meta.tags == ["old-tag"]  # untouched


def test_update_note_fields_does_not_rename_the_note(tmp_path):
    from notetaker.notes import update_note_fields

    path = write_note(tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], []), ["hi"])
    original_path = path

    update_note_fields(path, title="A Totally Different Title")

    assert path == original_path
    assert len(list(tmp_path.glob("*.md"))) == 1


def test_update_note_fields_clearing_action_items_writes_none_sentinel(tmp_path):
    from notetaker.notes import update_note_fields

    path = write_note(
        tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 5,
        Summary("s", ["item to remove"], []), ["hi"],
    )

    update_note_fields(path, action_items=[])

    body = read_note_body(path)
    assert "item to remove" not in body
    assert "(none)" in body


def test_parse_note_body_splits_all_three_sections(tmp_path):
    from notetaker.notes import parse_note_body

    path = write_note(
        tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 5,
        Summary(text="We discussed X.", action_items=["Follow up"], tags=[]),
        ["[00:00:03] hello"],
    )

    summary_text, action_items, transcript = parse_note_body(path)

    assert summary_text == "We discussed X."
    assert action_items == ["Follow up"]
    assert transcript == "[00:00:03] hello"


def test_parse_note_body_empty_transcript_is_empty_string(tmp_path):
    from notetaker.notes import parse_note_body

    path = write_note(tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], []), [])

    _, _, transcript = parse_note_body(path)

    assert transcript == ""
```

`write_note`, `read_note_body`, `parse_note_meta`, `Summary`, and `datetime` are already imported at the top of `tests/test_notes.py` — reuse them.

Add to `tests/test_service.py`:

```python
def test_update_note_edits_fields_and_returns_note_path(tmp_path):
    from notetaker.service import update_note

    notes_dir = tmp_path / "notes"
    note_path = write_note(
        notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("old", [], ["old-tag"]), ["hi"]
    )

    result_path = update_note(_config(tmp_path), "2026-09-11-standup", title="New Title", tags=["new-tag"])

    assert result_path == note_path
    meta = parse_note_meta(note_path)
    assert meta.title == "New Title"
    assert meta.tags == ["new-tag"]


def test_update_note_raises_for_missing_note(tmp_path):
    from notetaker.service import update_note

    with pytest.raises(ServiceError, match="no note found"):
        update_note(_config(tmp_path), "nonexistent", title="X")


def test_get_note_detail_returns_structured_fields(tmp_path):
    from notetaker.service import get_note_detail

    write_note(
        tmp_path / "notes", "Standup", datetime(2026, 9, 11, 10, 0), 18,
        Summary(text="We discussed X.", action_items=["Follow up with Bob"], tags=["project-x"]),
        ["[00:00:03] hello"],
    )

    detail = get_note_detail(_config(tmp_path), "2026-09-11-standup")

    assert detail.note_id == "2026-09-11-standup"
    assert detail.title == "Standup"
    assert detail.duration_minutes == 18
    assert detail.tags == ["project-x"]
    assert detail.summary_text == "We discussed X."
    assert detail.action_items == ["Follow up with Bob"]
    assert detail.transcript == "[00:00:03] hello"


def test_get_note_detail_empty_transcript_is_empty_string(tmp_path):
    from notetaker.service import get_note_detail

    write_note(tmp_path / "notes", "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], []), [])

    detail = get_note_detail(_config(tmp_path), "2026-09-11-standup")

    assert detail.transcript == ""


def test_get_note_detail_raises_for_missing_note(tmp_path):
    from notetaker.service import get_note_detail

    with pytest.raises(ServiceError, match="no note found"):
        get_note_detail(_config(tmp_path), "nonexistent")
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/pytest tests/test_notes.py tests/test_service.py -k "action_items or update_note or note_body or note_detail" -v`
Expected: FAIL — `ImportError: cannot import name 'parse_action_items'` / `'update_note_fields'` / `'parse_note_body'` / `'update_note'` / `'get_note_detail'`.

- [ ] **Step 3: Implement**

In `notetaker/notes.py`, first refactor `_render_note_body` to share its literal rendering format with the new field-update path, so the two never drift. Replace the existing `_render_note_body` function:

```python
def _render_note_body(
    title: str,
    start_time: datetime,
    duration_minutes: int,
    summary: Summary,
    transcript_lines: list[str],
) -> str:
    frontmatter = {
        "title": title,
        "date": start_time.isoformat(),
        "duration_minutes": duration_minutes,
        "tags": summary.tags,
    }
    action_items_md = "\n".join(f"- [ ] {item}" for item in summary.action_items) or "- (none)"
    transcript_md = "\n".join(transcript_lines) or "(no transcript captured)"
    return (
        f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n\n"
        f"## Summary\n{summary.text}\n\n"
        f"## Action Items\n{action_items_md}\n\n"
        f"## Transcript\n{transcript_md}\n"
    )
```

with:

```python
def _render(frontmatter: dict, summary_text: str, action_items_md: str, transcript_md: str) -> str:
    return (
        f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n\n"
        f"## Summary\n{summary_text}\n\n"
        f"## Action Items\n{action_items_md}\n\n"
        f"## Transcript\n{transcript_md}\n"
    )


def _render_note_body(
    title: str,
    start_time: datetime,
    duration_minutes: int,
    summary: Summary,
    transcript_lines: list[str],
) -> str:
    frontmatter = {
        "title": title,
        "date": start_time.isoformat(),
        "duration_minutes": duration_minutes,
        "tags": summary.tags,
    }
    action_items_md = "\n".join(f"- [ ] {item}" for item in summary.action_items) or "- (none)"
    transcript_md = "\n".join(transcript_lines) or "(no transcript captured)"
    return _render(frontmatter, summary.text, action_items_md, transcript_md)
```

Then add, after `rewrite_note_summary`:

```python
def parse_action_items(action_items_md: str) -> list[str]:
    """Parses a rendered Action Items Markdown block back into a plain list
    — the inverse of the `"- [ ] {item}"` rendering in `_render`. The
    `"- (none)"` sentinel (written when there are no action items) parses
    back to an empty list.
    """
    items = []
    for line in action_items_md.strip().splitlines():
        line = line.strip()
        if line.startswith("- [ ] "):
            items.append(line[len("- [ ] "):])
    return items


def _split_body(body: str) -> tuple[str, str, str]:
    """Splits a Note's rendered body into (summary_text, action_items_md,
    transcript_md) exactly as `_render` wrote them, so a field-level edit
    can leave the sections it doesn't touch byte-for-byte unchanged.
    """
    summary_part, _, rest = body.partition("## Action Items")
    action_part, _, transcript_part = rest.partition("## Transcript")
    summary_text = summary_part.replace("## Summary", "", 1).strip()
    return summary_text, action_part.strip(), transcript_part.strip()


def update_note_fields(
    path: Path,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
    summary_text: str | None = None,
    action_items: list[str] | None = None,
) -> None:
    """Replaces only the given fields of an existing Note, in place, never
    renaming it (see CONTEXT.md's Note ID definition) and never touching
    its Transcript section.
    """
    meta = parse_note_meta(path)
    text = path.read_text()
    _, _, body = text.split("---", 2)
    current_summary_text, current_action_items_md, transcript_md = _split_body(body)

    new_title = title if title is not None else meta.title
    new_tags = tags if tags is not None else meta.tags
    new_summary_text = summary_text if summary_text is not None else current_summary_text
    if action_items is not None:
        new_action_items_md = "\n".join(f"- [ ] {item}" for item in action_items) or "- (none)"
    else:
        new_action_items_md = current_action_items_md

    frontmatter = {
        "title": new_title,
        "date": meta.date.isoformat(),
        "duration_minutes": meta.duration_minutes,
        "tags": new_tags,
    }
    path.write_text(_render(frontmatter, new_summary_text, new_action_items_md, transcript_md))


def parse_note_body(path: Path) -> tuple[str, list[str], str]:
    """Returns (summary_text, action_items, transcript_text) parsed from a
    Note's Markdown body — the structured counterpart to `read_note_body`,
    for callers (e.g. the dashboard's note-detail/edit view) that need the
    three sections separately rather than as one blob.
    """
    body = read_note_body(path)
    summary_text, action_items_md, transcript_md = _split_body(body)
    action_items = parse_action_items(action_items_md)
    transcript_text = "" if transcript_md == "(no transcript captured)" else transcript_md
    return summary_text, action_items, transcript_text
```

In `notetaker/service.py`, add the imports of `parse_note_body` and `update_note_fields` to the existing `from notetaker.notes import (...)` block (keep it alphabetized with the existing names: `find_note_path`, `list_notes`, `parse_note_body`, `parse_note_meta`, `read_note_body`, `rewrite_note_summary`, `update_note_fields`, `write_note`), then add, near `resummarize_note`:

```python
def update_note(
    config: Config,
    note_id: str,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
    summary_text: str | None = None,
    action_items: list[str] | None = None,
) -> Path:
    note_path = find_note_path(config.notes_dir, note_id)
    if note_path is None:
        raise ServiceError(f"no note found with id '{note_id}'.")
    update_note_fields(note_path, title=title, tags=tags, summary_text=summary_text, action_items=action_items)
    return note_path


@dataclass
class NoteDetail:
    note_id: str
    title: str
    date: datetime
    duration_minutes: int
    tags: list[str]
    summary_text: str
    action_items: list[str]
    transcript: str
    path: Path


def get_note_detail(config: Config, note_id: str) -> NoteDetail:
    note_path = find_note_path(config.notes_dir, note_id)
    if note_path is None:
        raise ServiceError(f"no note found with id '{note_id}'.")
    meta = parse_note_meta(note_path)
    summary_text, action_items, transcript = parse_note_body(note_path)
    return NoteDetail(
        note_id=meta.note_id,
        title=meta.title,
        date=meta.date,
        duration_minutes=meta.duration_minutes,
        tags=meta.tags,
        summary_text=summary_text,
        action_items=action_items,
        transcript=transcript,
        path=meta.path,
    )
```

- [ ] **Step 4: Run the full suite to verify everything passes**

Run: `.venv/bin/pytest -q`
Expected: PASS, 174 passed (161 + 13 new tests: 8 in `test_notes.py`, 5 in `test_service.py`).

- [ ] **Step 5: Commit**

```bash
git add notetaker/notes.py notetaker/service.py tests/test_notes.py tests/test_service.py
git commit -m "feat: add structured note read/edit (get_note_detail/update_note)"
```

---

## Task 4: Search and filter Notes (text + tag + date range)

Backs the dashboard's "Browse/search notes" feature.

**Files:**
- Modify: `notetaker/notes.py`
- Modify: `notetaker/service.py`
- Test: `tests/test_notes.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `list_notes`, `read_note_body`, `parse_note_meta` (all existing).
- Produces: `notetaker.notes.search_notes(notes_dir: Path, *, query: str | None = None, tag: str | None = None, start_date: date | None = None, end_date: date | None = None) -> list[NoteMeta]` — same sort order as `list_notes` (newest first), filtered. `query` matches case-insensitively against title OR the note's summary text (not the transcript). `tag` matches exact membership in `NoteMeta.tags`. `start_date`/`end_date` are inclusive bounds on `meta.date.date()`. All filters are optional and combine with AND. `notetaker.service.search_notes(config: Config, *, query=None, tag=None, start_date=None, end_date=None) -> list[NoteMeta]` — thin passthrough to `notes.search_notes(config.notes_dir, ...)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_notes.py` (add `from datetime import date` to the existing `from datetime import datetime` import line, making it `from datetime import date, datetime`):

```python
def test_search_notes_filters_by_query_in_title(tmp_path):
    from notetaker.notes import search_notes

    write_note(tmp_path, "Team Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], []), [])
    write_note(tmp_path, "1:1 with Bob", datetime(2026, 9, 12, 10, 0), 5, Summary("s", [], []), [])

    results = search_notes(tmp_path, query="standup")

    assert [n.title for n in results] == ["Team Standup"]


def test_search_notes_filters_by_query_in_summary(tmp_path):
    from notetaker.notes import search_notes

    write_note(tmp_path, "Meeting A", datetime(2026, 9, 11, 10, 0), 5, Summary("Discussed the roadmap", [], []), [])
    write_note(tmp_path, "Meeting B", datetime(2026, 9, 12, 10, 0), 5, Summary("Discussed hiring", [], []), [])

    results = search_notes(tmp_path, query="roadmap")

    assert [n.title for n in results] == ["Meeting A"]


def test_search_notes_filters_by_tag(tmp_path):
    from notetaker.notes import search_notes

    write_note(tmp_path, "Meeting A", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], ["project-x"]), [])
    write_note(tmp_path, "Meeting B", datetime(2026, 9, 12, 10, 0), 5, Summary("s", [], ["project-y"]), [])

    results = search_notes(tmp_path, tag="project-x")

    assert [n.title for n in results] == ["Meeting A"]


def test_search_notes_filters_by_date_range(tmp_path):
    from notetaker.notes import search_notes

    write_note(tmp_path, "Too Early", datetime(2026, 9, 1, 10, 0), 5, Summary("s", [], []), [])
    write_note(tmp_path, "In Range", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], []), [])
    write_note(tmp_path, "Too Late", datetime(2026, 9, 30, 10, 0), 5, Summary("s", [], []), [])

    results = search_notes(tmp_path, start_date=date(2026, 9, 10), end_date=date(2026, 9, 15))

    assert [n.title for n in results] == ["In Range"]


def test_search_notes_combines_filters_with_and(tmp_path):
    from notetaker.notes import search_notes

    write_note(tmp_path, "Standup A", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], ["project-x"]), [])
    write_note(tmp_path, "Standup B", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], ["project-y"]), [])

    results = search_notes(tmp_path, query="standup", tag="project-x")

    assert [n.title for n in results] == ["Standup A"]


def test_search_notes_no_filters_returns_everything_newest_first(tmp_path):
    from notetaker.notes import search_notes

    write_note(tmp_path, "Old", datetime(2026, 9, 1, 9, 0), 5, Summary("s", [], []), [])
    write_note(tmp_path, "New", datetime(2026, 9, 11, 9, 0), 5, Summary("s", [], []), [])

    results = search_notes(tmp_path)

    assert [n.title for n in results] == ["New", "Old"]
```

Add to `tests/test_service.py`:

```python
def test_search_notes_delegates_to_notes_module(tmp_path):
    from notetaker.service import search_notes

    write_note(tmp_path / "notes", "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], ["proj"]), [])

    results = search_notes(_config(tmp_path), tag="proj")

    assert [n.title for n in results] == ["Standup"]
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/pytest tests/test_notes.py tests/test_service.py -k "search_notes" -v`
Expected: FAIL — `ImportError: cannot import name 'search_notes'`.

- [ ] **Step 3: Implement**

In `notetaker/notes.py`, change the top import line from `from datetime import datetime` to `from datetime import date, datetime`, then add, after `list_notes`:

```python
def search_notes(
    notes_dir: Path,
    *,
    query: str | None = None,
    tag: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[NoteMeta]:
    metas = list_notes(notes_dir)
    if tag is not None:
        metas = [m for m in metas if tag in m.tags]
    if start_date is not None:
        metas = [m for m in metas if m.date.date() >= start_date]
    if end_date is not None:
        metas = [m for m in metas if m.date.date() <= end_date]
    if query is not None:
        needle = query.lower()
        matched = []
        for m in metas:
            if needle in m.title.lower():
                matched.append(m)
                continue
            summary_text, _, _ = _split_body(read_note_body(m.path))
            if needle in summary_text.lower():
                matched.append(m)
        metas = matched
    return metas
```

(`_split_body` was added in Task 3 — this task must run after Task 3. `read_note_body` returns the body after the `---` frontmatter delimiter, exactly what `_split_body` expects.)

In `notetaker/service.py`, `service.py` needs its own top-level `search_notes` name for this wrapper, so import the `notes.py` function under an alias to avoid shadowing it: add `search_notes as notes_search_notes` to the existing `from notetaker.notes import (...)` block (alphabetized by `search_notes`, alongside the rest), then add, near `list_all_notes`:

```python
def search_notes(
    config: Config,
    *,
    query: str | None = None,
    tag: str | None = None,
    start_date=None,
    end_date=None,
) -> list[NoteMeta]:
    return notes_search_notes(config.notes_dir, query=query, tag=tag, start_date=start_date, end_date=end_date)
```

- [ ] **Step 4: Run the full suite to verify everything passes**

Run: `.venv/bin/pytest -q`
Expected: PASS, 181 passed (174 + 7 new tests: 6 in `test_notes.py`, 1 in `test_service.py`).

- [ ] **Step 5: Commit**

```bash
git add notetaker/notes.py notetaker/service.py tests/test_notes.py tests/test_service.py
git commit -m "feat: add search_notes (text/tag/date-range filtering)"
```

---

## Task 5: Safe, editable non-secret config (`get_config` / `update_config`)

Backs the dashboard's config-editing page, and — as a side effect worth calling out explicitly — closes a long-standing deferred item from the original service-layer plan: a raw `ConfigError` has never been caught anywhere in `cli.py`, meaning any of five-plus CLI commands can traceback on a missing/invalid config today. That gap is only closed here for the *new* service-layer entry points this task adds (`get_config`/`update_config`) — `cli.py` itself is not touched by this plan (see Global Constraints) — but it means the dashboard, unlike the CLI, starts out with zero raw-traceback exposure on config errors.

**Files:**
- Modify: `notetaker/config.py`
- Modify: `notetaker/service.py`
- Test: `tests/test_config.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Produces: `notetaker.config.update_config(updates: dict, path: Path = CONFIG_PATH) -> Config` — merges `updates` (a dict whose keys must be a subset of `{"notes_dir", "whisper_model", "ai_provider"}`) into the existing config file on disk, validates the result the same way `load_config` does, writes it back, and returns the reloaded `Config`. Raises `ConfigError` for an unsupported key, a missing config file, or an invalid resulting value (e.g. unknown provider) — never touches `ai_model` or `api_key_env`. `notetaker.service.get_config(config_path: Path = CONFIG_PATH) -> Config` and `notetaker.service.update_config(config_path: Path, updates: dict) -> Config` — both wrap the `config.py` calls, translating any `ConfigError` into a `ServiceError`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
def test_update_config_changes_only_given_fields(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/notetaker-notes\n"
        "whisper_model: base.en\n"
        "ai_provider: claude\n"
        "ai_model: claude-sonnet-5\n"
        "api_key_env: ANTHROPIC_API_KEY\n"
    )

    config = update_config({"notes_dir": "~/custom-notes", "whisper_model": "small"}, path)

    assert str(config.notes_dir).endswith("custom-notes")
    assert config.whisper_model == "small"
    assert config.ai_provider == "claude"  # untouched
    assert config.ai_model == "claude-sonnet-5"  # untouched, not a supported update key


def test_update_config_persists_to_disk(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/notetaker-notes\nwhisper_model: base.en\nai_provider: claude\n"
        "ai_model: claude-sonnet-5\napi_key_env: ANTHROPIC_API_KEY\n"
    )

    update_config({"ai_provider": "apple_local"}, path)

    reloaded = load_config(path)
    assert reloaded.ai_provider == "apple_local"


def test_update_config_rejects_unsupported_field(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    write_default_config(path)

    with pytest.raises(ConfigError, match="api_key_env"):
        update_config({"api_key_env": "SOMETHING_ELSE"}, path)


def test_update_config_rejects_invalid_provider(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    write_default_config(path)

    with pytest.raises(ConfigError, match="Unknown ai_provider"):
        update_config({"ai_provider": "bogus"}, path)


def test_update_config_raises_when_file_missing(tmp_path):
    from notetaker.config import update_config

    with pytest.raises(ConfigError):
        update_config({"whisper_model": "small"}, tmp_path / "missing.yaml")
```

(`load_config`, `write_default_config`, `ConfigError`, `pytest` are already imported at the top of `tests/test_config.py` — reuse them.)

Add to `tests/test_service.py`:

```python
def test_get_config_wraps_config_error_as_service_error(tmp_path):
    from notetaker.service import get_config

    with pytest.raises(ServiceError, match="No config found"):
        get_config(tmp_path / "missing.yaml")


def test_get_config_returns_config_on_success(tmp_path):
    from notetaker.config import write_default_config
    from notetaker.service import get_config

    path = tmp_path / "config.yaml"
    write_default_config(path)

    config = get_config(path)

    assert config.whisper_model == "base.en"


def test_service_update_config_wraps_config_error_as_service_error(tmp_path):
    from notetaker.config import write_default_config
    from notetaker.service import update_config

    path = tmp_path / "config.yaml"
    write_default_config(path)

    with pytest.raises(ServiceError, match="Unknown ai_provider"):
        update_config(path, {"ai_provider": "bogus"})


def test_service_update_config_returns_updated_config(tmp_path):
    from notetaker.config import write_default_config
    from notetaker.service import update_config

    path = tmp_path / "config.yaml"
    write_default_config(path)

    config = update_config(path, {"whisper_model": "small"})

    assert config.whisper_model == "small"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py tests/test_service.py -k "update_config or get_config" -v`
Expected: FAIL — `ImportError: cannot import name 'update_config'` / `'get_config'`.

- [ ] **Step 3: Implement**

In `notetaker/config.py`, add after `load_config`:

```python
UPDATABLE_KEYS = {"notes_dir", "whisper_model", "ai_provider"}


def update_config(updates: dict, path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise ConfigError(f"No config found at {path}. Run `notetaker init` first.")
    unknown = set(updates) - UPDATABLE_KEYS
    if unknown:
        raise ConfigError(f"Cannot update unsupported config field(s): {', '.join(sorted(unknown))}.")
    raw = yaml.safe_load(path.read_text()) or {}
    raw.update(updates)
    if raw.get("ai_provider") not in VALID_PROVIDERS:
        raise ConfigError(f"Unknown ai_provider '{raw.get('ai_provider')}' — expected one of {VALID_PROVIDERS}.")
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return load_config(path)
```

In `notetaker/service.py`, change the import line `from notetaker.config import Config, write_default_config` to:

```python
from notetaker.config import (
    CONFIG_PATH,
    Config,
    ConfigError,
    load_config,
    update_config as config_update_config,
    write_default_config,
)
```

Then add, near `initialize_config`:

```python
def get_config(config_path: Path = CONFIG_PATH) -> Config:
    try:
        return load_config(config_path)
    except ConfigError as exc:
        raise ServiceError(str(exc)) from exc


def update_config(config_path: Path, updates: dict) -> Config:
    try:
        return config_update_config(updates, config_path)
    except ConfigError as exc:
        raise ServiceError(str(exc)) from exc
```

- [ ] **Step 4: Run the full suite to verify everything passes**

Run: `.venv/bin/pytest -q`
Expected: PASS, 190 passed (181 + 9 new tests: 5 in `test_config.py`, 4 in `test_service.py`).

- [ ] **Step 5: Commit**

```bash
git add notetaker/config.py notetaker/service.py tests/test_config.py tests/test_service.py
git commit -m "feat: add get_config/update_config for safe non-secret config editing"
```

---

## Final check (do this after Task 5, before considering the plan done)

Run the full suite one more time and confirm the final count:

```bash
.venv/bin/pytest -q
```

Expected: `190 passed` (plus 1 deselected if run without `-m "not integration"` filtering is not applied — the integration test is unaffected by this plan either way).
