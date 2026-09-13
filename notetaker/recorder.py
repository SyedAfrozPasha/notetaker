import queue
import signal
import subprocess
import sys
import threading
import wave
from enum import Enum
from pathlib import Path

import numpy as np
import sounddevice as sd

from notetaker.transcriber import ME_CHANNEL, OTHERS_CHANNEL, Transcriber, append_transcript_line, read_wav_channels

SAMPLE_RATE = 16000  # what Whisper wants; CoreAudio resamples every device to it
SILENCE_PEAK = 200  # int16 peak below which a channel is treated as silent
SILENT_CHUNKS_BEFORE_WARNING = 3
NO_MEETING_AUDIO_WARNING = (
    "[warning] no meeting audio detected — macOS sound output is probably not the "
    "Multi-Output Device that includes BlackHole. Fix it in System Settings > Sound > Output."
)


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
    try:
        result = subprocess.run(["brew", "list", "blackhole-2ch"], capture_output=True)
    except FileNotFoundError:
        return BlackHoleStatus.NOT_INSTALLED
    if result.returncode == 0:
        return BlackHoleStatus.INSTALLED_NOT_ACTIVE
    return BlackHoleStatus.NOT_INSTALLED


def default_input_device_name() -> str | None:
    try:
        return sd.query_devices(kind="input").get("name")
    except Exception:
        return None


def default_output_device_name() -> str | None:
    try:
        return sd.query_devices(kind="output").get("name")
    except Exception:
        return None


def check_output_routing() -> str | None:
    """Returns a warning when the current macOS sound output is not a
    Multi-Output Device — BlackHole only hears audio routed through one, so
    any other output (speakers, headphones picked automatically when you
    plug them in) silently gives an empty transcript.
    """
    name = default_output_device_name()
    if name is None:
        return None
    if "BlackHole" in name:
        return (
            f"macOS sound output is '{name}' itself — you will not hear the meeting. "
            "Select the Multi-Output Device (headphones/speakers + BlackHole) in System Settings > Sound > Output."
        )
    if "Multi-Output" not in name and "Aggregate" not in name:
        return (
            f"macOS sound output is '{name}', not a Multi-Output Device that includes BlackHole — "
            "meeting audio will NOT be captured. Select the Multi-Output Device in System Settings > Sound > Output "
            "(after connecting your headphones)."
        )
    return None


def check_microphone_routing() -> str | None:
    """Returns a warning when the default input device is BlackHole itself,
    which would record the meeting twice and your voice never."""
    name = default_input_device_name()
    if name and "BlackHole" in name:
        return (
            f"macOS sound input is '{name}' — your voice will not be recorded. "
            "Select your microphone or headset in System Settings > Sound > Input."
        )
    return None


