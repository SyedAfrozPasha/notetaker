import difflib
import io
import json
import platform
import re
import shutil
import urllib.error
import urllib.request
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Channel layout of a stereo chunk written by `recorder.LiveCapture`.
ME_CHANNEL = 0  # your microphone
OTHERS_CHANNEL = 1  # meeting audio via the Core Audio tap
SPEAKER_LABELS = {ME_CHANNEL: "Me", OTHERS_CHANNEL: "Others"}
ECHO_WINDOW_SECONDS = 4.0  # a mic echo of the speakers lands within this of the tapped original
ECHO_SIMILARITY = 0.6  # difflib ratio above which a Me segment is treated as an echo of an Others one
ECHO_CONTAINMENT = 0.8  # or: this share of the Me text appears verbatim inside the Others text
ECHO_MIN_CHARS = 12  # ignore containment for very short fragments ("yes", "okay") — too easy to match
ECHO_VERBATIM_MIN_CHARS = 6  # but a fragment this long that appears verbatim in the Others text is an echo tail

# ohr's own `--serve` default (11434) collides with apfel's — see ADR 0005. The
# recorder spawns its own ohr server on this fixed port instead; not configurable.
OHR_PORT = 11435
OHR_BASE_URL = f"http://127.0.0.1:{OHR_PORT}/v1"
# ohr's health check lives at the server root, not under /v1 like its other
# endpoints (confirmed against a real `ohr --serve`: /health -> 200, /v1/health -> 404).
OHR_HEALTH_URL = f"http://127.0.0.1:{OHR_PORT}/health"


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def append_transcript_line(transcript_path: Path, line: str) -> None:
    with open(transcript_path, "a") as f:
        f.write(line + "\n")


def read_wav_channels(wav_path: Path) -> list[np.ndarray] | None:
    """Reads a 16-bit WAV into one float32 array per channel. Returns None
    if the file is not a readable WAV, so callers can fall back to handing
    the path to ohr as-is.
    """
    try:
        with wave.open(str(wav_path), "rb") as wf:
            if wf.getsampwidth() != 2:
                return None
            channels = wf.getnchannels()
            raw = wf.readframes(wf.getnframes())
    except (wave.Error, EOFError, OSError):
        return None
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels == 1:
        return [samples]
    samples = samples.reshape(-1, channels)
    return [np.ascontiguousarray(samples[:, i]) for i in range(channels)]


def _wav_framerate(wav_path: Path) -> int:
    with wave.open(str(wav_path), "rb") as wf:
        return wf.getframerate()


