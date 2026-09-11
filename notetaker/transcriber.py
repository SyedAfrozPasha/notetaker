from pathlib import Path

from faster_whisper import WhisperModel


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def append_transcript_line(transcript_path: Path, line: str) -> None:
    with open(transcript_path, "a") as f:
        f.write(line + "\n")


class Transcriber:
    def __init__(
        self,
        model_size: str,
        device: str = "cpu",
        compute_type: str = "int8",
        _model=None,
    ):
        self._model = _model if _model is not None else WhisperModel(
            model_size, device=device, compute_type=compute_type
        )

    def transcribe_chunk(self, wav_path: Path, elapsed_seconds: float) -> str:
        # language="en" avoids faster-whisper's language auto-detection, which
        # crashes (ValueError: max() arg is an empty sequence) when vad_filter
        # strips a fully-silent chunk down to zero audio frames. Meetings this
        # tool targets are English (the default whisper_model is "base.en").
        segments, _ = self._model.transcribe(str(wav_path), vad_filter=True, language="en")
        text = " ".join(segment.text.strip() for segment in segments).strip()
        if not text:
            return ""
        return f"[{format_timestamp(elapsed_seconds)}] {text}"
