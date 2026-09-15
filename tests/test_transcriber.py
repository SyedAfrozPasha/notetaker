import io
import json
import urllib.error

import pytest

from notetaker.transcriber import (
    OhrClient,
    OhrError,
    Segment,
    Transcriber,
    append_transcript_line,
    check_ohr_preflight,
    format_timestamp,
)


def test_format_timestamp():
    assert format_timestamp(0) == "00:00:00"
    assert format_timestamp(7) == "00:00:07"
    assert format_timestamp(3665) == "01:01:05"


def test_append_transcript_line_appends(tmp_path):
    path = tmp_path / "transcript.txt"
    append_transcript_line(path, "[00:00:01] hello")
    append_transcript_line(path, "[00:00:05] world")
    assert path.read_text() == "[00:00:01] hello\n[00:00:05] world\n"


def _fake_urlopen_ctx(payload: dict):
    class FakeCtx:
        def __enter__(self):
            return io.BytesIO(json.dumps(payload).encode())

        def __exit__(self, *a):
            return False

    return FakeCtx()


def test_ohr_client_parses_verbose_json_segments(monkeypatch):
    payload = {
        "text": "hello world",
        "segments": [
            {"id": 0, "start": 0.5, "end": 1.0, "text": "hello"},
            {"id": 1, "start": 1.2, "end": 2.0, "text": "world"},
        ],
    }
    monkeypatch.setattr("notetaker.transcriber.urllib.request.urlopen", lambda req, timeout=60: _fake_urlopen_ctx(payload))

    segments = OhrClient().transcribe(b"RIFF....WAVEfmt ")

    assert [(s.start, s.text) for s in segments] == [(0.5, "hello"), (1.2, "world")]


def test_ohr_client_sends_a_full_bcp47_locale_not_a_bare_language_code(monkeypatch):
    # ohr's SpeechTranscriber rejects a bare "en" as an "unsupported locale" —
    # confirmed against a real `ohr --serve`: language=en -> 500, language=en-US -> 200.
    captured = {}

    def fake_urlopen(req, timeout=60):
        captured["body"] = req.data
        return _fake_urlopen_ctx({"text": "", "segments": []})

    monkeypatch.setattr("notetaker.transcriber.urllib.request.urlopen", fake_urlopen)

    OhrClient().transcribe(b"RIFF....WAVEfmt ")

    body = captured["body"]
    assert b'name="language"\r\n\r\nen-US\r\n' in body
    assert b'name="language"\r\n\r\nen\r\n' not in body


def test_ohr_client_returns_empty_list_for_no_segments(monkeypatch):
    monkeypatch.setattr(
        "notetaker.transcriber.urllib.request.urlopen", lambda req, timeout=60: _fake_urlopen_ctx({"text": "", "segments": []})
    )
    assert OhrClient().transcribe(b"RIFF") == []


def test_ohr_client_raises_on_connection_failure(monkeypatch):
    def raise_error(req, timeout=60):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("notetaker.transcriber.urllib.request.urlopen", raise_error)
    with pytest.raises(OhrError):
        OhrClient().transcribe(b"RIFF")


def test_ohr_client_raises_on_http_error(monkeypatch):
    def raise_error(req, timeout=60):
        raise urllib.error.HTTPError("http://x", 500, "Internal Server Error", {}, None)

    monkeypatch.setattr("notetaker.transcriber.urllib.request.urlopen", raise_error)
    with pytest.raises(OhrError):
        OhrClient().transcribe(b"RIFF")


def test_check_ohr_preflight_flags_non_macos(monkeypatch):
    monkeypatch.setattr("notetaker.transcriber.platform.system", lambda: "Linux")
    problems = check_ohr_preflight()
    assert any("macOS" in p for p in problems)


def test_check_ohr_preflight_flags_old_macos(monkeypatch):
    monkeypatch.setattr("notetaker.transcriber.platform.system", lambda: "Darwin")
    monkeypatch.setattr("notetaker.transcriber.platform.mac_ver", lambda: ("15.1", ("", "", ""), ""))
    problems = check_ohr_preflight()
    assert any("macOS 26" in p for p in problems)


def test_check_ohr_preflight_flags_missing_ohr(monkeypatch):
    monkeypatch.setattr("notetaker.transcriber.platform.system", lambda: "Darwin")
    monkeypatch.setattr("notetaker.transcriber.platform.mac_ver", lambda: ("26.0", ("", "", ""), ""))
    monkeypatch.setattr("notetaker.transcriber.shutil.which", lambda name: None)
    problems = check_ohr_preflight()
    assert any("ohr is not installed" in p for p in problems)


def test_check_ohr_preflight_passes_when_everything_ready(monkeypatch):
    monkeypatch.setattr("notetaker.transcriber.platform.system", lambda: "Darwin")
    monkeypatch.setattr("notetaker.transcriber.platform.mac_ver", lambda: ("26.0", ("", "", ""), ""))
    monkeypatch.setattr("notetaker.transcriber.shutil.which", lambda name: "/opt/homebrew/bin/ohr")
    assert check_ohr_preflight() == []


