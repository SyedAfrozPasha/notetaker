import os
import signal

import pytest
from unittest.mock import MagicMock

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
                raise RuntimeError("ohr hiccup")
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


def test_run_recorder_silence_threshold_is_configurable(tmp_path):
    # Tap mode waits much longer: meeting audio is legitimately absent until an app plays.
    from notetaker.recorder import NO_MEETING_AUDIO_WARNING_TAP

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    call_count = {"n": 0}

    def fake_capture(duration_seconds, out_path):
        call_count["n"] += 1
        _write_wav(out_path, me_peak=3000, others_peak=0)
        if call_count["n"] >= 5:
            os.kill(os.getpid(), signal.SIGTERM)

    run_recorder(
        session_dir, transcriber=FakeTranscriber([]), chunk_seconds=1, capture_fn=fake_capture,
        no_meeting_audio_warning=NO_MEETING_AUDIO_WARNING_TAP, silent_chunks_before_warning=18,
    )

    transcript_path = session_dir / "transcript.txt"
    assert not transcript_path.exists() or NO_MEETING_AUDIO_WARNING_TAP not in transcript_path.read_text()


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


def test_live_capture_pads_silence_when_a_source_stalls_and_reports_once(monkeypatch):
    import numpy as np

    from notetaker.recorder import LiveCapture

    started = []

    class FakeStream:
        def __init__(self, device, channels, samplerate, dtype, callback):
            self.callback = callback
            started.append(self)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("notetaker.recorder.sd.InputStream", FakeStream)
    # Freeze the clock: this test is about stall padding, not lead-in alignment
    # (covered by test_live_capture_aligns_late_starting_source_with_leading_silence).
    monkeypatch.setattr("notetaker.recorder.time.monotonic", lambda: 100.0)
    stalls = []
    capture = LiveCapture(system_device=7, mic_device="default", sample_rate=100, on_stall=stalls.append)
    capture.STALL_SECONDS = 0.01
    mic_stream, system_stream = started

    import tempfile
    import wave

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "chunk.wav")
        # Mic delivers a full chunk; the tap has produced nothing yet (no app playing).
        mic_stream.callback(np.full((100, 1), 11, dtype=np.int16), 100, None, None)
        capture.capture_chunk(1, out)
        with wave.open(out, "rb") as wf:
            frames = np.frombuffer(wf.readframes(100), dtype=np.int16).reshape(-1, 2)
        assert set(frames[:, 0]) == {11}
        assert set(frames[:, 1]) == {0}  # padded, not stalled
        assert stalls == ["meeting audio"]

        # Second chunk: tap now half-delivers, mic stalls entirely.
        system_stream.callback(np.full((40, 1), 22, dtype=np.int16), 40, None, None)
        capture.capture_chunk(1, out)
        with wave.open(out, "rb") as wf:
            frames = np.frombuffer(wf.readframes(100), dtype=np.int16).reshape(-1, 2)
        assert set(frames[:, 0]) == {0}
        assert list(frames[:40, 1]) == [22] * 40 and set(frames[40:, 1]) == {0}
        assert stalls == ["meeting audio", "microphone"]

        # Third chunk: both stalled again — no repeated reports.
        capture.capture_chunk(1, out)
        assert stalls == ["meeting audio", "microphone"]
    capture.close()


def test_live_capture_single_source_pads_when_nothing_arrives(monkeypatch):
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
    capture.STALL_SECONDS = 0.01
    assert capture.with_mic is False
    import tempfile
    import wave

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "chunk.wav")
        capture.capture_chunk(1, out)
        with wave.open(out, "rb") as wf:
            assert wf.getnchannels() == 1 and wf.getnframes() == 100


