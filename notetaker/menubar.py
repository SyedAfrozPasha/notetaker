import webbrowser
from datetime import datetime
from pathlib import Path

import rumps

from notetaker import service
from notetaker.config import CONFIG_DIR, ConfigError, load_config
from notetaker.service import SessionInfo

IDLE_TITLE = "Notetaker"
POLL_INTERVAL_SECONDS = 2
SETUP_WARNING_TITLE = "⚠️ Notetaker"


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


class NotetakerMenuBarApp(rumps.App):
    def __init__(self, dashboard_url: str | None = None):
        """`dashboard_url` is set by `notetaker dashboard`, which serves the
        web dashboard from a background thread of this same process and adds
        an "Open Dashboard" item; `notetaker menubar` runs without it."""
        super().__init__(IDLE_TITLE, quit_button="Quit")
        self._dashboard_url = dashboard_url
        self._toggle_item = rumps.MenuItem("Start Recording", callback=self._on_toggle)
        items = [self._toggle_item]
        if dashboard_url:
            items += [rumps.separator, rumps.MenuItem("Open Dashboard", callback=self._on_open_dashboard)]
        self.menu = items
        self._timer = rumps.Timer(self._on_tick, POLL_INTERVAL_SECONDS)
        self._timer.start()

    def _on_open_dashboard(self, _sender):
        webbrowser.open(self._dashboard_url)

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
            # A Recorder that crashed since the last 2s tick would otherwise be
            # overwritten by start_session, losing its transcript for good.
            salvaged_path = service.check_and_salvage_orphan(config, CONFIG_DIR)
            if salvaged_path is not None:
                self._notify("Recovered a crashed session", "Saved as a note", salvaged_path.name)
            info = service.start_session(title, config, CONFIG_DIR)
        except Exception as exc:
            rumps.alert(title="Could not start recording", message=str(exc))
            return
        for warning in list(getattr(info, "warnings", None) or []):
            self._notify("Recording started with a warning", "Check audio routing", warning)

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