class FakeOhrClient:
    """Answers each successive `transcribe()` call with the next entry in
    `responses` (one per channel, in the order Transcriber calls them —
    channel 0/Me then channel 1/Others), recording the wav bytes it was given."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls: list[bytes] = []

    def transcribe(self, wav_bytes: bytes) -> list[Segment]:
        self.calls.append(wav_bytes)
        return [Segment(start=start, text=text) for start, text in self._responses.pop(0)]


def test_transcribe_chunk_returns_timestamped_line(tmp_path):
    wav_path = tmp_path / "chunk_0.wav"
    wav_path.write_bytes(b"RIFF....WAVEfmt ")
    transcriber = Transcriber(_client=FakeOhrClient([(0.0, " hello "), (0.5, " world")]))
    line = transcriber.transcribe_chunk(wav_path, elapsed_seconds=7)
    assert line == "[00:00:07] hello world"


def test_transcribe_chunk_returns_empty_string_for_silence(tmp_path):
    wav_path = tmp_path / "chunk_0.wav"
    wav_path.write_bytes(b"RIFF....WAVEfmt ")
    transcriber = Transcriber(_client=FakeOhrClient([]))
    assert transcriber.transcribe_chunk(wav_path, elapsed_seconds=0) == ""


def _write_stereo_wav(path, sample_rate=16000, seconds=1):
    import wave

    import numpy as np

    n = sample_rate * seconds
    data = np.column_stack([np.full(n, 1000, dtype=np.int16), np.full(n, -1000, dtype=np.int16)])
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(data.tobytes())


def test_transcribe_chunk_labels_speakers_and_merges_by_time_for_stereo(tmp_path):
    wav_path = tmp_path / "chunk.wav"
    _write_stereo_wav(wav_path)
    client = FakeOhrClient(
        [(0.5, " hi all "), (1.0, "let's start")],
        [(3.0, "sounds good"), (4.2, "I'll send the deck")],
    )
    transcriber = Transcriber(_client=client)

    lines = transcriber.transcribe_chunk(wav_path, elapsed_seconds=60).splitlines()

    assert lines == [
        "[00:01:00] Me: hi all let's start",
        "[00:01:03] Others: sounds good I'll send the deck",
    ]
    assert len(client.calls) == 2  # one ohr call per channel
    assert all(wav_bytes.startswith(b"RIFF") for wav_bytes in client.calls)  # each is its own valid mono WAV


def test_transcribe_chunk_returns_empty_for_silent_stereo_chunk(tmp_path):
    wav_path = tmp_path / "chunk.wav"
    _write_stereo_wav(wav_path)
    transcriber = Transcriber(_client=FakeOhrClient([], []))
    assert transcriber.transcribe_chunk(wav_path, elapsed_seconds=0) == ""


def test_drop_echoes_removes_mic_copy_of_meeting_audio_but_keeps_real_speech():
    from notetaker.transcriber import drop_echoes

    spoken = [
        (33.0, "Others", "There has been a lot of hype around model context protocol"),
        (33.4, "Me", "There has been a lot of hype around"),  # speakers heard by the mic
        (40.0, "Others", "An extremely simple explanation of MCP today."),
        (40.2, "Me", "extremely simple explanation of MCP today"),
        (41.0, "Me", "Can you share the slides afterwards?"),  # genuinely me, overlapping
        (70.0, "Me", "There has been a lot of hype around"),  # same words, far away in time: kept
        (80.0, "Others", "Shall we move on to the next topic, any objections?"),
        (80.5, "Me", "Yes"),  # short fragment: never treated as an echo by containment
        (90.0, "Others", "and write code or pseudo code on the spot."),
        (91.5, "Me", "the spot."),  # echo tail, verbatim inside the Others line: dropped
    ]

    kept = drop_echoes(spoken)

    assert [(s, l) for s, l, _ in kept] == [
        (33.0, "Others"), (40.0, "Others"), (41.0, "Me"), (70.0, "Me"), (80.0, "Others"), (80.5, "Me"),
        (90.0, "Others"),
    ]


def test_transcribe_chunk_drops_echo_lines(tmp_path):
    wav_path = tmp_path / "chunk.wav"
    _write_stereo_wav(wav_path)
    client = FakeOhrClient(
        [(0.5, "There has been a lot of hype around"), (6.0, "good question")],
        [(0.2, "There has been a lot of hype around model context protocol")],
    )
    lines = Transcriber(_client=client).transcribe_chunk(wav_path, elapsed_seconds=0).splitlines()
    assert lines == [
        "[00:00:00] Others: There has been a lot of hype around model context protocol",
        "[00:00:06] Me: good question",
    ]


def test_drop_echoes_uses_previous_chunk_others_across_boundary():
    from notetaker.transcriber import drop_echoes

    # Others said this at 8.5s of the previous chunk (-1.5s relative to this one);
    # the mic's echo begins in this chunk at 0.2s.
    previous = [(-1.5, "provide an extremely simple explanation of MCP today")]
    spoken = [(0.2, "Me", "an extremely simple explanation of MCP today"), (3.0, "Me", "Thanks, that helps.")]

    kept = drop_echoes(spoken, previous_others=previous)

    assert kept == [(3.0, "Me", "Thanks, that helps.")]


def test_transcriber_remembers_previous_chunk_others_for_echo_removal(tmp_path):
    wav_path = tmp_path / "chunk.wav"
    _write_stereo_wav(wav_path)
    transcriber = Transcriber(_client=FakeOhrClient([], [(8.5, "provide an extremely simple explanation of MCP today")]))
    transcriber.transcribe_chunk(wav_path, elapsed_seconds=0)

    transcriber._client = FakeOhrClient([(0.2, "an extremely simple explanation of MCP today")], [])
    assert transcriber.transcribe_chunk(wav_path, elapsed_seconds=10) == ""