def test_main_tap_mode_creates_tap_resolves_device_and_closes_everything(monkeypatch, tmp_path):
    from notetaker import recorder

    events = []
    tap = MagicMock(device_name="notetaker-system-audio", fell_back_to_global=True)
    tap.close.side_effect = lambda: events.append("tap.close")
    monkeypatch.setattr("notetaker.systemaudio.create_system_audio_tap", lambda bundle: events.append(("tap", bundle)) or tap)
    monkeypatch.setattr(recorder, "_hard_exit", lambda code: events.append(("hard_exit", code)))
    monkeypatch.setattr(recorder, "portaudio_device_index", lambda name: events.append(("index", name)) or 4)

    class FakeCapture:
        def __init__(self, system_device, mic, on_stall=None):
            events.append(("capture", system_device, mic))
            self.on_stall = on_stall

        def capture_chunk(self, seconds, out):
            pass

        def close(self):
            events.append("capture.close")

    captures = []
    monkeypatch.setattr(recorder, "LiveCapture", lambda *a, **k: captures.append(FakeCapture(*a, **k)) or captures[-1])
    monkeypatch.setattr(recorder, "Transcriber", lambda: events.append("model"))
    monkeypatch.setattr(recorder, "start_ohr_server", lambda: events.append("ohr_start") or "OHRPROC")
    monkeypatch.setattr(recorder, "stop_ohr_server", lambda proc: events.append(("ohr_stop", proc)))
    monkeypatch.setattr(
        recorder, "run_recorder",
        lambda *a, **k: events.append(("run", k["no_meeting_audio_warning"], k["silent_chunks_before_warning"])),
    )

    recorder.main([
        "--session-dir", str(tmp_path), "--mic", "default",
        "--system-audio", "tap", "--tap-process", "com.microsoft.teams2",
    ])

    assert events == [
        "ohr_start",  # spawned before the tap so a failure to start leaves nothing else to tear down
        ("tap", "com.microsoft.teams2"),
        ("index", "notetaker-system-audio"),
        ("capture", 4, "default"),
        "model",
        ("run", recorder.NO_MEETING_AUDIO_WARNING_TAP, recorder.SILENT_CHUNKS_BEFORE_WARNING_TAP),
        ("ohr_stop", "OHRPROC"),
        "tap.close",  # destroyed before any stream stop, then hard exit (see recorder.main)
        ("hard_exit", 0),
    ]
    transcript = (tmp_path / "transcript.txt").read_text()
    assert "com.microsoft.teams2 is not running" in transcript
    # A stalled mic is reported into the transcript; quiet meeting audio is not.
    captures[0].on_stall("meeting audio")
    captures[0].on_stall("microphone")
    transcript = (tmp_path / "transcript.txt").read_text()
    assert transcript.count("[warning]") == 1 and "microphone stopped" in transcript


def test_main_blackhole_mode_uses_given_device_and_no_tap(monkeypatch, tmp_path):
    from notetaker import recorder

    events = []
    monkeypatch.setattr(
        "notetaker.systemaudio.create_system_audio_tap", lambda bundle: (_ for _ in ()).throw(AssertionError("no tap"))
    )

    class FakeCapture:
        def __init__(self, system_device, mic, on_stall=None):
            events.append(("capture", system_device, mic))

        def capture_chunk(self, seconds, out):
            pass

        def close(self):
            pass

    monkeypatch.setattr(recorder, "LiveCapture", FakeCapture)
    monkeypatch.setattr(recorder, "Transcriber", lambda: None)
    monkeypatch.setattr(recorder, "start_ohr_server", lambda: "OHRPROC")
    monkeypatch.setattr(recorder, "stop_ohr_server", lambda proc: None)
    monkeypatch.setattr(recorder, "run_recorder", lambda *a, **k: events.append(("run", k["no_meeting_audio_warning"])))

    recorder.main([
        "--session-dir", str(tmp_path), "--mic", "none",
        "--system-audio", "blackhole", "--system-device", "2",
    ])

    assert events == [("capture", 2, None), ("run", recorder.NO_MEETING_AUDIO_WARNING)]


