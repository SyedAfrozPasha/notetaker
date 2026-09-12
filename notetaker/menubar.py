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