class LiveCapture:
    """Continuously captures the meeting (BlackHole) and, optionally, your
    microphone into one stereo WAV per chunk: left = Me, right = Others.
    Both streams stay open for the whole session — reopening a Bluetooth
    headset every chunk takes ~1s and drops audio.
    """

    def __init__(self, system_device: int, mic_device: int | str | None, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.with_mic = mic_device is not None
        self._streams: list[sd.InputStream] = []
        self._queues: list[queue.Queue] = []
        self._leftovers: list[np.ndarray] = []
        sources = [mic_device, system_device] if self.with_mic else [system_device]
        for device in sources:
            q: queue.Queue = queue.Queue()

            def _on_audio(indata, frames, time_info, status, q=q):
                q.put(indata[:, 0].copy())

            stream = sd.InputStream(
                device=None if device == "default" else device,
                channels=1,
                samplerate=sample_rate,
                dtype="int16",
                callback=_on_audio,
            )
            stream.start()
            self._streams.append(stream)
            self._queues.append(q)
            self._leftovers.append(np.zeros(0, dtype=np.int16))

    def _read(self, source: int, frames: int, timeout: float) -> np.ndarray:
        buffer = self._leftovers[source]
        while len(buffer) < frames:
            buffer = np.concatenate([buffer, self._queues[source].get(timeout=timeout)])
        self._leftovers[source] = buffer[frames:]
        return buffer[:frames]

    def capture_chunk(self, duration_seconds: int, out_path: Path) -> None:
        frames = int(duration_seconds * self.sample_rate)
        try:
            columns = [self._read(i, frames, timeout=duration_seconds + 5) for i in range(len(self._queues))]
        except queue.Empty as exc:
            raise RuntimeError("audio device stopped delivering samples (unplugged?)") from exc
        data = np.column_stack(columns) if len(columns) > 1 else columns[0]
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(len(columns))
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(np.ascontiguousarray(data).tobytes())

    def close(self) -> None:
        for stream in self._streams:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass


def _channel_peaks(wav_path: Path) -> list[int] | None:
    channels = read_wav_channels(wav_path)
    if not channels:
        return None
    return [int(np.abs(c).max() * 32768) if len(c) else 0 for c in channels]


def run_recorder(
    session_dir: Path,
    transcriber: Transcriber,
    capture_fn,
    chunk_seconds: int = 10,
) -> None:
    """Captures audio continuously on this (main) thread while a single
    worker thread transcribes finished chunks in order. Capture must never
    wait on Whisper: any second spent transcribing on the capture thread is
    audio that is never recorded.

    `capture_fn(duration_seconds, out_path)` writes one WAV chunk (see
    `LiveCapture.capture_chunk`). If it writes stereo (Me / Others) chunks
    and the Others channel stays silent while Me is not, a routing warning
    is appended to the transcript once — the usual cause is macOS output
    switched away from the Multi-Output Device.
    """
    stop_flag = {"stop": False}

    def handle_sigterm(signum, frame):
        stop_flag["stop"] = True

    signal.signal(signal.SIGTERM, handle_sigterm)

    transcript_path = session_dir / "transcript.txt"
    chunks_dir = session_dir / "chunks"
    chunks_dir.mkdir(exist_ok=True)

    def _safe_append(line: str) -> None:
        try:
            append_transcript_line(transcript_path, line)
        except OSError:
            pass  # session dir already removed by a timed-out stop; nothing to save to

    pending: queue.Queue = queue.Queue()

    def transcribe_worker() -> None:
        while True:
            item = pending.get()
            if item is None:
                return
            if isinstance(item, str):
                _safe_append(item)  # keeps status lines in order with transcript lines
                continue
            chunk_path, offset, index = item
            try:
                line = transcriber.transcribe_chunk(chunk_path, offset)
                if line:
                    _safe_append(line)
            except Exception as exc:
                # A single bad chunk must not end the meeting: note it and keep going.
                _safe_append(f"[transcription failed for chunk {index}: {exc}]")
            finally:
                chunk_path.unlink(missing_ok=True)

    worker = threading.Thread(target=transcribe_worker, daemon=True)
    worker.start()

    elapsed = 0.0
    index = 0
    silent_meeting_chunks = 0
    routing_warned = False
    try:
        while not stop_flag["stop"]:
            chunk_path = chunks_dir / f"chunk_{index:05d}.wav"
            try:
                capture_fn(chunk_seconds, chunk_path)
            except Exception as exc:
                pending.put(f"[recording stopped due to error: {exc}]")
                break
            peaks = _channel_peaks(chunk_path)
            if peaks and len(peaks) >= 2 and not routing_warned:
                if peaks[OTHERS_CHANNEL] < SILENCE_PEAK and peaks[ME_CHANNEL] >= SILENCE_PEAK:
                    silent_meeting_chunks += 1
                else:
                    silent_meeting_chunks = 0
                if silent_meeting_chunks >= SILENT_CHUNKS_BEFORE_WARNING:
                    pending.put(NO_MEETING_AUDIO_WARNING)
                    routing_warned = True
            pending.put((chunk_path, elapsed, index))
            elapsed += chunk_seconds
            index += 1
    finally:
        pending.put(None)
        worker.join()


if __name__ == "__main__":
    # argv: session_dir system_device_index model_size mic_device("default"|"none") model_path("" = download)
    _session_dir = Path(sys.argv[1])
    _system_device = int(sys.argv[2])
    _model_size = sys.argv[3]
    _mic = sys.argv[4] if len(sys.argv) > 4 else "default"
    _model_path = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else None
    _capture = LiveCapture(_system_device, None if _mic == "none" else _mic)
    try:
        run_recorder(_session_dir, Transcriber(_model_size, model_path=_model_path), _capture.capture_chunk)
    finally:
        _capture.close()
