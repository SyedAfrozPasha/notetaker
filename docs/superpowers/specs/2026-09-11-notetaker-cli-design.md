# Notetaker CLI — Design Spec

Date: 2026-09-11

## Purpose

A macOS CLI tool that records the audio of an ongoing Microsoft Teams
meeting, transcribes it live using a local Whisper model, and uses a
pluggable AI provider to summarize the transcript into a structured
Markdown note (summary, action items, tags). Installed by cloning the
git repo and running a setup script — no package registry required.

## Non-goals (MVP)

- Not cross-platform: macOS only (system audio loopback capture is
  platform-specific; Windows/Linux are future work, not this spec).
- Not a note-taking GUI or web app — CLI only.
- No full-text search command (`list`/`show` only for MVP).
- No plugin-loading system for AI providers — just a clean interface
  with one implementation (Claude), so a second provider can be added
  later without restructuring.
- No true overlap-and-merge streaming transcription (see Approach A
  vs B below) — chunked polling is good enough for MVP.

## Architecture

A single Python CLI package (`notetaker`) with two run modes:

- A short-lived foreground process for user commands (`init`,
  `start`, `stop`, `list`, `show`).
- A detached background recorder process spawned by `start`.

The two coordinate entirely through the filesystem under
`~/.notetaker/` — a PID file, a per-session working directory, and a
config file. No daemon socket, no database server. This keeps the
system simple to install, debug (everything is inspectable files),
and reason about.

### Why chunked polling, not true streaming (Approach A)

Three approaches were considered for the live-transcription core:

- **A — Chunked polling (chosen).** Record system audio into rolling
  ~10s WAV chunks; transcribe each chunk as it completes with
  `faster-whisper`; append timestamped lines to a running transcript.
  "Live" means transcript segments appear every ~10s, not
  word-by-word. Simple, uses well-tested libraries.
- **B — Overlap-and-merge streaming.** Sliding overlapping windows
  (e.g. 10s window every 5s) with text-diffing to stitch overlapping
  transcriptions into one corrected stream. More accurate at chunk
  boundaries, but the overlap-deduplication logic is a real source of
  subtle bugs and meaningfully more complex than this MVP needs.
- **C — Socket/RPC daemon.** Same recording approach as A, but the
  background process is a proper daemon the CLI talks to over a Unix
  socket instead of PID file + signals. More extensible (e.g. live
  status queries) but more moving parts than the MVP's command set
  (`start`/`stop`) requires.

Approach A was chosen: it satisfies "live" well enough for the use
case while keeping process lifecycle management (PID file + signals)
as simple as possible. B or C can be adopted later if transcript
quality at chunk boundaries or richer live-status features become
important.

## Components

- `cli.py` — argument parsing (Typer) and command dispatch.
- `recorder.py` — the background process. Opens the BlackHole loopback
  input via `sounddevice`, writes rolling ~10s WAV chunks to a session
  temp directory, and hands each chunk to the transcriber. Runs until
  it receives SIGTERM, then flushes and exits.
- `transcriber.py` — wraps `faster-whisper`. Loads the model once,
  transcribes each chunk as it arrives, appends timestamped lines to
  `transcript.txt` in the session directory.
- `summarizer.py` — the pluggable AI layer. Defines a minimal
  `Provider` interface (`summarize(transcript) -> Summary`, where
  `Summary` = `{text, action_items, tags}`) with a `ClaudeProvider`
  implementation that calls the Anthropic Messages API with a fixed
  prompt template requesting structured JSON output.
- `notes.py` — turns a finished session (transcript + summary) into a
  Markdown note file in the notes directory; also backs `list` and
  `show` by parsing note frontmatter.
- `config.py` — loads and validates `~/.notetaker/config.yaml`.

## Data Flow

1. `notetaker start "Standup"` — CLI checks no session is already
   running (PID file + liveness check), verifies the BlackHole device
   exists, then forks `recorder.py` as a detached process and writes
   its PID plus session metadata (title, start time, session dir) to
   `~/.notetaker/current_session.json`.
