import os
import queue
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from enum import Enum
from pathlib import Path

import numpy as np
import sounddevice as sd

from notetaker.transcriber import (
    ME_CHANNEL,
    OHR_HEALTH_URL,
    OHR_PORT,
    OTHERS_CHANNEL,
    Transcriber,
    append_transcript_line,
    read_wav_channels,
)

SAMPLE_RATE = 16000  # what SpeechAnalyzer/ohr wants; CoreAudio resamples every device to it
SILENCE_PEAK = 200  # int16 peak below which a channel is treated as silent
SILENT_CHUNKS_BEFORE_WARNING = 3  # BlackHole: a Multi-Output Device carries audio from the first second
SILENT_CHUNKS_BEFORE_WARNING_TAP = 18  # tap: nothing arrives until an app plays, so wait ~3 min before doubting permission
NO_MEETING_AUDIO_WARNING = (
    "[warning] no meeting audio detected — macOS sound output is probably not the "
    "Multi-Output Device that includes BlackHole. Fix it in System Settings > Sound > Output."
)
NO_MEETING_AUDIO_WARNING_TAP = (
    "[warning] no meeting audio detected — make sure the meeting app is playing sound, and that notetaker "
    "is allowed under System Settings > Privacy & Security > Screen & System Audio Recording."
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
    """Continuously captures the meeting audio and, optionally, your
    microphone into one stereo WAV per chunk: left = Me, right = Others.
    Both streams stay open for the whole session — reopening a Bluetooth
    headset every chunk takes ~1s and drops audio.

    The first source (mic when enabled, else meeting audio) is the clock:
    a chunk is cut when it has delivered `chunk_seconds` of samples. Any
    other source contributes what it has delivered by then and is padded
    with silence — a process tap delivers nothing until some app has played
    audio, and padding keeps the channels aligned instead of stalling.
    A source that delivers nothing for `stall_seconds` is reported once via
    `on_stall(source_name)` (e.g. a Bluetooth headset that disconnected).
    """

    STALL_SECONDS = 5.0

    def __init__(
        self,
        system_device: int,
        mic_device: int | str | None,
        sample_rate: int = SAMPLE_RATE,
        on_stall=None,
    ):
        self.sample_rate = sample_rate
        self.with_mic = mic_device is not None
        self._on_stall = on_stall
        self._stalled: set[str] = set()
        self._streams: list[sd.InputStream] = []
        self._queues: list[queue.Queue] = []
        self._leftovers: list[np.ndarray] = []
        self._names: list[str] = []
        self._first_block_seen: list[bool] = []
        # Both sources are aligned to this instant: a source's first block is
        # preceded by as much silence as elapsed before it arrived. A tap
        # delivers nothing until an app plays, so without this its audio
        # would start at chunk position 0 while the mic's copy of the same
        # words sits seconds later — and the echo filter would never match.
        self._started_at = time.monotonic()
        sources = [("microphone", mic_device), ("meeting audio", system_device)] if self.with_mic else [
            ("meeting audio", system_device)
        ]
        for index, (name, device) in enumerate(sources):
            q: queue.Queue = queue.Queue()

            def _on_audio(indata, frames, time_info, status, q=q, index=index):
                block = indata[:, 0].copy()
                if not self._first_block_seen[index]:
                    self._first_block_seen[index] = True
                    lead_in = int((time.monotonic() - self._started_at) * self.sample_rate) - len(block)
                    if lead_in > 0:
                        q.put(np.zeros(lead_in, dtype=np.int16))
                q.put(block)

            self._queues.append(q)
            self._leftovers.append(np.zeros(0, dtype=np.int16))
            self._names.append(name)
            self._first_block_seen.append(False)
            stream = sd.InputStream(
                device=None if device == "default" else device,
                channels=1,
                samplerate=sample_rate,
                dtype="int16",
                callback=_on_audio,
            )
            stream.start()
            self._streams.append(stream)

    def _read(self, source: int, frames: int, timeout: float) -> np.ndarray:
        """Up to `frames` samples from `source`, waiting at most `timeout`
        seconds for more to arrive; the caller pads whatever is missing."""
        buffer = self._leftovers[source]
        deadline = time.monotonic() + timeout
        while len(buffer) < frames:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                buffer = np.concatenate([buffer, self._queues[source].get(timeout=remaining)])
            except queue.Empty:
                break
        self._leftovers[source] = buffer[frames:]
        return buffer[:frames]

    def _note_stall(self, source: int, got: int, wanted: int) -> None:
        name = self._names[source]
        if got == 0 and name not in self._stalled:
            self._stalled.add(name)
            if self._on_stall:
                self._on_stall(name)
        elif got == wanted:
            self._stalled.discard(name)

    def capture_chunk(self, duration_seconds: int, out_path: Path) -> None:
        frames = int(duration_seconds * self.sample_rate)
        columns = []
        for source in range(len(self._queues)):
            # The clock source waits for a full chunk (plus a stall margin);
            # the others get a short grace period and are then padded.
            timeout = duration_seconds + self.STALL_SECONDS if source == 0 else 0.5
            data = self._read(source, frames, timeout)
            self._note_stall(source, len(data), frames)
            if len(data) < frames:
                data = np.concatenate([data, np.zeros(frames - len(data), dtype=np.int16)])
            columns.append(data)
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


def portaudio_device_index(name: str, timeout_seconds: float = 5.0) -> int:
    """Index of the input device called `name` after re-enumerating —
    PortAudio caches the device list at init, so a device created later
    (the tap's aggregate) is invisible until re-initialized. Core Audio
    publishes a new device asynchronously (~0.5s), so poll briefly."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        sd._terminate()
        sd._initialize()
        for index, device in enumerate(sd.query_devices()):
            if device.get("name") == name and device.get("max_input_channels", 0) > 0:
                return index
        if time.monotonic() >= deadline:
            raise RuntimeError(f"audio device '{name}' not found after creating it")
        time.sleep(0.25)


def _channel_peaks(wav_path: Path) -> list[int] | None:
    channels = read_wav_channels(wav_path)
    if not channels:
        return None
    return [int(np.abs(c).max() * 32768) if len(c) else 0 for c in channels]


def start_ohr_server(timeout_seconds: float = 15.0) -> subprocess.Popen:
    """Spawns `ohr --serve` on OHR_PORT (see transcriber.OHR_PORT for why a
    non-default port — apfel already uses ohr's own default) and waits for
    it to report healthy before returning, so the Transcriber's first
    request never races the server's startup. Scoped to this session: there
    is no brew-services-managed ohr process (its formula has no service
    block), so the recorder owns spawning and tearing it down."""
    proc = subprocess.Popen(
        ["ohr", "--serve", "--port", str(OHR_PORT), "--host", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + timeout_seconds
    while True:
        if proc.poll() is not None:
            raise RuntimeError(f"ohr --serve exited immediately (code {proc.returncode})")
        try:
            with urllib.request.urlopen(OHR_HEALTH_URL, timeout=1):
                return proc
        except urllib.error.URLError:
            pass
        if time.monotonic() >= deadline:
            proc.terminate()
            raise RuntimeError(f"ohr --serve did not become healthy within {timeout_seconds}s")
        time.sleep(0.2)


def stop_ohr_server(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_recorder(
    session_dir: Path,
    transcriber: Transcriber,
    capture_fn,
    chunk_seconds: int = 10,
    no_meeting_audio_warning: str = NO_MEETING_AUDIO_WARNING,
    silent_chunks_before_warning: int = SILENT_CHUNKS_BEFORE_WARNING,
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
                if silent_meeting_chunks >= silent_chunks_before_warning:
                    pending.put(no_meeting_audio_warning)
                    routing_warned = True
            pending.put((chunk_path, elapsed, index))
            elapsed += chunk_seconds
            index += 1
    finally:
        pending.put(None)
        worker.join()


def _parse_args(argv: list[str]):
    import argparse

    parser = argparse.ArgumentParser(prog="notetaker.recorder")
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--mic", default="default", help="'default' (macOS default input) or 'none'")
    parser.add_argument("--system-audio", choices=["tap", "blackhole"], default="tap")
    parser.add_argument("--system-device", type=int, default=None, help="BlackHole device index (blackhole mode)")
    parser.add_argument("--tap-process", default="", help="bundle id to tap exclusively (tap mode)")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    from notetaker.systemaudio import create_system_audio_tap

    args = _parse_args(argv)
    session_dir = Path(args.session_dir)
    transcript_path = session_dir / "transcript.txt"
    mic = None if args.mic == "none" else args.mic

    # Spawned before the tap/capture so a failure to start leaves nothing else to tear down.
    ohr_proc = start_ohr_server()

    tap = None
    if args.system_audio == "tap":
        tap = create_system_audio_tap(args.tap_process or None)
        if args.tap_process and tap.fell_back_to_global:
            append_transcript_line(
                transcript_path,
                f"[note] {args.tap_process} is not running — capturing all system audio instead.",
            )
        system_device = portaudio_device_index(tap.device_name)
        warning = NO_MEETING_AUDIO_WARNING_TAP
        silent_chunks = SILENT_CHUNKS_BEFORE_WARNING_TAP
    else:
        if args.system_device is None:
            raise SystemExit("--system-device is required with --system-audio blackhole")
        system_device = args.system_device
        warning = NO_MEETING_AUDIO_WARNING
        silent_chunks = SILENT_CHUNKS_BEFORE_WARNING

    def _on_stall(source_name: str) -> None:
        if source_name == "microphone":  # meeting audio is legitimately quiet until someone speaks
            append_transcript_line(
                transcript_path,
                "[warning] microphone stopped delivering audio (headset disconnected?) — your voice is not "
                "being recorded. Reconnect it, then stop and start the recording.",
            )

    capture = LiveCapture(system_device, mic, on_stall=_on_stall)
    exit_code = 0
    try:
        run_recorder(
            session_dir,
            Transcriber(),
            capture.capture_chunk,
            no_meeting_audio_warning=warning,
            silent_chunks_before_warning=silent_chunks,
        )
    except BaseException:
        import traceback

        traceback.print_exc()
        exit_code = 1
    finally:
        stop_ohr_server(ohr_proc)
        if tap is not None:
            # Destroy the tap's devices while the PortAudio streams are still
            # running, then leave without stopping them: Pa_StopStream on the
            # tap aggregate can deadlock inside CoreAudio (observed: the
            # aggregate's IO thread holds the HAL mutex while parked waiting
            # for a cycle). The transcript is complete by now — run_recorder
            # joins its transcription worker — and a private aggregate must
            # be destroyed explicitly or it outlives the process.
            tap.close()
            _hard_exit(exit_code)
        else:
            capture.close()
    return exit_code


def _hard_exit(code: int) -> None:
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
