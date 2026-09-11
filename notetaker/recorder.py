import signal
import subprocess
import sys
import wave
from enum import Enum
from pathlib import Path

import sounddevice as sd

from notetaker.transcriber import Transcriber, append_transcript_line


class BlackHoleStatus(Enum):
    NOT_INSTALLED = "not_installed"
    INSTALLED_NOT_ACTIVE = "installed_not_active"
    ACTIVE = "active"


def find_blackhole_device_index() -> int | None:
    for idx, device in enumerate(sd.query_devices()):
        if "BlackHole" in device.get("name", "") and device.get("max_input_channels", 0) > 0:
            return idx
    return None


def check_blackhole() -> BlackHoleStatus:
    if find_blackhole_device_index() is not None:
        return BlackHoleStatus.ACTIVE
    result = subprocess.run(["brew", "list", "blackhole-2ch"], capture_output=True)
    if result.returncode == 0:
        return BlackHoleStatus.INSTALLED_NOT_ACTIVE
    return BlackHoleStatus.NOT_INSTALLED


def capture_chunk(device_index: int, duration_seconds: int, out_path: Path, sample_rate: int = 16000) -> None:
    frames = sd.rec(
        int(duration_seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        device=device_index,
    )
    sd.wait()
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames.tobytes())


def run_recorder(
    session_dir: Path,
    device_index: int,
    transcriber: Transcriber,
    chunk_seconds: int = 10,
    capture_fn=capture_chunk,
) -> None:
    stop_flag = {"stop": False}

    def handle_sigterm(signum, frame):
        stop_flag["stop"] = True

    signal.signal(signal.SIGTERM, handle_sigterm)

    transcript_path = session_dir / "transcript.txt"
    chunks_dir = session_dir / "chunks"
    chunks_dir.mkdir(exist_ok=True)

    elapsed = 0.0
    index = 0
    while not stop_flag["stop"]:
        chunk_path = chunks_dir / f"chunk_{index:05d}.wav"
        capture_fn(device_index, chunk_seconds, chunk_path)
        line = transcriber.transcribe_chunk(chunk_path, elapsed)
        if line:
            append_transcript_line(transcript_path, line)
        elapsed += chunk_seconds
        index += 1


if __name__ == "__main__":
    _session_dir = Path(sys.argv[1])
    _device_index = int(sys.argv[2])
    _model_size = sys.argv[3]
    run_recorder(_session_dir, _device_index, Transcriber(_model_size))
