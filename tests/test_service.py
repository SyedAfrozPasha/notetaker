import json
import os
import sys
from unittest.mock import MagicMock

import pytest

from notetaker.config import Config
from notetaker.recorder import BlackHoleStatus
from notetaker.service import ServiceError, pid_alive, session_file_path, start_session


def test_session_file_path_is_under_config_dir(tmp_path):
    assert session_file_path(tmp_path) == tmp_path / "current_session.json"


def test_pid_alive_true_for_current_process():
    assert pid_alive(os.getpid()) is True


def test_pid_alive_false_for_nonexistent_pid():
    assert pid_alive(999999) is False


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
