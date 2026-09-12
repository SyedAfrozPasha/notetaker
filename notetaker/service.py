import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from notetaker.config import Config
from notetaker.recorder import BlackHoleStatus, check_blackhole, find_blackhole_device_index

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
