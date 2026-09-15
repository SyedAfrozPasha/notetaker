# Notetaker UI Design

Design spec for adding a menu bar app and local web dashboard on top of the existing `notetaker` CLI. Captured from a grilling session on 2026-09-12; see `CONTEXT.md` and `docs/adr/0003-brew-services-for-ui-processes-no-app-packaging.md` for the domain vocabulary and packaging decision this spec depends on.

## Goal

Two additional UI surfaces, both operating on the same `~/.notetaker/` filesystem state the CLI already uses — the CLI itself is unchanged in behavior and remains fully supported.

- **Menu bar app** (`rumps`): Start/Stop, live in-progress + elapsed-time indicator, native notifications on save-complete, on summarization failure, and on a detected crash.
- **Local web dashboard** (FastAPI + Jinja2 + htmx, bound to `127.0.0.1` only): create sessions, live status + rolling transcript preview (polled every ~2-3s), search/filter notes (text + tag + date range), view a note (transcript read-only), edit title/tags/summary via structured fields, copy buttons for Summary and full-note-as-Markdown, delete a note (with confirmation) or cancel an in-progress session, resummarize a note, edit non-secret config, and manage the Claude API key (Keychain-backed, masked display, validate-on-save).

## Architecture

- **Shared service layer**: all operational logic (start/stop/cancel/delete/resummarize/salvage/status) is extracted from `cli.py` into a plain importable module so the CLI, menu bar app, and dashboard backend all call the same implementation instead of three copies drifting apart.
- **Process model**: the menu bar app and dashboard are separate long-running processes from the CLI's detached recorder; both poll the same filesystem state independently (no daemon/socket — extends the existing PID-file design, see the original CLI design spec's "Approach A vs B vs C").
- **Distribution**: core CLI install stays git-clone + `install.sh` + pip/venv. The menu bar app and dashboard run via `brew services` (a local/personal Homebrew formula) rather than manual subcommands or hand-rolled LaunchAgents — see ADR 0003 for why (corporate MacBook: no App Store, no direct website installers, brew is sanctioned).
- **Summarization stays synchronous**: `stop` (and resummarize) block until the Provider call returns, matching today's CLI behavior — no async job/polling state machine.

## Session lifecycle

Three distinct endings for a Session (see `CONTEXT.md` for full definitions — this spec assumes that vocabulary):

- **Stop**: normal end, always produces a Note (even a near-empty one if no audio was captured).
- **Cancel**: discards an in-progress Session's Transcript; no Note is ever created.
- **Salvage**: recovers an Orphaned session (Recorder crashed without a matching `stop`) into a Note automatically, so a crash never silently loses the meeting record. Detection is by active polling from the menu bar/dashboard (piggybacking on their existing status-poll) — not deferred to the next `start`. Lives in the shared service layer so CLI-only users benefit too.

A Note's Transcript persists as its own file alongside the Note (no longer deleted when the Session ends), so a Note can be Resummarized later without re-transcribing.

## Feature scope by surface

**CLI** (existing commands, `init`/`start`/`stop`/`list`/`show`, unchanged behavior): gets `rich`-based spinners — "Starting recorder..." during `start`, and distinct "Stopping recorder..." / "Summarizing..." / "Saved." phases during `stop`, since the summarization phase's duration is unpredictable (depends on the Provider).

**Menu bar app**: intentionally minimal — Start (auto-generates a timestamp title, editable later in the dashboard), Stop, live elapsed-time + in-progress indicator, native notifications (save-complete, summarization-failure, crash-detected).

**Dashboard**: full management surface.
- Create a session (same as `start`, optional title/tags up front — no scheduling).
- Live view of an in-progress session: elapsed time + rolling transcript preview (polled, not pushed — transcript chunks land every ~10s regardless, per the existing chunked-transcription design, so push buys nothing).
- Browse/search notes: text search (title + summary) + tag filter + date-range filter.
- View a note: Summary, Action Items (read-only checkboxes, not interactive — a later nice-to-have), Transcript (read-only).
- Edit a note: structured fields for title, tags, summary/action-items text — re-serialized into the existing Markdown format. Transcript is never editable.
- Delete a note (file + its transcript sidecar) — requires confirmation, irreversible.
- Cancel an in-progress session — distinct action from Delete (see `CONTEXT.md`).
- Resummarize a note on demand (covers both retrying a failure and redoing a successful summary), using the persisted transcript sidecar.
- Copy buttons: Summary text, and full note as Markdown.
- Config editing: notes directory, provider choice, whisper model. Explicitly NOT the API key path via env var — kept separate from the Keychain-backed credential flow below.
- Provider credential management: view (masked, e.g. `sk-ant-••••1234`) and set the Claude API key, stored via `keyring` in the macOS Keychain — never in `config.yaml` or any plaintext file. Saving validates the key with a live API check before accepting it.

## Explicitly out of scope (for this round of work)

- Interactive action-item checkboxes (toggle done/undone in place).
- Auto-start-at-login beyond what `brew services` already provides.
- Public Homebrew tap or distributing this tool to other users.
- Any UI capability to bypass the env-var path for non-Claude provider settings that aren't secrets.
