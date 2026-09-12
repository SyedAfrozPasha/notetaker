import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from notetaker.config import Config, write_default_config
from notetaker.notes import NoteMeta, find_note_path, list_notes, read_note_body, write_note
from notetaker.recorder import BlackHoleStatus, check_blackhole, find_blackhole_device_index
from notetaker.summarizer import Summary, check_apple_local_preflight, get_provider, summarize_transcript
from notetaker.transcriber import Transcriber

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


def start_session(title: str, config: Config, config_dir: Path) -> SessionInfo:
    session_file = session_file_path(config_dir)
    if session_file.exists():
        try:
            raw = json.loads(session_file.read_text())
            if pid_alive(raw["pid"]):
                raise ServiceError("a session is already running. Run `notetaker stop` first.")
        except (ValueError, KeyError, TypeError, OSError):
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
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise ServiceError(
            f"session file at {session_file} is corrupt or unreadable. "
            f"Check ~/.notetaker/sessions/ manually for a salvageable transcript, "
            f"then remove {session_file} to reset."
        ) from exc


def _terminate_recorder(pid: int) -> None:
    if pid_alive(pid):
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            if not pid_alive(pid):
                break
            time.sleep(1)


def stop_session(info: SessionInfo, config: Config, config_dir: Path, end_time: datetime | None = None) -> Path:
    session_file = session_file_path(config_dir)

    _terminate_recorder(info.pid)

    transcript_path = info.session_dir / "transcript.txt"
    transcript = transcript_path.read_text() if transcript_path.exists() else ""
    transcript_lines = transcript.splitlines()

    if end_time is None:
        end_time = datetime.now()
    duration_minutes = int((end_time - info.start_time).total_seconds() // 60)

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
    transcript_sidecar_path = note_path.parent / f"{note_path.stem}.transcript.txt"
    transcript_sidecar_path.write_text(transcript)

    shutil.rmtree(info.session_dir, ignore_errors=True)
    session_file.unlink(missing_ok=True)

    return note_path


def cancel_session(info: SessionInfo, config_dir: Path) -> None:
    session_file = session_file_path(config_dir)

    _terminate_recorder(info.pid)

    shutil.rmtree(info.session_dir, ignore_errors=True)
    session_file.unlink(missing_ok=True)


def list_all_notes(config: Config) -> list[NoteMeta]:
    return list_notes(config.notes_dir)


def get_note_body(config: Config, note_id: str) -> str:
    path = find_note_path(config.notes_dir, note_id)
    if path is None:
        raise ServiceError(f"no note found with id '{note_id}'.")
    return read_note_body(path)


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