def test_portaudio_device_index_reenumerates_and_finds_input_device(monkeypatch):
    from notetaker import recorder

    events = []
    monkeypatch.setattr(recorder.sd, "_terminate", lambda: events.append("terminate"))
    monkeypatch.setattr(recorder.sd, "_initialize", lambda: events.append("initialize"))
    monkeypatch.setattr(recorder.time, "sleep", lambda s: events.append("sleep"))
    # Core Audio publishes the device asynchronously: absent on the first
    # enumeration, present on the second.
    listings = iter([
        [{"name": "Mic", "max_input_channels": 1}],
        [{"name": "Mic", "max_input_channels": 1}, {"name": "notetaker-system-audio", "max_input_channels": 2}],
    ])
    monkeypatch.setattr(recorder.sd, "query_devices", lambda: next(listings))

    assert recorder.portaudio_device_index("notetaker-system-audio") == 1
    assert events == ["terminate", "initialize", "sleep", "terminate", "initialize"]

    monkeypatch.setattr(recorder.sd, "query_devices", lambda: [])
    with pytest.raises(RuntimeError, match="not found"):
        recorder.portaudio_device_index("nope", timeout_seconds=0)


def _fake_health_ctx():
    class FakeCtx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return FakeCtx()


def test_start_ohr_server_spawns_and_returns_once_healthy(monkeypatch):
    from notetaker import recorder

    proc = MagicMock()
    proc.poll.return_value = None
    monkeypatch.setattr(recorder.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(recorder.urllib.request, "urlopen", lambda url, timeout=1: _fake_health_ctx())

    assert recorder.start_ohr_server() is proc


def test_start_ohr_server_polls_the_health_endpoint_not_under_v1(monkeypatch):
    # ohr's health check lives at the server root (GET /health), not under
    # /v1 like /v1/audio/transcriptions and /v1/models — confirmed against a
    # real `ohr --serve`: /health -> 200, /v1/health -> 404.
    from notetaker import recorder

    proc = MagicMock()
    proc.poll.return_value = None
    requested_urls = []

    def fake_urlopen(url, timeout=1):
        requested_urls.append(url)
        return _fake_health_ctx()

    monkeypatch.setattr(recorder.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(recorder.urllib.request, "urlopen", fake_urlopen)

    recorder.start_ohr_server()

    assert requested_urls == [f"http://127.0.0.1:{recorder.OHR_PORT}/health"]


def test_start_ohr_server_retries_health_check_until_ready(monkeypatch):
    from notetaker import recorder

    proc = MagicMock()
    proc.poll.return_value = None
    attempts = iter([recorder.urllib.error.URLError("refused"), recorder.urllib.error.URLError("refused"), None])

    def fake_urlopen(url, timeout=1):
        outcome = next(attempts)
        if outcome is not None:
            raise outcome
        return _fake_health_ctx()

    monkeypatch.setattr(recorder.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(recorder.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(recorder.time, "sleep", lambda s: None)

    assert recorder.start_ohr_server() is proc


def test_start_ohr_server_raises_when_process_exits_immediately(monkeypatch):
    from notetaker import recorder

    proc = MagicMock()
    proc.poll.return_value = 1
    proc.returncode = 1
    monkeypatch.setattr(recorder.subprocess, "Popen", lambda *a, **k: proc)

    with pytest.raises(RuntimeError, match="exited"):
        recorder.start_ohr_server()


def test_start_ohr_server_raises_and_terminates_when_never_healthy(monkeypatch):
    from notetaker import recorder

    proc = MagicMock()
    proc.poll.return_value = None
    monkeypatch.setattr(recorder.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(
        recorder.urllib.request, "urlopen", lambda url, timeout=1: (_ for _ in ()).throw(recorder.urllib.error.URLError("refused"))
    )
    monkeypatch.setattr(recorder.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="did not become healthy"):
        recorder.start_ohr_server(timeout_seconds=0)
    proc.terminate.assert_called_once()


def test_stop_ohr_server_does_nothing_if_already_exited(monkeypatch):
    from notetaker import recorder

    proc = MagicMock()
    proc.poll.return_value = 0
    recorder.stop_ohr_server(proc)
    proc.terminate.assert_not_called()


def test_stop_ohr_server_terminates_running_process(monkeypatch):
    from notetaker import recorder

    proc = MagicMock()
    proc.poll.return_value = None
    recorder.stop_ohr_server(proc)
    proc.terminate.assert_called_once()
    proc.kill.assert_not_called()


def test_stop_ohr_server_kills_if_terminate_does_not_finish_in_time(monkeypatch):
    import subprocess as sp

    from notetaker import recorder

    proc = MagicMock()
    proc.poll.return_value = None
    proc.wait.side_effect = sp.TimeoutExpired(cmd="ohr", timeout=5)
    recorder.stop_ohr_server(proc)
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


def test_main_tap_mode_hard_exits_nonzero_when_recording_crashes(monkeypatch, tmp_path):
    from notetaker import recorder

    events = []
    tap = MagicMock(device_name="notetaker-system-audio", fell_back_to_global=False)
    tap.close.side_effect = lambda: events.append("tap.close")
    monkeypatch.setattr("notetaker.systemaudio.create_system_audio_tap", lambda bundle: tap)
    monkeypatch.setattr(recorder, "portaudio_device_index", lambda name: 4)
    monkeypatch.setattr(recorder, "_hard_exit", lambda code: events.append(("hard_exit", code)))

    class FakeCapture:
        def __init__(self, *a, **k):
            pass

        def capture_chunk(self, seconds, out):
            pass

        def close(self):
            events.append("capture.close")

    monkeypatch.setattr(recorder, "LiveCapture", FakeCapture)
    monkeypatch.setattr(recorder, "Transcriber", lambda: None)
    monkeypatch.setattr(recorder, "start_ohr_server", lambda: "OHRPROC")
    monkeypatch.setattr(recorder, "stop_ohr_server", lambda proc: events.append(("ohr_stop", proc)))
    monkeypatch.setattr(recorder, "run_recorder", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    recorder.main(["--session-dir", str(tmp_path), "--system-audio", "tap"])

    assert events == [("ohr_stop", "OHRPROC"), "tap.close", ("hard_exit", 1)]


def test_live_capture_aligns_late_starting_source_with_leading_silence(monkeypatch):
    import numpy as np

    from notetaker.recorder import LiveCapture

    started = []

    class FakeStream:
        def __init__(self, device, channels, samplerate, dtype, callback):
            self.callback = callback
            started.append(self)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("notetaker.recorder.sd.InputStream", FakeStream)
    clock = {"now": 100.0}
    monkeypatch.setattr("notetaker.recorder.time.monotonic", lambda: clock["now"])
    capture = LiveCapture(system_device=7, mic_device="default", sample_rate=100)
    capture.STALL_SECONDS = 0.01
    mic_stream, tap_stream = started

    # Mic delivers from the start; the tap only starts 0.4s in (an app began playing).
    clock["now"] = 100.1
    mic_stream.callback(np.full((10, 1), 11, dtype=np.int16), 10, None, None)
    clock["now"] = 100.5
    tap_stream.callback(np.full((10, 1), 22, dtype=np.int16), 10, None, None)
    clock["now"] = 101.0
    mic_stream.callback(np.full((90, 1), 11, dtype=np.int16), 90, None, None)
    tap_stream.callback(np.full((50, 1), 22, dtype=np.int16), 50, None, None)

    import tempfile
    import wave

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "chunk.wav")
        capture.capture_chunk(1, out)
        with wave.open(out, "rb") as wf:
            frames = np.frombuffer(wf.readframes(100), dtype=np.int16).reshape(-1, 2)
    # Tap channel: 40 samples of lead-in silence (0.5s - 0.1s of block), then audio.
    assert set(frames[:40, 1]) == {0}
    assert set(frames[40:, 1]) == {22}
    assert set(frames[:, 0]) == {11}
    capture.close()
