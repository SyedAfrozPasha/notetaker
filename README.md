# notetaker

Records a meeting's system audio (e.g. Microsoft Teams), transcribes it locally with
`faster-whisper`, and saves an AI-generated summary as a Markdown note. macOS only.

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

   <details>
   <summary><b>Fallback: BlackHole loopback (older macOS, or if the tap is blocked by IT)</b></summary>

   Set `system_audio: blackhole` in `~/.notetaker/config.yaml`, then:

   - Install [BlackHole](https://github.com/ExistentialAudio/BlackHole) via Homebrew
     (`brew install blackhole-2ch` — a cask that runs a `.pkg` installer, so it needs an
     administrator password) and **reboot**; `notetaker init` tells you if it's installed
     but not yet active.
   - Create a Multi-Output Device in **Audio MIDI Setup** (Applications → Utilities: **+** →
     **Create Multi-Output Device**, tick your speakers + **BlackHole 2ch**) and select it in
     **System Settings → Sound → Output** during meetings. If you use headphones, make a
     second Multi-Output Device with headphones + BlackHole (Bluetooth headphones must be
     connected to appear) and re-select it every time you connect them — macOS switches
     output straight to headphones, which bypasses BlackHole and gives an empty transcript.
     `notetaker start` warns when the output is not a Multi-Output Device, and the live
     transcript warns if meeting audio stays silent while your mic is active. Volume keys
     don't work while a Multi-Output Device is selected.
   </details>

3. Clone this repo and run the installer:

   ```bash
   git clone <this-repo-url>
   cd notetaker-app
   ./install.sh
   ```

   `install.sh` requires Python 3.10 or 3.11 (not 3.12/3.13 — `faster-whisper`'s PyAV
   dependency doesn't reliably build there) and will tell you clearly if your `python3`
   doesn't qualify.

4. Set your AI provider (the default config is fully on-device — nothing leaves your Mac):
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

## Dashboard & menu bar

`notetaker dashboard` runs both UI surfaces in one process: the local web dashboard at
http://127.0.0.1:8420 and a menu bar item. Start a recording from either and it shows up
in both — the menu bar icon turns into a record glyph with the elapsed time (`12:34`)
while recording, its menu has Start/Stop and "Open Dashboard", and the dashboard's
Record page shows the live transcript as Me/Others rows with a running timer and "last
transcribed Ns ago", then opens the finished note once Stop & save is done. The dashboard also
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

- **Meeting audio needs no install** in the default tap mode. If an MDM profile denies
  "System Audio Recording", the fallback is BlackHole (`brew install blackhole-2ch`, a cask
  that runs a `.pkg` installer and needs an administrator password).
- **The Whisper model** is normally downloaded from huggingface.co on `notetaker init`.
  If that host is blocked, copy a faster-whisper model directory from another machine
  (e.g. `~/.cache/huggingface/hub/models--Systran--faster-whisper-base.en/snapshots/<id>/`,
  which contains `model.bin`, `config.json`, `tokenizer.json`, `vocabulary.txt`) to the
  locked-down Mac and set `whisper_model_path: /path/to/that/dir` in
  `~/.notetaker/config.yaml`. `notetaker init` then loads it with no network access.
- **`./install.sh`** uses `pip` against PyPI; it needs the same proxy access your other
  Python tooling has.
- With the default `apple_local` provider no cloud service is contacted at any point.
