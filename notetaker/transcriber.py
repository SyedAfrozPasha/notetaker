import difflib
import re
import wave
from pathlib import Path

import numpy as np


def WhisperModel(*args, **kwargs):  # noqa: N802 — keeps the class name as the (patchable) entry point
    """Lazy `faster_whisper.WhisperModel`: importing faster-whisper pulls in
    ctranslate2 and av, which takes seconds on a cold start, and only the
    Recorder process and `notetaker init` ever load a model."""
    from faster_whisper import WhisperModel as _WhisperModel

    return _WhisperModel(*args, **kwargs)


# Channel layout of a stereo chunk written by `recorder.LiveCapture`.
ME_CHANNEL = 0  # your microphone
OTHERS_CHANNEL = 1  # meeting audio via BlackHole
SPEAKER_LABELS = {ME_CHANNEL: "Me", OTHERS_CHANNEL: "Others"}
ECHO_WINDOW_SECONDS = 4.0  # a mic echo of the speakers lands within this of the tapped original
ECHO_SIMILARITY = 0.6  # difflib ratio above which a Me segment is treated as an echo of an Others one
ECHO_CONTAINMENT = 0.8  # or: this share of the Me text appears verbatim inside the Others text
ECHO_MIN_CHARS = 12  # ignore containment for very short fragments ("yes", "okay") — too easy to match
ECHO_VERBATIM_MIN_CHARS = 6  # but a fragment this long that appears verbatim in the Others text is an echo tail


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def append_transcript_line(transcript_path: Path, line: str) -> None:
    with open(transcript_path, "a") as f:
        f.write(line + "\n")


def read_wav_channels(wav_path: Path) -> list[np.ndarray] | None:
    """Reads a 16-bit WAV into one float32 array per channel (what
    faster-whisper accepts directly). Returns None if the file is not a
    readable WAV, so callers can fall back to handing the path to Whisper.
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


class Transcriber:
    def __init__(
        self,
        model_size: str,
        device: str = "cpu",
        compute_type: str = "int8",
        _model=None,
        model_path: str | None = None,
    ):
        """`model_path` loads a faster-whisper model from a local directory
        (no network) — for machines that cannot reach Hugging Face."""
        self._previous_others: list[tuple[float, str]] = []  # (absolute start, text) from the last chunk
        if _model is not None:
            self._model = _model
        elif model_path:
            self._model = WhisperModel(model_path, device=device, compute_type=compute_type, local_files_only=True)
        else:
            self._model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def _segments(self, audio):
        # language="en" avoids faster-whisper's language auto-detection, which
        # crashes (ValueError: max() arg is an empty sequence) when vad_filter
        # strips a fully-silent chunk down to zero audio frames. Meetings this
        # tool targets are English (the default whisper_model is "base.en").
        segments, _ = self._model.transcribe(audio, vad_filter=True, language="en")
        return list(segments)

    def transcribe_chunk(self, wav_path: Path, elapsed_seconds: float) -> str:
        """Returns the chunk's transcript lines ("" if silent). A mono chunk
        gives one `[hh:mm:ss] text` line. A stereo chunk (Me / Others) is
        transcribed per channel and merged in time order into
        `[hh:mm:ss] Me: ...` / `[hh:mm:ss] Others: ...` lines.
        """
        channels = read_wav_channels(wav_path)
        if channels is None or len(channels) == 1:
            audio = str(wav_path) if channels is None else channels[0]
            text = " ".join(seg.text.strip() for seg in self._segments(audio)).strip()
            if not text:
                return ""
            return f"[{format_timestamp(elapsed_seconds)}] {text}"

        spoken: list[tuple[float, str, str]] = []
        for index, audio in enumerate(channels[:2]):
            label = SPEAKER_LABELS[index]
            for seg in self._segments(audio):
                text = seg.text.strip()
                if text:
                    spoken.append((float(getattr(seg, "start", 0.0)), label, text))
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
