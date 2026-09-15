from pathlib import Path

import pytest

from notetaker.recorder import start_ohr_server, stop_ohr_server
from notetaker.transcriber import Transcriber, check_ohr_preflight

FIXTURE = Path(__file__).parent / "fixtures" / "silence.wav"


@pytest.mark.integration
def test_transcriber_processes_real_wav_without_error():
    problems = check_ohr_preflight()
    if problems:
        pytest.skip(f"ohr not available: {'; '.join(problems)}")

    proc = start_ohr_server()
    try:
        transcriber = Transcriber()
        line = transcriber.transcribe_chunk(FIXTURE, elapsed_seconds=0)
    finally:
        stop_ohr_server(proc)

    # Silence should produce no transcribable speech.
    assert line == ""
