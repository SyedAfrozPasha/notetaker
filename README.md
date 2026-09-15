# notetaker

Records a meeting's system audio (e.g. Microsoft Teams), transcribes it locally with
Apple's on-device SpeechAnalyzer (via [`ohr`](https://github.com/Arthur-Ficial/ohr)), and
saves an AI-generated summary as a Markdown note. macOS only.

## Quick start

### Prerequisites

- **Apple Silicon Mac** running **macOS 26 or later** (required for `ohr`/SpeechAnalyzer
  transcription and for `apfel`; there is no older-macOS or Intel fallback for either).
- **Apple Intelligence** enabled and signed into iCloud (step 2 below).
- **[Homebrew](https://brew.sh)** — installs Python, `ohr`, and `apfel`.
- **Python 3.10 or 3.11** specifically (step 1 below).
- **git**, to clone this repo.
- An **Anthropic (Claude) API key** — only if you opt out of the default on-device
  provider; get one at https://console.anthropic.com/.

Six steps, in order. All defaults are fully on-device — nothing leaves your Mac and
nothing needs an account or API key. Each step is expanded with requirements and
troubleshooting in [Setup](#setup) and in the full [USAGE.md](USAGE.md) guide.

1. **Install Python 3.11.** Check what you have first — `python3 --version`. If it's not
   3.10 or 3.11:
   ```bash
   brew install python@3.11
   ```
   `install.sh` (step 5 below) looks for a qualifying `python3` on your `PATH` and tells
   you clearly if it can't find one.

2. **Enable Apple Intelligence** (powers the AI summary via `apfel`, below):
   **System Settings → Apple Intelligence & Siri** → turn it on. Requires Apple Silicon,
   macOS 26+, being signed into iCloud, and Device Language + Siri Language set to the
   same supported language. The on-device model (~3–4 GB) then downloads in the
   background — give it a few minutes on first enable.

3. **Install and start `apfel`** (on-device AI summarization — turns the transcript into
   minutes/action items/tags):
   ```bash
   brew install apfel
   brew services start apfel
   ```

4. **Install `ohr`** (on-device transcription — required, not optional; same author as
   `apfel`):
   ```bash
   brew tap Arthur-Ficial/tap
   brew install Arthur-Ficial/tap/ohr
   ```
   Requires macOS 26+ and Apple Silicon. Nothing to download afterwards — the speech
   model ships with macOS itself. Unlike `apfel`, don't `brew services start` this one;
   `notetaker start` spawns and tears down its own `ohr --serve` process per recording.

5. **Clone this repo and run the installer:**
   ```bash
   git clone <this-repo-url>
   cd notetaker-app
   ./install.sh
   ```

6. **Verify everything's ready:**
   ```bash
   notetaker init
   ```
   It writes the default config and checks audio capture, `ohr`, and `apfel` in one
   pass — printing exactly what's missing and the command to fix it (e.g. `brew services
   start apfel`). Re-run it after fixing anything; it's safe to run repeatedly.

You're set — `notetaker start "Meeting title"` to begin recording, `notetaker stop` to
save the note. See [Usage](#usage) below for the full command list.

## Setup

1. **Nothing to install for meeting audio.** On macOS 14.2+ notetaker captures system audio
   through a Core Audio *process tap* — no driver, no admin password, no reboot, and it
   works the same whether you listen on speakers, wired or Bluetooth headphones. macOS asks
   once for **System Audio Recording** permission on the first `notetaker start`
   (System Settings → Privacy & Security → Screen & System Audio Recording).

   Optional: set `tap_process: com.microsoft.teams2` in `~/.notetaker/config.yaml` to
   capture only Teams (no Slack pings or music in the transcript). If Teams isn't running
   when you start, notetaker falls back to all system audio and says so in the transcript.

2. **Your own voice** is recorded from the macOS default input device (built-in mic, or
   your headset mic when connected — macOS picks it automatically). Nothing to configure;
   set `capture_microphone: false` in the config to turn it off. Transcript lines are
   labelled `Me:` / `Others:` so the minutes can assign action items to the right person.
   Without headphones your mic also hears the speakers; notetaker drops `Me:` lines that
   are near-duplicates of an `Others:` line at the same moment, but headphones give the
   cleanest transcript.

3. Clone this repo and run the installer:

   ```bash
   git clone <this-repo-url>
   cd notetaker-app
   ./install.sh
   ```

   `install.sh` requires Python 3.10 or 3.11 and will tell you clearly if your `python3`
   doesn't qualify.

4. **Transcription** runs on Apple's on-device SpeechAnalyzer, reached via
   [`ohr`](https://github.com/Arthur-Ficial/ohr) (same author and install pattern as
   `apfel` below). Install it:

   ```bash
   brew tap Arthur-Ficial/tap
   brew install Arthur-Ficial/tap/ohr
   ```

   Requires macOS 26+ and Apple Silicon. Unlike `apfel`, there's no `brew services start` —
   `notetaker start` spawns and tears down its own `ohr --serve` process for the duration
   of each recording, on a fixed port distinct from `apfel`'s. The speech model ships with
   macOS itself, so there is nothing to download and no Hugging Face (or any other network)
   access required.

5. Set your AI provider (the default config is fully on-device — nothing leaves your Mac):
   - **Apple Foundation Models (default, fully local, no API key):** install
     [`apfel`](https://github.com/Arthur-Ficial/apfel) (`brew install apfel`) and start it
     as a background service (`brew services start apfel`). Requires macOS 26+, Apple
     Silicon, and Apple Intelligence enabled in System Settings. The on-device model has a
     small (4K-token) context, so long meetings are summarized in chunks and then merged;
     `notetaker stop` shows "Summarizing... chunk 3 of 7" while it works.
   - **Claude (opt-in, cloud):** set `ai_provider: claude` and `ai_model: claude-sonnet-5`
     in `~/.notetaker/config.yaml` (or pick it on the dashboard's Settings page, which
     switches the model for you), then either run `notetaker set-api-key` (stores it in the
     macOS Keychain — recommended) or export `ANTHROPIC_API_KEY` in your shell profile.

6. Run setup checks:

   ```bash
   notetaker init
   ```

## Usage

```bash
notetaker start "Team Standup"   # begin recording + live transcription
notetaker stop                   # stop, summarize, save the note
notetaker list                   # show recent notes
notetaker show 2026-09-11-team-standup
notetaker resummarize 2026-09-11-team-standup   # redo the summary from the saved transcript
notetaker set-api-key            # store your Claude API key in the macOS Keychain
notetaker show-api-key           # show the masked, currently active key
```

## Dashboard & menu bar

`notetaker dashboard` runs both UI surfaces in one process: the local web dashboard at
http://127.0.0.1:8420 and a menu bar item. Start a recording from either and it shows up
in both — the menu bar logo turns red with the elapsed time (`12:34`) while recording,
its menu has Start/Stop and "Open Dashboard", and the dashboard's Record page shows the
live transcript as Me/Others rows with a running timer and "last transcribed Ns ago".
Stop from either surface: the Record page shows the saving progress and opens the finished
note; the menu bar shows "Saving…" then notifies. The dashboard also
browses, edits, deletes and resummarizes saved notes, and edits `config.yaml` plus the
Claude API key under Settings. It needs no internet (htmx is bundled) and follows the
system light/dark appearance.

`notetaker menubar` runs just the menu bar item, without the web server, if you never
want the browser UI. Don't run it alongside `notetaker dashboard` — you'd get two icons.

To keep it running in the background (auto-start at login, restart on crash), let
`brew services` manage it. `./install.sh` generates a personal Homebrew formula
(`Formula/notetaker-dashboard.rb`) — not published anywhere, just a local wrapper that
points `brew services` at the `.venv/bin/notetaker` that `./install.sh` already set up.
See `docs/adr/0003-brew-services-for-ui-processes-no-app-packaging.md` for why.

```bash
# One-time: create a local (unpublished) tap — brew tap-new avoids the
# "clone from a path" mechanism, which would silently drop the gitignored
# formula file below.
brew tap-new syedafrozpasha/notetaker --no-git

# Every time ./install.sh regenerates the formula (first run, or after
# moving/re-cloning this repo), copy it into that tap:
cp Formula/*.rb "$(brew --repository syedafrozpasha/notetaker)/Formula/"

brew install syedafrozpasha/notetaker/notetaker-dashboard
brew services start notetaker-dashboard   # http://127.0.0.1:8420 + menu bar item

brew services list                        # check status
brew services stop notetaker-dashboard
```

Re-run `./install.sh` any time you move or re-clone this repo — the formula bakes in an absolute
path and must be regenerated, then re-copied into the tap (the `cp` step above) and
`brew uninstall`/`brew install` again, if that path changes. If you set up the older, separate
`notetaker-menubar` service, `./install.sh` stops and uninstalls it for you.

## A note on recording consent

This tool captures system audio directly — Teams (or any other meeting app) has no idea
it's happening and will not show its own "this meeting is being recorded" indicator to
other participants. `notetaker start` prints a reminder each time, but it's on you to let
participants know per your organization's policy and local law.

**First run note:** macOS prompts for **Microphone** access (your voice) and **System Audio
Recording** (the meeting, in tap mode) when you first run `notetaker start` — allow both. If you
start recordings from the menu bar app or dashboard (run by `brew services`), the prompts are
attributed to the Python in `.venv`; if none appears and the transcript stays empty, grant both
under System Settings → Privacy & Security (Microphone, and Screen & System Audio Recording), or
run one `notetaker start`/`stop` from Terminal first.

## Locked-down / corporate Macs (Homebrew only, no other downloads)

- **Meeting audio needs no install** — captured via a Core Audio process tap built into macOS.
- **Transcription needs no download.** SpeechAnalyzer's speech model ships with macOS
  itself — only the `ohr` binary needs installing via Homebrew (step 4 above), which is
  exactly what this project switched to: `faster-whisper`'s Hugging Face model download
  used to be the thing a locked-down machine couldn't reach; SpeechAnalyzer has no such
  step. See `docs/adr/0005-speechanalyzer-via-ohr-replaces-faster-whisper.md`.
- **`./install.sh`** uses `pip` against PyPI; it needs the same proxy access your other
  Python tooling has.
- With the default `apple_local` provider no cloud service is contacted at any point.