2. Recorder loop: capture ~10s chunk → hand to transcriber → append
   result to `transcript.txt`. Repeats until SIGTERM.
3. `notetaker stop` — CLI reads the PID file, sends SIGTERM, waits for
   the recorder to flush and exit, reads the final `transcript.txt`.
   If the recorder process is already dead (crashed), whatever
   transcript exists is salvaged and used anyway.
4. CLI calls `summarizer.py` with the full transcript, gets back a
   summary, action items, and tags. If this call fails (network/API
   error), the transcript is still saved as a note with the summary
   section replaced by an error note — a failed API call never loses
   the meeting record.
5. `notes.py` writes `<notes_dir>/YYYY-MM-DD-<slug>.md` (frontmatter +
   summary + action items + transcript) and cleans up the session
   directory and PID file.

## Note File Format

One Markdown file per meeting, YAML frontmatter for machine-readable
metadata, Markdown body for humans:

```markdown
---
title: Standup
date: 2026-09-11T10:00:00-07:00
duration_minutes: 18
tags: [project-x, planning]
---

## Summary
...

## Action Items
- [ ] ...

## Transcript
[00:00:03] ...
[00:00:07] ...
```

Frontmatter makes `list` (date/title/tags only) and `show` (full body)
straightforward to implement by parsing frontmatter and optionally
skipping the body.

## Configuration

`~/.notetaker/config.yaml`, created by `notetaker init` with commented
defaults:

```yaml
notes_dir: ~/notetaker-notes
whisper_model: base.en          # tiny/base/small/medium
ai_provider: claude
ai_model: claude-sonnet-5
api_key_env: ANTHROPIC_API_KEY  # read key from this env var; never stored in the file
```

`notetaker init` also checks whether BlackHole is installed
(`brew list blackhole-2ch`) and prints setup instructions if not. It
does not attempt to install the audio driver itself, since that
requires interacting with System Settings.

## AI Provider Abstraction

```python
class Provider(Protocol):
    def summarize(self, transcript: str) -> Summary:
        ...
```

MVP ships one implementation, `ClaudeProvider`. The interface exists
purely as a clean seam — not a plugin-loading system — so a second
provider (e.g. a local Ollama-backed provider) can be added later
without touching `notes.py` or `cli.py`.

## Error Handling

- `start` while a session is already running → error, instruct the
  user to `stop` first.
- `stop` with no active session → clear error, no-op.
- Recorder crashes mid-meeting → `stop` detects the dead PID, salvages
  the partial transcript, and still runs summarization on it.
- BlackHole device not found at `start` → fail fast before spawning
  the background process, pointing at `notetaker init`'s setup check.
- AI summarization call fails → raw transcript is still saved as a
  note; summary section notes the failure instead of the note being
  silently discarded.

## Testing Strategy

- Unit tests for parts that don't need real audio/API calls:
  - `notes.py` — Markdown generation/parsing round-trip.
  - `config.py` — loading/validation.
  - `summarizer.py` — prompt construction and response parsing, with
    the Anthropic client mocked.
- A thin integration test for `recorder.py`/`transcriber.py` using a
  short pre-recorded WAV fixture run through real chunking +
  `faster-whisper` (tiny model), to catch wiring bugs — not to
  validate real-time behavior.
- No end-to-end test requiring an actual Teams call or live BlackHole
  audio; that stays manual/exploratory.

## CLI Reference (MVP)

- `notetaker init` — write default config, check BlackHole install.
- `notetaker start "<title>"` — begin recording + live transcription
  in the background.
- `notetaker stop` — stop recording, summarize, save note.
- `notetaker list` — show recent notes (date, title, tags).
- `notetaker show <id>` — print a note's summary + transcript.

## Install

`git clone`, then `./install.sh`, which creates a virtualenv, runs
`pip install -e .`, and symlinks the `notetaker` entry point onto
`PATH` (e.g. `~/.local/bin`). The README documents the one manual
step this can't automate: installing BlackHole via Homebrew and
setting it up as an input device alongside a multi-output device (so
meeting audio is both captured and still audible to the user).
