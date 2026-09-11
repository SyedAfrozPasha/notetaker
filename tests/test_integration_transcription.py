from pathlib import Path

import pytest

from notetaker.transcriber import Transcriber

FIXTURE = Path(__file__).parent / "fixtures" / "silence.wav"


@pytest.mark.integration
def test_transcriber_processes_real_wav_without_error():
    transcriber = Transcriber("tiny")
    line = transcriber.transcribe_chunk(FIXTURE, elapsed_seconds=0)
    # Silence should produce no transcribable speech. With vad_filter=True,
    # faster-whisper should not hallucinate text (e.g. "you") on silence.
    assert line == ""
