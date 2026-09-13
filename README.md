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

   **If you use headphones**, make a second Multi-Output Device containing your
   headphones + BlackHole 2ch (Bluetooth headphones must be connected to show up in the
   list). macOS switches the sound output straight to headphones every time you connect
   them, which bypasses BlackHole and gives an empty transcript — so re-select the
   headphones Multi-Output Device in **System Settings → Sound → Output** after
   connecting. `notetaker start` warns if the current output is not a Multi-Output
   Device, and the live transcript shows a warning if meeting audio stays silent while
   your mic is active. Volume keys don't work while a Multi-Output Device is selected;
   adjust volume in Audio MIDI Setup or on the headphones themselves.

   **Your own voice** is recorded from the macOS default input device (built-in mic, or
   your headset mic when connected — macOS picks it automatically). Nothing to configure;
   set `capture_microphone: false` in the config to turn it off. Transcript lines are
   labelled `Me:` / `Others:` so the minutes can assign action items to the right person.
   Without headphones your mic also hears the speakers, so the meeting may appear twice
   in the transcript (once under each label) — headphones avoid this.

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

## Menu bar app & dashboard (via `brew services`)

`./install.sh` also generates two personal Homebrew formulas (`Formula/notetaker-dashboard.rb`,
`Formula/notetaker-menubar.rb`) — not published anywhere, just local wrappers so `brew services`
can auto-start and crash-restart these two long-running processes, the same way you'd manage any
other background service on a Mac where Homebrew is the sanctioned install path. Neither formula
builds anything; they just point `brew services` at the `.venv/bin/notetaker` that `./install.sh`
already set up. See `docs/adr/0003-brew-services-for-ui-processes-no-app-packaging.md` for why.

```bash
# One-time: create a local (unpublished) tap — brew tap-new avoids the
# "clone from a path" mechanism, which would silently drop the gitignored
# formula files below.
brew tap-new syedafrozpasha/notetaker --no-git

# Every time ./install.sh regenerates the formulas (first run, or after
# moving/re-cloning this repo), copy them into that tap:
cp Formula/*.rb "$(brew --repository syedafrozpasha/notetaker)/Formula/"

brew install syedafrozpasha/notetaker/notetaker-dashboard
brew install syedafrozpasha/notetaker/notetaker-menubar

brew services start notetaker-dashboard   # http://127.0.0.1:8420
brew services start notetaker-menubar

brew services list                        # check status
brew services stop notetaker-dashboard    # or notetaker-menubar
```

Re-run `./install.sh` any time you move or re-clone this repo — the formulas bake in an absolute
path and must be regenerated, then re-copied into the tap (the `cp` step above) and
`brew uninstall`/`brew install` again, if that path changes.

## A note on recording consent

This tool captures system audio directly — Teams (or any other meeting app) has no idea
it's happening and will not show its own "this meeting is being recorded" indicator to
other participants. `notetaker start` prints a reminder each time, but it's on you to let
participants know per your organization's policy and local law.

**First run note:** macOS will prompt for microphone access (used for both BlackHole and your
mic) when you first run `notetaker start` — please allow it for recording to work. If you start
recordings from the menu bar app or dashboard (run by `brew services`), the prompt is attributed
to the Python in `.venv`; if no prompt appears and the transcript stays empty, grant it under
System Settings → Privacy & Security → Microphone.

## Locked-down / corporate Macs (Homebrew only, no other downloads)

- **BlackHole** installs via `brew install blackhole-2ch`, but it is a cask that runs a
  `.pkg` installer and needs an administrator password.
- **The Whisper model** is normally downloaded from huggingface.co on `notetaker init`.
  If that host is blocked, copy a faster-whisper model directory from another machine
  (e.g. `~/.cache/huggingface/hub/models--Systran--faster-whisper-base.en/snapshots/<id>/`,
  which contains `model.bin`, `config.json`, `tokenizer.json`, `vocabulary.txt`) to the
  locked-down Mac and set `whisper_model_path: /path/to/that/dir` in
  `~/.notetaker/config.yaml`. `notetaker init` then loads it with no network access.
- **`./install.sh`** uses `pip` against PyPI; it needs the same proxy access your other
  Python tooling has.
- With the default `apple_local` provider no cloud service is contacted at any point.
