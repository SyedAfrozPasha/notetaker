from types import SimpleNamespace

from notetaker.transcriber import Transcriber, append_transcript_line, format_timestamp


def test_format_timestamp():
    assert format_timestamp(0) == "00:00:00"
    assert format_timestamp(7) == "00:00:07"
    assert format_timestamp(3665) == "01:01:05"


def test_append_transcript_line_appends(tmp_path):
    path = tmp_path / "transcript.txt"
    append_transcript_line(path, "[00:00:01] hello")
    append_transcript_line(path, "[00:00:05] world")
    assert path.read_text() == "[00:00:01] hello\n[00:00:05] world\n"


class FakeWhisperModel:
    def __init__(self, segments_text):
        self._segments_text = segments_text

    def transcribe(self, wav_path, vad_filter=False, language=None):
        segments = [SimpleNamespace(text=t) for t in self._segments_text]
        return segments, None


def test_transcribe_chunk_returns_timestamped_line(tmp_path):
    wav_path = tmp_path / "chunk_0.wav"
    wav_path.write_bytes(b"")
    transcriber = Transcriber("base.en", _model=FakeWhisperModel([" hello ", " world"]))
    line = transcriber.transcribe_chunk(wav_path, elapsed_seconds=7)
    assert line == "[00:00:07] hello world"


def test_transcribe_chunk_returns_empty_string_for_silence(tmp_path):
    wav_path = tmp_path / "chunk_0.wav"
    wav_path.write_bytes(b"")
    transcriber = Transcriber("base.en", _model=FakeWhisperModel([]))
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


class FakePerChannelModel:
    """Answers with different segments depending on which channel it is fed
    (positive samples = Me, negative = Others), recording what it was given."""

    def __init__(self, me_segments, others_segments):
        self._me = me_segments
        self._others = others_segments
        self.inputs = []

    def transcribe(self, audio, vad_filter=False, language=None):
        self.inputs.append(audio)
        segments = self._me if float(audio[0]) > 0 else self._others
        return [SimpleNamespace(text=t, start=s) for s, t in segments], None


def test_transcribe_chunk_labels_speakers_and_merges_by_time_for_stereo(tmp_path):
    wav_path = tmp_path / "chunk.wav"
    _write_stereo_wav(wav_path)
    model = FakePerChannelModel(
        me_segments=[(0.5, " hi all "), (1.0, "let's start")],
        others_segments=[(3.0, "sounds good"), (4.2, "I'll send the deck")],
    )
    transcriber = Transcriber("base.en", _model=model)

    lines = transcriber.transcribe_chunk(wav_path, elapsed_seconds=60).splitlines()

    assert lines == [
        "[00:01:00] Me: hi all let's start",
        "[00:01:03] Others: sounds good I'll send the deck",
    ]
    assert len(model.inputs) == 2  # one Whisper pass per channel, as float32 arrays
    assert all(inp.dtype.name == "float32" for inp in model.inputs)


def test_transcribe_chunk_returns_empty_for_silent_stereo_chunk(tmp_path):
    wav_path = tmp_path / "chunk.wav"
    _write_stereo_wav(wav_path)
    transcriber = Transcriber("base.en", _model=FakePerChannelModel([], []))
    assert transcriber.transcribe_chunk(wav_path, elapsed_seconds=0) == ""


def test_transcriber_loads_local_model_dir_without_network(monkeypatch):
    calls = {}

    def fake_whisper(model, device, compute_type, local_files_only=False):
        calls.update(model=model, local_files_only=local_files_only)
        return object()

    monkeypatch.setattr("notetaker.transcriber.WhisperModel", fake_whisper)
    Transcriber("base.en", model_path="/models/base.en")
    assert calls == {"model": "/models/base.en", "local_files_only": True}


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
    ]

    kept = drop_echoes(spoken)

    assert [(s, l) for s, l, _ in kept] == [
        (33.0, "Others"), (40.0, "Others"), (41.0, "Me"), (70.0, "Me"), (80.0, "Others"), (80.5, "Me"),
    ]


def test_transcribe_chunk_drops_echo_lines(tmp_path):
    wav_path = tmp_path / "chunk.wav"
    _write_stereo_wav(wav_path)
    model = FakePerChannelModel(
        me_segments=[(0.5, "There has been a lot of hype around"), (6.0, "good question")],
        others_segments=[(0.2, "There has been a lot of hype around model context protocol")],
    )
    lines = Transcriber("base.en", _model=model).transcribe_chunk(wav_path, elapsed_seconds=0).splitlines()
    assert lines == [
        "[00:00:00] Others: There has been a lot of hype around model context protocol",
        "[00:00:06] Me: good question",
    ]
