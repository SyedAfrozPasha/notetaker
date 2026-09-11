from pathlib import Path

import pytest

from notetaker.transcriber import Transcriber

FIXTURE = Path(__file__).parent / "fixtures" / "silence.wav"


@pytest.mark.integration
def test_transcriber_processes_real_wav_without_error():
    transcriber = Transcriber("tiny")
    line = transcriber.transcribe_chunk(FIXTURE, elapsed_seconds=0)
    # Silence should produce no transcribable speech — the assertion is that
    # this doesn't raise and returns a string (possibly empty), proving the
    # faster-whisper wiring (model load, file read, segment iteration) works.
    assert isinstance(line, str)
