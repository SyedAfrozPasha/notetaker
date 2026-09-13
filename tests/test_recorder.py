import os
import signal

import pytest

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

    def fake_capture(duration_seconds, out_path):
        call_count["n"] += 1
        out_path.write_bytes(b"")
        if call_count["n"] >= 2:
            os.kill(os.getpid(), signal.SIGTERM)

    transcriber = FakeTranscriber(["[00:00:00] hello", "[00:00:10] world"])
    run_recorder(session_dir, transcriber=transcriber, chunk_seconds=10, capture_fn=fake_capture)

    transcript = (session_dir / "transcript.txt").read_text()
    assert "hello" in transcript
    assert "world" in transcript
    assert call_count["n"] == 2


def test_run_recorder_logs_error_and_returns_when_capture_raises(tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    call_count = {"n": 0}

    def flaky_capture(duration_seconds, out_path):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("device removed")
        out_path.write_bytes(b"")

    transcriber = FakeTranscriber(["[00:00:00] hello"])

    # Should not raise, even though flaky_capture raises on its second call.
    run_recorder(session_dir, transcriber=transcriber, chunk_seconds=10, capture_fn=flaky_capture)

    transcript = (session_dir / "transcript.txt").read_text()
    assert "hello" in transcript
    assert "recording stopped due to error" in transcript
    assert "device removed" in transcript
    assert call_count["n"] == 2


def test_run_recorder_continues_when_one_chunk_fails_to_transcribe(tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    call_count = {"n": 0}

    def fake_capture(duration_seconds, out_path):
        call_count["n"] += 1
        out_path.write_bytes(b"")
        if call_count["n"] >= 3:
            os.kill(os.getpid(), signal.SIGTERM)

    class FlakyTranscriber:
        def __init__(self):
            self.calls = 0

        def transcribe_chunk(self, wav_path, elapsed_seconds):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("ctranslate2 hiccup")
            return f"[{int(elapsed_seconds):02d}] line {self.calls}"

    run_recorder(session_dir, transcriber=FlakyTranscriber(), chunk_seconds=10, capture_fn=fake_capture)

    lines = (session_dir / "transcript.txt").read_text().splitlines()
    assert lines[0] == "[00] line 1"
    assert "transcription failed for chunk 1" in lines[1]
    assert lines[2] == "[20] line 3"
    assert call_count["n"] == 3
    assert not list((session_dir / "chunks").glob("*.wav"))  # chunks cleaned up


def _write_wav(path, me_peak, others_peak, seconds=1, sample_rate=16000):
    import wave

    import numpy as np

    n = seconds * sample_rate
    me = np.full(n, me_peak, dtype=np.int16)
    others = np.full(n, others_peak, dtype=np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(np.column_stack([me, others]).tobytes())


def test_run_recorder_warns_once_when_meeting_channel_stays_silent_but_mic_is_live(tmp_path):
    from notetaker.recorder import NO_MEETING_AUDIO_WARNING

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    call_count = {"n": 0}

    def fake_capture(duration_seconds, out_path):
        call_count["n"] += 1
        _write_wav(out_path, me_peak=3000, others_peak=0)
        if call_count["n"] >= 5:
            os.kill(os.getpid(), signal.SIGTERM)

    run_recorder(session_dir, transcriber=FakeTranscriber([]), chunk_seconds=1, capture_fn=fake_capture)

    transcript = (session_dir / "transcript.txt").read_text()
    assert transcript.count(NO_MEETING_AUDIO_WARNING) == 1


def test_run_recorder_does_not_warn_when_meeting_audio_present(tmp_path):
    from notetaker.recorder import NO_MEETING_AUDIO_WARNING

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    call_count = {"n": 0}

    def fake_capture(duration_seconds, out_path):
        call_count["n"] += 1
        _write_wav(out_path, me_peak=3000, others_peak=2500)
        if call_count["n"] >= 5:
            os.kill(os.getpid(), signal.SIGTERM)

    run_recorder(session_dir, transcriber=FakeTranscriber([]), chunk_seconds=1, capture_fn=fake_capture)

    transcript_path = session_dir / "transcript.txt"
    assert not transcript_path.exists() or NO_MEETING_AUDIO_WARNING not in transcript_path.read_text()


def test_check_output_routing_flags_plain_output_devices(monkeypatch):
    from notetaker.recorder import check_output_routing

    monkeypatch.setattr("notetaker.recorder.default_output_device_name", lambda: "MacBook Pro Speakers")
    assert "Multi-Output" in check_output_routing()
    monkeypatch.setattr("notetaker.recorder.default_output_device_name", lambda: "BlackHole 2ch")
    assert "will not hear" in check_output_routing()
    monkeypatch.setattr("notetaker.recorder.default_output_device_name", lambda: "Multi-Output Device")
    assert check_output_routing() is None
    monkeypatch.setattr("notetaker.recorder.default_output_device_name", lambda: None)
    assert check_output_routing() is None


def test_check_microphone_routing_flags_blackhole_as_input(monkeypatch):
    from notetaker.recorder import check_microphone_routing

    monkeypatch.setattr("notetaker.recorder.default_input_device_name", lambda: "BlackHole 2ch")
    assert "your voice will not be recorded" in check_microphone_routing()
    monkeypatch.setattr("notetaker.recorder.default_input_device_name", lambda: "AirPods Pro")
    assert check_microphone_routing() is None


def test_live_capture_writes_stereo_chunk_me_left_others_right(monkeypatch):
    import numpy as np

    from notetaker.recorder import LiveCapture

    started = []

    class FakeStream:
        def __init__(self, device, channels, samplerate, dtype, callback):
            self.device = device
            self.callback = callback
            started.append(self)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("notetaker.recorder.sd.InputStream", FakeStream)
    capture = LiveCapture(system_device=7, mic_device="default", sample_rate=100)
    mic_stream, system_stream = started
    assert mic_stream.device is None  # "default" -> sounddevice default input
    assert system_stream.device == 7

    # Feed 1.5 chunks' worth of audio into each stream in odd-sized blocks.
    mic_stream.callback(np.full((80, 1), 11, dtype=np.int16), 80, None, None)
    mic_stream.callback(np.full((70, 1), 11, dtype=np.int16), 70, None, None)
    system_stream.callback(np.full((150, 1), 22, dtype=np.int16), 150, None, None)

    import tempfile
    import wave

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "chunk.wav")
        capture.capture_chunk(1, out)
        with wave.open(out, "rb") as wf:
            assert wf.getnchannels() == 2
            assert wf.getnframes() == 100
            frames = np.frombuffer(wf.readframes(100), dtype=np.int16).reshape(-1, 2)
    assert set(frames[:, 0]) == {11}
    assert set(frames[:, 1]) == {22}
    # 50 leftover frames remain buffered for the next chunk.
    assert len(capture._leftovers[0]) == 50
    capture.close()


def test_live_capture_raises_when_device_stops_delivering(monkeypatch):
    from notetaker.recorder import LiveCapture

    class FakeStream:
        def __init__(self, device, channels, samplerate, dtype, callback):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("notetaker.recorder.sd.InputStream", FakeStream)
    capture = LiveCapture(system_device=7, mic_device=None, sample_rate=100)
    assert capture.with_mic is False
    capture._queues[0].get = lambda timeout: (_ for _ in ()).throw(__import__("queue").Empty())
    with pytest.raises(RuntimeError, match="stopped delivering"):
        capture.capture_chunk(1, "/dev/null")
