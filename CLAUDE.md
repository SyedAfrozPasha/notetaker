# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Implemented and tested — see `notetaker/` for the code and `tests/` for the suite (75 tests, including one real-model integration test). The original design spec ([docs/superpowers/specs/2026-09-11-notetaker-cli-design.md](docs/superpowers/specs/2026-09-11-notetaker-cli-design.md)) is still useful background on the "Approach A vs B vs C" rationale, but the code and this file are now the source of truth — keep the architecture summary below in sync with the code, not the spec, when either changes. `CONTEXT.md` and `docs/adr/` capture domain terms and key decisions made during implementation.

## What this project is

A macOS CLI tool (`notetaker`) that:
- Records the system audio of an ongoing Microsoft Teams meeting via a loopback device (BlackHole).
- Transcribes it live using a local `faster-whisper` model, in ~10s rolling chunks (not true word-by-word streaming — see the spec's "Approach A" rationale).
- Sends the finished transcript to a pluggable AI provider (Claude by default) to produce a summary, action items, and tags.
- Saves each meeting as a single Markdown file with YAML frontmatter under a configurable notes directory.

Installed by cloning the repo and running a setup script (`./install.sh`) — no package registry involved.

## Planned architecture (from the spec)

Two run modes coordinating entirely through the filesystem under `~/.notetaker/` (a PID file, a per-session working directory, and a config file) — no daemon socket, no database:

- `cli.py` — command dispatch (Typer) for `init`, `start`, `stop`, `list`, `show`, `resummarize`, `set-api-key`, `show-api-key`, `menubar`. `start`/`stop` show a `rich` loading spinner, since both can block for an unpredictable duration (spawning the recorder; the summarization API call).
- `menubar.py` — a minimal `rumps`-based menu bar app: one-click Start/Stop, live elapsed-time display, and native notifications on save-complete, summarization-failure, and crash-detected. Polls `service.check_and_salvage_orphan`/`get_current_session_status` every 2 seconds — the first UI surface besides the CLI to call these on its own cadence, which is why `check_and_salvage_orphan` now claims the session file atomically before salvaging.
- `service.py` — all operational logic (start/stop/list/show/init/cancel), used by `cli.py` and (from here on) every other UI surface. Raises `ServiceError` for user-facing failures; never prints anything itself — `cli.py` is a thin adapter that translates its results into `typer.echo` calls and exit codes. Detects and salvages an Orphaned session (a crashed Recorder from a previous run) via `check_and_salvage_orphan`, called before every `start`; `cancel_session` discards an in-progress Session without producing a Note. Every `stop`/salvage also writes a `<note-id>.transcript.txt` sidecar next to the note, preserving the raw transcript for future resummarization.
- `credentials.py` — provider-agnostic Keychain read/write/masking (`get_provider_credential`, `set_provider_credential`, `mask_credential`). Never stores a Provider credential in `config.yaml` or any plaintext file. `get_provider`/`check_setup` check the Keychain first, falling back to the credential's environment variable for backward compatibility.
- `recorder.py` — background process (spawned by `start`, killed via SIGTERM by `stop`) that captures the BlackHole input into rolling WAV chunks.
- `transcriber.py` — wraps `faster-whisper`; transcribes each chunk and appends timestamped lines to a running transcript file.
- `summarizer.py` — defines the `Provider` interface (`summarize(transcript) -> Summary`) with a `ClaudeProvider` implementation; this is the seam for adding other AI backends later.
- `notes.py` — converts a finished session (transcript + summary) into a Markdown note, and backs `list`/`show` by parsing note frontmatter.
- `config.py` — loads/validates `~/.notetaker/config.yaml`.

Key design decisions worth knowing before changing this system:
- Chunked polling was chosen over true overlap-merge streaming transcription for simplicity; see the spec's "Approach A vs B vs C" section before changing the recording/transcription model.
- A crashed recorder or a failed AI summarization call must never lose the meeting record — partial transcripts are always salvaged and saved, with the summary section noting the failure if summarization fails.
- The AI provider abstraction is intentionally minimal (one interface, one implementation) — it is a clean seam, not a plugin-loading system. Don't build out plugin discovery/config for it until a second provider is actually needed.
- macOS only for the MVP; the audio capture layer is the platform-specific piece if cross-platform support is added later.

## Development

- `./install.sh` — one-time setup (creates `.venv`, installs pinned deps).
- `.venv/bin/pytest -q` — full test suite.
- `.venv/bin/pytest -m "not integration" -q` — fast suite only, skips the network-dependent faster-whisper integration test.
- Use `.venv/bin/python` / `.venv/bin/pytest` explicitly; there is no ambient install.
