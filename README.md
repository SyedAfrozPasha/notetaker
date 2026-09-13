# notetaker

Records a meeting's system audio (e.g. Microsoft Teams), transcribes it locally with
`faster-whisper`, and saves an AI-generated summary as a Markdown note. macOS only.

## Setup

1. Install [BlackHole](https://github.com/ExistentialAudio/BlackHole) (a virtual audio
   loopback device) via Homebrew:

   ```bash
   brew install blackhole-2ch
   ```

   **Reboot your Mac after installing** — BlackHole's driver only becomes active after a
   restart. `notetaker init` will tell you if it's installed but not yet active.

2. Set up a Multi-Output Device so you can still hear the meeting while it's being
   captured: open **Audio MIDI Setup** (Applications → Utilities), click **+** → **Create
   Multi-Output Device**, check both your normal output (e.g. MacBook speakers) and
   **BlackHole 2ch**, then select that Multi-Output Device as your Mac's sound output
   during meetings. This step is manual and not automated by this project.

3. Clone this repo and run the installer:

   ```bash
   git clone <this-repo-url>
   cd notetaker-app
   ./install.sh
   ```

   `install.sh` requires Python 3.10 or 3.11 (not 3.12/3.13 — `faster-whisper`'s PyAV
   dependency doesn't reliably build there) and will tell you clearly if your `python3`
   doesn't qualify.

4. Set your AI provider:
   - **Claude (default, cloud):** either run `notetaker set-api-key` (stores it in the
     macOS Keychain — recommended), or export `ANTHROPIC_API_KEY` in your shell profile.
   - **Apple Foundation Models (opt-in, fully local, no API key):** install
     [`apfel`](https://github.com/Arthur-Ficial/apfel) (`brew install apfel`), start it
     as a background service (`brew services start apfel`), and set both `ai_provider:
     apple_local` and `ai_model: apple-foundationmodel` in `~/.notetaker/config.yaml`. Requires macOS 26+, Apple Silicon, and
     Apple Intelligence enabled in System Settings.

5. Run setup checks and download the transcription model:

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

## Menu bar app & dashboard (via `brew services`)

`./install.sh` also generates two personal Homebrew formulas (`Formula/notetaker-dashboard.rb`,
`Formula/notetaker-menubar.rb`) — not published anywhere, just local wrappers so `brew services`
can auto-start and crash-restart these two long-running processes, the same way you'd manage any
other background service on a Mac where Homebrew is the sanctioned install path. Neither formula
builds anything; they just point `brew services` at the `.venv/bin/notetaker` that `./install.sh`
already set up. See `docs/adr/0003-brew-services-for-ui-processes-no-app-packaging.md` for why.

```bash
brew tap syedafrozpasha/notetaker "$(pwd)"
brew install syedafrozpasha/notetaker/notetaker-dashboard
brew install syedafrozpasha/notetaker/notetaker-menubar

brew services start notetaker-dashboard   # http://127.0.0.1:8420
brew services start notetaker-menubar

brew services list                        # check status
brew services stop notetaker-dashboard    # or notetaker-menubar
```

Re-run `./install.sh` any time you move or re-clone this repo — the formulas bake in an absolute
path and must be regenerated (then `brew uninstall`/`brew install` again) if that path changes.

## A note on recording consent

This tool captures system audio directly — Teams (or any other meeting app) has no idea
it's happening and will not show its own "this meeting is being recorded" indicator to
other participants. `notetaker start` prints a reminder each time, but it's on you to let
participants know per your organization's policy and local law.

**First run note:** macOS will prompt for microphone access to read the BlackHole device
when you first run `notetaker start` — please allow it for recording to work.
