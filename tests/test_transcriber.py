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

    def transcribe(self, wav_path):
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
