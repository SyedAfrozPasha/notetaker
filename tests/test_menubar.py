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