def _channel_wav_bytes(samples: np.ndarray, framerate: int) -> bytes:
    """Encodes one channel's float32 [-1, 1] samples back to a standalone
    16-bit mono WAV file in memory, for upload to ohr (which has no
    diarization — each channel must be sent as its own file)."""
    pcm = np.clip(samples * 32768.0, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def drop_echoes(
    spoken: list[tuple[float, str, str]],
    previous_others: list[tuple[float, str]] | None = None,
) -> list[tuple[float, str, str]]:
    """Removes Me segments that are echoes of Others segments: without
    headphones the microphone also hears the speakers, so everything the
    meeting says shows up a second time, slightly garbled, under Me. A Me
    segment within ECHO_WINDOW_SECONDS of an Others segment whose text is at
    least ECHO_SIMILARITY similar is dropped. Real overlap (you talking over
    someone) survives because the words differ. `previous_others` carries
    the last chunk's Others segments (start relative to this chunk, so
    negative) so an echo straddling a chunk boundary is caught too.
    """
    others = [(start, _normalize(text)) for start, label, text in spoken if label == "Others"]
    others += [(start, _normalize(text)) for start, text in (previous_others or [])]
    kept = []
    for start, label, text in spoken:
        if label == "Me":
            mine = _normalize(text)
            echo = any(
                abs(start - other_start) <= ECHO_WINDOW_SECONDS and _is_echo(mine, other_text)
                for other_start, other_text in others
            )
            if echo:
                continue
        kept.append((start, label, text))
    return kept


def _is_echo(mine: str, other: str) -> bool:
    if not mine or not other:
        return False
    matcher = difflib.SequenceMatcher(None, mine, other)
    if matcher.ratio() >= ECHO_SIMILARITY:
        return True
    # The mic catching only the tail of a sentence ("the spot"): too short for
    # a similarity score, but present word-for-word in the tapped original.
    if len(mine) >= ECHO_VERBATIM_MIN_CHARS and mine in other:
        return True
    # A short mic fragment of a long tapped sentence: whole-string similarity
    # is low, but most of the fragment appears verbatim in the original.
    if len(mine) >= ECHO_MIN_CHARS:
        longest = matcher.find_longest_match(0, len(mine), 0, len(other)).size
        return longest / len(mine) >= ECHO_CONTAINMENT
    return False


@dataclass
class Segment:
    start: float
    text: str


class OhrError(Exception):
    pass


class OhrClient:
    """Talks to a locally spawned `ohr --serve` — Apple's on-device
    SpeechAnalyzer wrapped in an OpenAI-compatible HTTP server. See ADR 0005."""

    def __init__(self, base_url: str = OHR_BASE_URL):
        self._base_url = base_url

    def transcribe(self, wav_bytes: bytes, filename: str = "chunk.wav") -> list[Segment]:
        boundary = uuid.uuid4().hex
        body = _multipart_body(boundary, wav_bytes, filename)
        request = urllib.request.Request(
            f"{self._base_url}/audio/transcriptions",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise OhrError(f"ohr at {self._base_url} returned HTTP {exc.code}: {exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OhrError(f"Could not reach ohr at {self._base_url}: {exc}") from exc
        except ValueError as exc:
            raise OhrError(f"ohr at {self._base_url} returned a non-JSON response: {exc}") from exc
        return [Segment(start=float(s.get("start", 0.0)), text=str(s.get("text", ""))) for s in data.get("segments", [])]


def _multipart_body(boundary: str, wav_bytes: bytes, filename: str) -> bytes:
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="response_format"\r\n\r\nverbose_json\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\nen-US\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n".encode() + wav_bytes + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts)


def check_ohr_preflight() -> list[str]:
    """Whether this machine can run ohr at all — macOS 26+ and the `ohr`
    binary on PATH. Does not check a running server: the recorder spawns and
    tears down its own `ohr --serve` per session (see recorder.py)."""
    problems: list[str] = []
    if platform.system() != "Darwin":
        problems.append("SpeechAnalyzer transcription requires macOS.")
        return problems

    major = int(platform.mac_ver()[0].split(".")[0] or 0)
    if major < 26:
        problems.append(f"SpeechAnalyzer transcription requires macOS 26+ (found {platform.mac_ver()[0]}).")

    if shutil.which("ohr") is None:
        problems.append(
            "ohr is not installed. Run: brew tap Arthur-Ficial/tap && brew install Arthur-Ficial/tap/ohr"
        )

    return problems


class Transcriber:
    def __init__(self, base_url: str = OHR_BASE_URL, _client=None):
        self._previous_others: list[tuple[float, str]] = []  # (absolute start, text) from the last chunk
        self._client = _client if _client is not None else OhrClient(base_url)

    def _segments(self, wav_bytes: bytes) -> list[Segment]:
        return self._client.transcribe(wav_bytes)

    def transcribe_chunk(self, wav_path: Path, elapsed_seconds: float) -> str:
        """Returns the chunk's transcript lines ("" if silent). A mono chunk
        gives one `[hh:mm:ss] text` line. A stereo chunk (Me / Others) is
        transcribed per channel and merged in time order into
        `[hh:mm:ss] Me: ...` / `[hh:mm:ss] Others: ...` lines.
        """
        channels = read_wav_channels(wav_path)
        if channels is None or len(channels) == 1:
            wav_bytes = wav_path.read_bytes()
            text = " ".join(seg.text.strip() for seg in self._segments(wav_bytes)).strip()
            if not text:
                return ""
            return f"[{format_timestamp(elapsed_seconds)}] {text}"

        framerate = _wav_framerate(wav_path)
        spoken: list[tuple[float, str, str]] = []
        for index, channel in enumerate(channels[:2]):
            label = SPEAKER_LABELS[index]
            wav_bytes = _channel_wav_bytes(channel, framerate)
            for seg in self._segments(wav_bytes):
                text = seg.text.strip()
                if text:
                    spoken.append((float(seg.start), label, text))
        previous = [(start - elapsed_seconds, text) for start, text in self._previous_others]
        spoken = drop_echoes(spoken, previous_others=previous)
        self._previous_others = [
            (elapsed_seconds + start, text) for start, label, text in spoken if label == "Others"
        ]
        spoken.sort(key=lambda item: item[0])

        lines: list[str] = []
        for start, label, text in spoken:
            if lines and lines[-1][1] == label:
                lines[-1] = (lines[-1][0], label, f"{lines[-1][2]} {text}")
            else:
                lines.append((start, label, text))
        return "\n".join(
            f"[{format_timestamp(elapsed_seconds + start)}] {label}: {text}" for start, label, text in lines
        )
