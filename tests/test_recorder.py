import os
import signal

from notetaker.recorder import BlackHoleStatus, check_blackhole, run_recorder


class FakeTranscriber:
    def __init__(self, lines):
        self.lines = list(lines)
        self.calls = 0

    def transcribe_chunk(self, wav_path, elapsed_seconds):
        self.calls += 1
        return self.lines.pop(0) if self.lines else ""


def test_check_blackhole_not_installed(monkeypatch):
    import subprocess as sp

    monkeypatch.setattr("notetaker.recorder.subprocess.run", lambda *a, **k: sp.CompletedProcess(a, returncode=1))
    monkeypatch.setattr("notetaker.recorder.find_blackhole_device_index", lambda: None)
    assert check_blackhole() == BlackHoleStatus.NOT_INSTALLED


def test_check_blackhole_installed_not_active(monkeypatch):
    import subprocess as sp

    monkeypatch.setattr("notetaker.recorder.subprocess.run", lambda *a, **k: sp.CompletedProcess(a, returncode=0))
    monkeypatch.setattr("notetaker.recorder.find_blackhole_device_index", lambda: None)
    assert check_blackhole() == BlackHoleStatus.INSTALLED_NOT_ACTIVE


def test_check_blackhole_active(monkeypatch):
    import subprocess as sp

    monkeypatch.setattr("notetaker.recorder.subprocess.run", lambda *a, **k: sp.CompletedProcess(a, returncode=1))
    monkeypatch.setattr("notetaker.recorder.find_blackhole_device_index", lambda: 3)
    assert check_blackhole() == BlackHoleStatus.ACTIVE


def test_run_recorder_stops_on_sigterm_and_appends_transcript(tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    call_count = {"n": 0}

    def fake_capture(device_index, duration_seconds, out_path, sample_rate=16000):
        call_count["n"] += 1
        out_path.write_bytes(b"")
        if call_count["n"] >= 2:
            os.kill(os.getpid(), signal.SIGTERM)

    transcriber = FakeTranscriber(["[00:00:00] hello", "[00:00:10] world"])
    run_recorder(session_dir, device_index=0, transcriber=transcriber, chunk_seconds=10, capture_fn=fake_capture)

    transcript = (session_dir / "transcript.txt").read_text()
    assert "hello" in transcript
    assert "world" in transcript
    assert call_count["n"] == 2
