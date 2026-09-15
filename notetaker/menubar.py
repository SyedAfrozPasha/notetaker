import webbrowser
from datetime import datetime
from pathlib import Path

import rumps
from AppKit import NSAttributedString, NSFont, NSFontAttributeName

from notetaker import service
from notetaker.config import CONFIG_DIR, ConfigError, load_config
from notetaker.service import SessionInfo

APP_NAME = "Notetaker"
# 1s so the elapsed time in the strip advances every second, not in 2s jumps.
POLL_INTERVAL_SECONDS = 1
SETUP_WARNING_TITLE = "⚠️"
SAVING_TITLE = "Saving…"

# Template (black + alpha) PNGs, so macOS tints them for light/dark menu bars.
ASSETS_DIR = Path(__file__).parent / "assets"
ICON_IDLE = str(ASSETS_DIR / "menubar-idle.png")  # an open ring
ICON_RECORDING = str(ASSETS_DIR / "menubar-recording.png")  # the ring with a filled dot: a record glyph


def format_elapsed(start_time: datetime, now: datetime) -> str:
    """Formats elapsed time as MM:SS, or H:MM:SS past an hour, for display."""
    total_seconds = int((now - start_time).total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def menu_bar_title(info: SessionInfo | None, now: datetime) -> str | None:
    """The text shown next to the icon in the macOS menu bar strip: the
    elapsed time while recording, nothing (icon only) when idle."""
    if info is None:
        return None
    return format_elapsed(info.start_time, now)


def menu_bar_icon(info: SessionInfo | None) -> str:
    return ICON_RECORDING if info is not None else ICON_IDLE


def toggle_item_title(info: SessionInfo | None) -> str:
    return "Stop Recording" if info is not None else "Start Recording"


def auto_generated_title(now: datetime) -> str:
    return f"Meeting {now:%Y-%m-%d %H:%M}"


def _tabular_title(text: str) -> NSAttributedString:
    """An attributed title using tabular-figure (monospaced) digits, so the
    strip's ticking timer doesn't visibly jitter as digit shapes change width
    every second — plain NSStatusItem.setTitle_ uses the proportional system
    font, in which e.g. "1" is narrower than "8"."""
    font = NSFont.monospacedDigitSystemFontOfSize_weight_(NSFont.systemFontSize(), 0)
    return NSAttributedString.alloc().initWithString_attributes_(text, {NSFontAttributeName: font})


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
        super().__init__(APP_NAME, title=None, icon=ICON_IDLE, template=True, quit_button="Quit")
        self._dashboard_url = dashboard_url
        self._toggle_item = rumps.MenuItem("Start Recording", callback=self._on_toggle)
        items = [self._toggle_item]
        if dashboard_url:
            items += [rumps.separator, rumps.MenuItem("Open Dashboard", callback=self._on_open_dashboard)]
        self.menu = items
        self._reported_stop_job = None  # the finished StopJob already turned into a notification/alert
        self._timer = rumps.Timer(self._on_tick, POLL_INTERVAL_SECONDS)
        self._timer.start()

    def _on_open_dashboard(self, _sender):
        webbrowser.open(self._dashboard_url)

    def _set_strip_title(self, text: str | None) -> None:
        """Sets the strip's title, then — once the real status item exists
        (not in tests, which never call `.run()`) — overrides it with a
        tabular-figure rendering so a ticking timer doesn't jitter."""
        self.title = text
        if text is None:
            return
        try:
            nsstatusitem = self._nsapp.nsstatusitem
        except AttributeError:
            return
        nsstatusitem.setAttributedTitle_(_tabular_title(text))

    def _on_tick(self, _timer):
        try:
            config = load_config()
        except ConfigError:
            self._set_strip_title(SETUP_WARNING_TITLE)
            self._toggle_item.title = "Start Recording"
            return
        try:
            salvaged_path = service.check_and_salvage_orphan(config, CONFIG_DIR)
        except Exception:
            salvaged_path = None
        if salvaged_path is not None:
            self._notify("Recovered a crashed session", "Saved as a note", salvaged_path.name)
        job = service.current_stop_job()
        if job is not None and job.running:
            self._set_strip_title(SAVING_TITLE)
            self._apply_icon(None)
            self._toggle_item.title = SAVING_TITLE
            return
        if job is not None and job is not self._reported_stop_job:
            self._reported_stop_job = job
            self._report_stop_result(job)
        info = service.get_current_session_status(CONFIG_DIR)
        self._set_strip_title(menu_bar_title(info, datetime.now()))
        self._apply_icon(info)
        self._toggle_item.title = toggle_item_title(info)

    def _apply_icon(self, info: SessionInfo | None) -> None:
        icon = menu_bar_icon(info)
        if self.icon == icon:
            return  # setting the icon reloads the image; only do it on a state change
        self.icon = icon
        # Idle is a template image (macOS tints it); recording is the dashboard's
        # red logo in full colour, so it reads as "recording" at a glance.
        template = info is None
        if self.template != template:
            self.template = template

    def _report_stop_result(self, job) -> None:
        if job.error is not None:
            rumps.alert(title="Could not save the recording", message=job.error)
        elif job.note_path is not None and note_summarization_failed(job.note_path):
            self._notify("Recording saved", "Summarization failed", job.note_path.name)
        elif job.note_path is not None:
            self._notify("Recording saved", "", job.note_path.name)

    def _on_toggle(self, _sender):
        try:
            config = load_config()
        except ConfigError as exc:
            rumps.alert(title="Notetaker is not set up", message=str(exc))
            return
        job = service.current_stop_job()
        if job is not None and job.running:
            self._notify("Still saving the previous recording", "", job.phase)
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
        # Non-blocking: the menu bar stays responsive, shows "Saving…" while the
        # job runs, and the next tick after it finishes reports the outcome.
        service.begin_stop(info, config, CONFIG_DIR)
        self._set_strip_title(SAVING_TITLE)
        self._toggle_item.title = SAVING_TITLE

    @staticmethod
    def _notify(title: str, subtitle: str, message: str) -> None:
        try:
            rumps.notification(title=title, subtitle=subtitle, message=message)
        except Exception:
            pass  # best-effort — notification delivery for an unbundled script isn't guaranteed on modern macOS
