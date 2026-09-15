---
status: accepted
---

# SpeechAnalyzer via ohr replaces faster-whisper as the sole Transcriber backend

Hugging Face downloads are blocked on the target corporate MacBook's network, breaking faster-whisper's model download path outright. Rather than route around the block (e.g. a pre-downloaded `whisper_model_path`), the Transcriber now runs entirely on Apple's on-device `SpeechAnalyzer`/`SpeechTranscriber` (Speech framework, macOS 26+), reached via [`ohr`](https://github.com/Arthur-Ficial/ohr) — a third-party CLI/server from the same author as `apfel`, run in `--serve` mode as a persistent local OpenAI-compatible HTTP server (`brew services`-managed, same lifecycle as `apfel`) and consumed with a plain `urllib` client. faster-whisper, the `Transcriber` class's Whisper-specific internals, and the `whisper_model`/`whisper_model_path` config keys are removed entirely — there is no second transcription backend and no runtime fallback.

This adds no new platform constraint: `apple_local` is already the default `ai_provider` (ADR 0002), which ADR 0004 notes already pins the project to macOS 26+, so SpeechAnalyzer's own macOS 26+ requirement is free.

`ohr` doesn't document stereo/multi-channel input — everything in its README reads as mono-only — so the Recorder's existing per-channel split (`read_wav_channels`, Me/Others) stays exactly as it is; `ohr` is just called once per channel where Whisper was.

## Considered and rejected

- **Point `whisper_model_path` at a pre-downloaded model instead of switching engines.** Rejected: would work around the immediate HF block, but leaves a one-time manual file-transfer step for every future machine and doesn't remove the dependency the corporate network keeps fighting.
- **Keep faster-whisper as a runtime fallback, auto-selected when SpeechAnalyzer is unavailable.** Rejected: this is a hard constraint (corporate network), not a preference, so a silent dual-path only adds an untested code path (mixed-engine transcripts, two sets of echo-drop tuning) for a case that shouldn't occur in practice.
- **A custom Swift helper binary calling `SpeechAnalyzer` directly.** Rejected: `SpeechAnalyzer`/`SpeechTranscriber` are Swift `actor`/`async`/`AsyncSequence` APIs not exposed to the Objective-C runtime, so — unlike the PyObjC/ctypes calls `systemaudio.py` already makes — there's no direct Python binding. Building and maintaining a Swift toolchain just for this would mean owning Speech-framework version churn in a project that's otherwise pure Python. `ohr` already solves this and follows the `apfel` precedent this codebase already accepted.
- **`mac-speech-analyzer` (PyPI).** Rejected: batch-only (no live/incremental transcription — listed as roadmap, not shipped), and its PyPI listing has no linked source repository at all, so its Swift/C bridge can't be independently audited before trusting it as a dependency.

## Consequences

- No config override exists to force a different transcription engine, and no fallback-visibility warning is needed — there's only one path.
- `ohr` is pre-1.0 (v0.1.6 at the time of this decision) and undocumented on channel handling; if it turns out to mishandle mono splitting in practice, that's a bug to work around in `ohr` or file upstream, not a signal to reintroduce a second backend.
- The lost real-model faster-whisper integration test is replaced with an equivalent real-`ohr`-server integration test (`@pytest.mark.integration`, skipped when `ohr` isn't reachable), so real-model transcription coverage isn't silently dropped.
