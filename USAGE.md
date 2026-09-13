# Notetaker — Full Installation & Usage Guide (macOS)

Notetaker is a macOS-only CLI that records a meeting's **system audio** (e.g. a
Microsoft Teams call) **and your own voice**, transcribes both locally with
`faster-whisper`, and turns the finished transcript into AI-generated meeting minutes
(summary, action items with owners, tags) saved as a Markdown note. With the default
settings nothing leaves your Mac and nothing needs to be installed beyond the tool itself.

This guide covers everything end-to-end: macOS-level setup (audio routing, permissions),
installing the tool, configuring an AI provider, day-to-day usage from the CLI, and the
optional menu bar app / web dashboard. See also [README.md](README.md) for the condensed
version and [CONTEXT.md](CONTEXT.md) for terminology.

---

## Table of contents

1. [How it works, in one paragraph](#how-it-works-in-one-paragraph)
2. [Requirements](#requirements)
3. [Step 1 — How meeting audio and your voice are captured](#step-1--how-meeting-audio-and-your-voice-are-captured)
4. [Step 2 — Clone the repo and run the installer](#step-2--clone-the-repo-and-run-the-installer)
5. [Step 3 — Choose and configure an AI provider](#step-3--choose-and-configure-an-ai-provider)
6. [Step 4 — Run `notetaker init`](#step-4--run-notetaker-init)
7. [macOS permission prompts you'll see](#macos-permission-prompts-youll-see)
8. [Day-to-day usage (CLI)](#day-to-day-usage-cli)
9. [Where notes are stored, and their format](#where-notes-are-stored-and-their-format)
10. [Menu bar app](#menu-bar-app)
11. [Web dashboard](#web-dashboard)
12. [Running the menu bar app / dashboard permanently via `brew services`](#running-the-menu-bar-app--dashboard-permanently-via-brew-services)
13. [Configuration reference (`~/.notetaker/config.yaml`)](#configuration-reference-notetakerconfigyaml)
14. [Locked-down / corporate Macs](#locked-down--corporate-macs)
15. [Fallback: BlackHole loopback instead of the audio tap](#fallback-blackhole-loopback-instead-of-the-audio-tap)
16. [Updating Notetaker](#updating-notetaker)
17. [Uninstalling](#uninstalling)
18. [Troubleshooting](#troubleshooting)
19. [Recording consent — read this](#recording-consent--read-this)

---

## How it works, in one paragraph

You run `notetaker start "Meeting title"` before or as your Teams call begins. A
background **Recorder** process captures two audio sources at once: the meeting (every
app's sound, taken straight from macOS through a **Core Audio process tap** — no virtual
audio device, and it doesn't matter whether you're on speakers or headphones) and your
own voice (from your Mac's default microphone, or your headset mic when one is
connected). It writes them as ~10 second stereo chunks, and a **Transcriber** turns each
chunk into timestamped text with a local Whisper model, labelling lines `Me:` and
`Others:` — no audio ever leaves your machine. When you run `notetaker stop`, the
transcript is sent to your chosen AI provider (by default Apple's on-device Foundation
Model via `apfel`, so still nothing leaves the Mac) to produce minutes, action items
with owners, and tags, and everything is saved as one Markdown file. If the Recorder
process ever crashes mid-meeting, the partial transcript is automatically salvaged into a
note the next time you run a command — you never lose the record of a meeting.

---

## Requirements

- **macOS 14.2 or later** for the default audio capture (Core Audio process taps). The
  default on-device AI provider needs **macOS 26+ and Apple Silicon** (see
  [Step 3](#step-3--choose-and-configure-an-ai-provider)); older Macs can use the Claude
  provider instead, and macOS older than 14.2 can use the
  [BlackHole fallback](#fallback-blackhole-loopback-instead-of-the-audio-tap).
- **Homebrew** (https://brew.sh) — used to install Python, `apfel`, and (optionally) to run
  the menu bar app/dashboard as background services.
- **Python 3.10 or 3.11** specifically. Not 3.12/3.13 — `faster-whisper`'s `PyAV`
  dependency doesn't reliably build on newer Python. Install one if you don't have it:
  ```bash
  brew install python@3.11
  ```
- **git**, to clone the repository.
- An **Anthropic (Claude) API key** only if you opt into the cloud provider — get one at
  https://console.anthropic.com/. The default provider needs no key and no network.

---

## Step 1 — How meeting audio and your voice are captured

There is nothing to install for audio. Two things are worth knowing:

### Meeting audio: the system audio tap

Notetaker asks macOS for a **process tap** — a Core Audio feature (macOS 14.2+) that hands
an app a copy of everything the system is playing, *before* it is routed to speakers or
headphones. Consequences:

- No driver, no admin password, no reboot, no Audio MIDI Setup.
- Speakers, wired headphones, AirPods — switch freely, even mid-meeting; capture is
  unaffected.
- macOS asks once for **System Audio Recording** permission (see
  [permission prompts](#macos-permission-prompts-youll-see)).
- Optionally capture **only Microsoft Teams** so Slack pings or music don't end up in
  the transcript: set `tap_process: com.microsoft.teams2` in `~/.notetaker/config.yaml`.
  If Teams isn't running when you start, Notetaker records all system audio instead and
  writes a note saying so at the top of the transcript.

### Your own voice: the default microphone

Notetaker also opens your Mac's **default input device** — the built-in microphone, or
your headset's microphone when one is connected (macOS switches the default input
automatically). Nothing to configure. Set `capture_microphone: false` in the config to
turn it off.

Transcript lines are labelled by source, e.g.

```
[00:12:03] Me: Can we push the release to Thursday?
[00:12:09] Others: Fine by me, I'll update the ticket.
```

so the minutes can assign action items to the right person ("Me: update the ticket").

**Headphones are recommended.** Without them, your microphone also hears your speakers.
Notetaker drops a `Me:` line when it is a near-duplicate of an `Others:` line from the
same moment (an echo of the speakers), so most of the doubling is removed automatically —
but a garbled echo can slip through, and headphones give the cleanest transcript.

---

## Step 2 — Clone the repo and run the installer

```bash
git clone <this-repo-url>
cd notetaker-app
./install.sh
```

What `install.sh` does:
- Finds a valid Python (3.10 or 3.11) on your `PATH` and tells you clearly if none
  qualifies, with the exact `brew install python@3.11` fix.
- Creates a virtualenv at `.venv/` inside the repo and installs Notetaker and its
  dependencies into it (pinned versions — no ambient/global install).
- Symlinks the `notetaker` command into `~/.local/bin/notetaker`.
- Generates one personal Homebrew formula file under `Formula/` for the dashboard + menu
  bar process (see [Running via `brew services`](#running-the-menu-bar-app--dashboard-permanently-via-brew-services) — optional, skip if you only want the CLI).
- If you're re-running it after already having `notetaker-dashboard` running as a
  `brew services` service, it stops it first, rebuilds the virtualenv, and restarts it
  automatically. If it finds the older, separate `notetaker-menubar` service, it stops
  and uninstalls that one (the menu bar is part of the dashboard process now).

Make sure `~/.local/bin` is on your `PATH`. If `notetaker` isn't found after installing,
add this to your `~/.zshrc` (the default macOS shell) and open a new terminal tab:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Verify:
```bash
notetaker --help
```

---

## Step 3 — Choose and configure an AI provider

Notetaker needs an AI provider to turn a transcript into meeting minutes. You have two
options; the default is fully local.

### Option A — Apple Foundation Models via `apfel` (default: fully local, no API key, no network)

Everything — recording, transcription, *and* summarization — stays on your Mac. This is
the default configuration, so nothing to change in `config.yaml`.

Requirements:
- **Apple Silicon Mac**
- **macOS 26 or later**
- **Apple Intelligence enabled**: **System Settings → Apple Intelligence & Siri** →
  turn on Apple Intelligence (requires being signed into iCloud and the feature being
  available for your region/language). The on-device model (~3–4 GB) downloads in the
  background after you enable it; `notetaker init` tells you if it isn't ready yet.

Setup:
```bash
brew install apfel
brew services start apfel
```

Good to know: Apple's on-device model has a small context window, so Notetaker
summarizes long meetings in chunks and then merges the partial minutes. `notetaker stop`
shows the progress ("Summarizing... chunk 3 of 7"); an hour-long meeting takes a few
minutes.

### Option B — Claude (opt-in, cloud-based)

Higher-quality minutes for long meetings, but the finished transcript is sent to
Anthropic's API when you stop a recording (nothing is sent while recording — only at
`notetaker stop`, and only the transcript, never raw audio). Requires an Anthropic API
key.

Edit `~/.notetaker/config.yaml` and set (or pick "Claude" on the
[dashboard's Settings page](#web-dashboard), which fills in the model for you):
```yaml
ai_provider: claude
ai_model: claude-sonnet-5
```

Store your key in the macOS **Keychain** (recommended — never touches disk in
plaintext):
```bash
notetaker set-api-key
# You'll be prompted to paste your key with input hidden, e.g.:
#   Enter your Claude API key:
```

Or, alternatively, export it in your shell profile (`~/.zshrc`):
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```
(Notetaker checks the Keychain first, then falls back to this environment variable.)

Check what's currently active (masked, so it's safe to screenshot):
```bash
notetaker show-api-key
# e.g. sk-ant••••1234
```

---

## Step 4 — Run `notetaker init`

This writes the default config file (if one doesn't exist yet), checks that audio capture
and your AI provider are ready, and downloads the Whisper transcription model (only
happens once — it's cached locally afterwards).

```bash
notetaker init
```

Expected output looks like:
```
Wrote default config to /Users/you/.notetaker/config.yaml
System audio: Core Audio process tap (nothing to install). macOS will ask for 'System Audio Recording' permission on the first `notetaker start`.
apfel is installed and running.
Loading Whisper model 'base.en' (downloads on first run)...
Whisper model ready.
```

If something's missing, `init` tells you exactly what and how to fix it (e.g. "apfel is
not installed. Run: brew install apfel", or "apple_local model is not available — enable
Apple Intelligence…"). Re-run `notetaker init` any time after fixing a reported issue —
it's safe to run repeatedly and won't overwrite an existing config.

If the Whisper download fails because your Mac can't reach huggingface.co, see
[Locked-down / corporate Macs](#locked-down--corporate-macs).

---

## macOS permission prompts you'll see

The first time you run `notetaker start`, expect the following system prompts. Approve
all of them — declining any will break recording or notifications.

| Prompt | When | What to do |
|---|---|---|
| **Microphone access** | First `notetaker start` | Needed to record your own voice. Click **Allow**. If you miss it, go to **System Settings → Privacy & Security → Microphone** and enable it for your Terminal app (Terminal.app, iTerm2, etc. — whichever you ran `notetaker` from). |
| **System Audio Recording** | First `notetaker start` (tap mode) | Needed to capture the meeting audio. Click **Allow**. If you miss it: **System Settings → Privacy & Security → Screen & System Audio Recording** → enable "System Audio Recording Only" for your terminal app. Without it the transcript contains only your side and a warning line. |
| **Notifications** (menu bar app only) | First time the menu bar app tries to notify you | Go to **System Settings → Notifications** and make sure notifications aren't blocked for your terminal/Python process if alerts on save-complete/failure don't appear. Because the menu bar app runs as an unbundled Python script rather than a signed `.app`, macOS notification delivery is best-effort — Notetaker won't crash if a notification silently doesn't show, but you can rely on `notetaker list`/the dashboard to confirm a note saved. |
| **Local network / incoming connections** (dashboard only) | First time `notetaker dashboard` binds a port | It's bound to `127.0.0.1` only (not reachable from other devices) but macOS may still ask about your terminal accepting incoming connections. Allow it. |

No **Screen Recording** or **Accessibility** permission is required — Notetaker only
captures audio, never the screen or keystrokes ("System Audio Recording Only" lives under
the Screen & System Audio Recording pane, but it grants audio only).

**If you start recordings from the menu bar app or dashboard** (run by `brew services`),
the prompts are attributed to the Python inside the repo's `.venv` and may not appear at
all. Run one `notetaker start` / `notetaker stop` from Terminal first so both permissions
get granted, then use the menu bar or dashboard. If the transcript still stays empty,
grant them by hand in the two Privacy & Security panes above.

---

## Day-to-day usage (CLI)

```bash
notetaker start "Team Standup"                    # begin recording + live transcription
notetaker stop                                    # stop, summarize, save the note
notetaker list                                    # show recent notes
notetaker show 2026-09-11-team-standup            # print one note's full Markdown
notetaker resummarize 2026-09-11-team-standup     # redo the summary from the saved transcript
notetaker set-api-key                             # store/replace your Claude API key
notetaker show-api-key                            # show the masked, currently active key
notetaker dashboard                               # web UI at http://127.0.0.1:8420 + menu bar item (see "Web dashboard")
notetaker menubar                                 # menu bar item only, no web UI (see "Menu bar app")
```

A typical session:
1. Put on your headphones (optional, but keeps your side and theirs from being
   transcribed twice) and join/start the Teams meeting as normal.
2. `notetaker start "Sprint Planning"` — you'll see a reminder about the permission
   prompts and about telling participants they're being recorded (Teams shows **no**
   indicator of its own — see [Recording consent](#recording-consent--read-this)).
3. Let the meeting run. Notetaker transcribes continuously in ~10s rolling chunks in the
   background; it doesn't block your terminal usage other than the one it's running in.
   The transcript grows under `~/.notetaker/sessions/<timestamp>/transcript.txt` (the
   dashboard shows it live), so you can check early that both `Me:` and `Others:` lines
   are appearing.
4. `notetaker stop` when the meeting ends. This shows a progress spinner while it
   finalizes the transcript and calls your AI provider ("Summarizing... chunk 2 of 5"),
   then prints the saved note's path.

If a recording was started by mistake or isn't worth keeping, there's no dedicated CLI
"cancel" command yet — either let it finish and delete the resulting note file
afterwards, or use the **Cancel** button in the [web dashboard](#web-dashboard), which
discards the in-progress session without producing a note at all.

**Crash recovery:** if the Recorder process dies unexpectedly (crash, `kill -9`, laptop
sleep issues, etc.), the next time you run *any* `notetaker` command (`start`, or a
dashboard/menu bar poll), Notetaker detects the orphaned session and automatically
salvages whatever transcript exists into a note — you're told this happened, e.g.:
```
Recovered a crashed session and saved it as a note: /Users/you/notetaker-notes/2026-09-11-standup.md
```

---

## Where notes are stored, and their format

By default, notes are saved under `~/notetaker-notes/` (configurable — see
[Configuration reference](#configuration-reference-notetakerconfigyaml)), one Markdown
file per meeting, named `<date>-<title-slug>.md` (e.g. `2026-09-11-team-standup.md`; a
time suffix like `-1430` is appended only if two meetings share the same date and
title).

Each note looks like:

```markdown
---
title: Team Standup
date: '2026-09-11T09:30:00'
duration_minutes: 18
tags: [planning, backend]
---

## Summary
<AI-generated minutes: key discussion points, decisions made, open questions>

## Action Items
- [ ] Me: follow up with design on the new onboarding flow
- [ ] Priya: file a ticket for the flaky CI job (by Friday)

## Transcript
[00:00:03] Me: ...
[00:00:14] Others: ...
```

Alongside each note, a `<note-id>.transcript.txt` sidecar file is also kept (not
deleted) so `notetaker resummarize` can regenerate the summary later without needing to
re-transcribe anything — useful if you change AI providers or the first summarization
attempt failed.

If summarization fails (e.g. `apfel` not running, network issue, invalid API key), the
transcript is **still saved** — the Summary section will note the failure instead of losing the meeting
record. Fix the underlying issue, then run `notetaker resummarize <note-id>`.

---

## Menu bar app

A `rumps`-based menu bar item gives you one-click start/stop without opening a terminal,
a live elapsed-time display in the menu bar, and native notifications when a recording
saves, when summarization fails, or when a crash is detected and salvaged.

It is part of `notetaker dashboard` (see [Web dashboard](#web-dashboard)): whenever the
dashboard is running, the menu bar item is there too, and a recording started from the
browser shows up in it within two seconds. To run the menu bar item **on its own**,
without the web UI (blocks the terminal it's run from):
```bash
notetaker menubar
```
Don't run `notetaker menubar` alongside `notetaker dashboard` — you'd get two icons.

You'll see the Notetaker logo (a rounded square with a dot, the same mark as the
dashboard) in your macOS menu bar: monochrome when idle, red while recording, followed by
the elapsed time, **MM:SS**, ticking every second, and **Saving…** while a stopped
recording is being transcribed and summarized. Stopping never freezes the menu bar; a
notification arrives when the note is saved, and the dashboard's Record page (if open)
shows the same progress and opens the note. Click the icon for a **Start Recording** /
**Stop Recording** menu item (recordings
started this way get an auto-generated title like "Meeting 2026-09-11 09:30"), an
**Open Dashboard** item (when run via `notetaker dashboard`), and a **Quit** item.

---

## Web dashboard

A local-only web UI (bound to `127.0.0.1` — never reachable from other devices on your
network) at **http://127.0.0.1:8420**, for starting/stopping recordings from a browser
and for browsing, searching, editing, deleting, and resummarizing past notes, plus a
Settings page. It needs no internet connection (htmx is bundled) and follows your
system's light/dark appearance.

Run it directly:
```bash
notetaker dashboard
```
This one process serves the web UI **and** puts the menu bar item up (see
[Menu bar app](#menu-bar-app)); use the menu bar's **Open Dashboard** item or open
http://127.0.0.1:8420 in your browser. If port 8420 is already taken (usually a second
`notetaker dashboard`), the command exits with an error instead of starting.

What it offers:
- **Record page** — live status: a pulsing timer with the meeting title and tags, the
  transcript so far as **Me** / **Others** rows (recorder notices such as "no meeting
  audio detected" appear inline in amber), a segment count and "last transcribed Ns ago"
  so you can tell audio is flowing, and **Stop & save** / **Cancel** buttons. The
  transcript pane follows the newest line unless you scroll up to re-read. **Stop & save**
  switches the page to a processing view (which step is running, how long so far) and
  opens the finished note as soon as it is saved. Unlike the CLI, **Cancel** is available
  here — it discards an in-progress session with no note produced. Audio-routing warnings from start stay on screen for the whole session. The
  page polls its own status every 2 seconds, and — like the menu bar app — this is also
  when it checks for and salvages any crashed session.
- **Notes** (`/notes`) — browse and search all saved notes by text, tag, or date range.
- **Note detail** (`/notes/<id>`) — full note view with copy buttons, an **Edit** page
  (title, tags, summary text, action items), **Delete**, and **Resummarize**.
- **Settings** (`/settings`) — change every config value without hand-editing the YAML
  file: notes directory, Whisper model and offline model path, whether to record your
  microphone, the meeting audio source (tap or BlackHole) and the tap-only-this-app
  bundle id, AI provider and model. Switching provider fills in that provider's default
  model. When the provider is Claude, a section appears to set/replace the API key
  (masked display, live-validated, stored in the Keychain).

---

## Running the menu bar app / dashboard permanently via `brew services`

Running `notetaker dashboard` directly is fine for trying it out, but it ties up a
terminal tab and won't restart itself if it crashes or after a reboot. `install.sh`
already generated one **personal, unpublished** Homebrew formula
(`Formula/notetaker-dashboard.rb`) that wraps `.venv/bin/notetaker dashboard` — it
builds nothing of its own, it just lets `brew services` supervise that one process (web
dashboard + menu bar item) the way it would any other background service (see
`docs/adr/0003-brew-services-for-ui-processes-no-app-packaging.md` for the reasoning —
this approach was chosen specifically to avoid `.app` packaging or hand-rolled `launchd`
plists, and its amendment for why the menu bar is no longer a second service).

One-time setup:
```bash
# Create a local (unpublished) tap. `brew tap-new` is used deliberately —
# tapping directly from a path/URL would silently drop the gitignored formula file.
brew tap-new syedafrozpasha/notetaker --no-git

# Copy the generated formula into that tap:
cp Formula/*.rb "$(brew --repository syedafrozpasha/notetaker)/Formula/"

brew install syedafrozpasha/notetaker/notetaker-dashboard
```

Start/stop/check status:
```bash
brew services start notetaker-dashboard   # always on: http://127.0.0.1:8420 + menu bar item

brew services list                        # check status
brew services stop notetaker-dashboard
```

**After moving or re-cloning this repo:** the formula bakes in this machine's absolute
path to the repo, so re-run `./install.sh` (regenerates it), re-copy it into the tap
(the `cp` step above), then `brew uninstall` + `brew install` again.

Logs (if the service silently isn't behaving as expected):
```bash
tail -f "$(brew --prefix)/var/log/notetaker-dashboard.log"
```

---

## Configuration reference (`~/.notetaker/config.yaml`)

Created by `notetaker init` with these defaults:

```yaml
notes_dir: ~/notetaker-notes
whisper_model: base.en          # tiny.en/base.en/small.en/medium.en
# whisper_model_path: /path/to/faster-whisper-model   # offline machines: load the model from this
#                                                      # directory instead of downloading from Hugging Face
capture_microphone: true        # also record your own voice from the default input device
system_audio: tap               # tap = Core Audio process tap (macOS 14.2+, nothing to install)
                                # blackhole = BlackHole loopback device + Multi-Output Device
# tap_process: com.microsoft.teams2   # tap only this app's audio (falls back to all audio if not running)
ai_provider: apple_local        # apple_local (fully on-device via apfel) or claude (cloud)
ai_model: apple-foundationmodel
api_key_env: ANTHROPIC_API_KEY  # only used by ai_provider: claude; never stored in this file
```

| Key | Meaning | Notes |
|---|---|---|
| `notes_dir` | Where finished `.md` notes (and `.transcript.txt` sidecars) are saved | |
| `whisper_model` | Local Whisper model size used for transcription | Larger = more accurate but slower and more memory. `base.en` keeps up with a meeting comfortably; `small.en` is noticeably more accurate on Apple Silicon and still faster than real time. `.en` variants are better for English-only meetings |
| `whisper_model_path` | Optional. A local faster-whisper model directory to load instead of downloading | For Macs that can't reach huggingface.co — see [Locked-down / corporate Macs](#locked-down--corporate-macs) |
| `capture_microphone` | `true`/`false`. Record your own voice from the macOS default input device | Off = transcript has only the meeting audio, no `Me:`/`Others:` labels |
| `system_audio` | `tap` (default) or `blackhole` | `tap` needs macOS 14.2+ and the System Audio Recording permission; `blackhole` is the [fallback](#fallback-blackhole-loopback-instead-of-the-audio-tap) |
| `tap_process` | Optional. Bundle id of the only app to capture in tap mode | e.g. `com.microsoft.teams2` (new Teams). Falls back to all system audio, with a note in the transcript, when that app isn't running |
| `ai_provider` | `apple_local` (default) or `claude` | See [Step 3](#step-3--choose-and-configure-an-ai-provider) |
| `ai_model` | Model name passed to the provider | `apple-foundationmodel` for the local provider, e.g. `claude-sonnet-5` for Claude. Changing `ai_provider` without naming a model resets this to the new provider's default |
| `api_key_env` | Name of the env var Notetaker falls back to if nothing is in the Keychain | Only relevant for `claude` |

The Claude API key itself is **never** stored in this file — only in the macOS Keychain
(service name `notetaker`) or read from the environment at runtime. Every key except
`api_key_env` can be changed from the dashboard's Settings page; all of them can be
hand-edited.

---

## Locked-down / corporate Macs

If your Mac only allows software from Homebrew (no App Store, no downloads from
websites), the defaults are designed for exactly that situation:

- **Meeting audio needs no install** (the tap is built into macOS). If an IT profile
  denies the "System Audio Recording" permission, use the
  [BlackHole fallback](#fallback-blackhole-loopback-instead-of-the-audio-tap) — but note
  BlackHole is a cask that runs a `.pkg` installer and needs an administrator password.
- **The AI provider is on-device** (`apfel`, installed with Homebrew); no cloud service
  is contacted at any point with the default configuration.
- **The Whisper model** is normally downloaded from huggingface.co on `notetaker init`.
  If that host is blocked, copy a faster-whisper model directory from another machine —
  e.g. `~/.cache/huggingface/hub/models--Systran--faster-whisper-base.en/snapshots/<id>/`,
  which contains `model.bin`, `config.json`, `tokenizer.json`, `vocabulary.txt` — to the
  locked-down Mac, and set in `~/.notetaker/config.yaml`:
  ```yaml
  whisper_model_path: /Users/you/models/faster-whisper-base.en
  ```
  `notetaker init` then loads it with no network access.
- **`./install.sh`** uses `pip` against PyPI; it needs the same proxy access your other
  Python tooling has.

---

## Fallback: BlackHole loopback instead of the audio tap

Only needed on macOS older than 14.2, or where IT blocks the System Audio Recording
permission. Set `system_audio: blackhole` in `~/.notetaker/config.yaml` (or pick it on
the dashboard's Settings page), then:

1. Install [BlackHole](https://github.com/ExistentialAudio/BlackHole), a free virtual
   audio device that you route your Mac's system output into:
   ```bash
   brew install blackhole-2ch
   ```
   This is a cask that runs a `.pkg` installer, so it needs an administrator password.
   **Reboot afterwards** — the driver only becomes active after a restart; `notetaker
   init` reports "installed but not active" until you do.

2. Create a **Multi-Output Device** so you still hear the meeting: open **Audio MIDI
   Setup** (Applications → Utilities), click **+** → **Create Multi-Output Device**, tick
   both your normal output (e.g. **MacBook Pro Speakers**) and **BlackHole 2ch**, and
   select that Multi-Output Device in **System Settings → Sound → Output** during
   meetings.

3. **If you use headphones**, make a second Multi-Output Device containing your
   headphones + BlackHole 2ch (Bluetooth headphones must be connected to show up in the
   list). macOS switches the sound output straight to headphones every time you connect
   them, which bypasses BlackHole and gives an empty transcript — so re-select the
   headphones Multi-Output Device after connecting. `notetaker start` warns if the
   current output is not a Multi-Output Device, and the live transcript shows a warning
   if meeting audio stays silent while your mic is active.

4. Volume keys don't work while a Multi-Output Device is selected; adjust volume in
   Audio MIDI Setup or on the headphones themselves. Switch back to your normal output
   after the meeting.

---

## Updating Notetaker

```bash
cd notetaker-app
git pull
./install.sh
```
`install.sh` detects and safely restarts a running `notetaker-dashboard` `brew services`
service around the virtualenv rebuild (and retires the old separate `notetaker-menubar`
service if it finds one). If a recording is
currently in progress, `install.sh` will refuse to run and tell you to `notetaker stop`
first — it never touches an in-progress session's files.

---

## Uninstalling

```bash
# Stop and remove the background service, if you set it up:
brew services stop notetaker-dashboard
brew uninstall syedafrozpasha/notetaker/notetaker-dashboard
brew untap syedafrozpasha/notetaker

# Remove the CLI symlink and the repo/venv:
rm ~/.local/bin/notetaker
rm -rf /path/to/notetaker-app   # the cloned repo, including its .venv

# Optional — remove saved config, session state, and the Keychain entry:
rm -rf ~/.notetaker
security delete-generic-password -s notetaker -a ANTHROPIC_API_KEY

# Your notes are NOT touched by any of the above — they live wherever
# notes_dir pointed (default ~/notetaker-notes/). Remove that directory
# yourself if you want the notes gone too.

# Only if you used the BlackHole fallback — remove it and the Multi-Output Device:
brew uninstall blackhole-2ch
# Then delete the Multi-Output Device in Audio MIDI Setup manually.
# (The audio tap creates nothing persistent — nothing to remove.)
```

---

## Troubleshooting

**The transcript has only `Me:` lines (or is empty) and ends with a "[warning] no meeting
audio detected" line.**
In tap mode the warning appears only after about three minutes of you talking with no
meeting audio at all, and almost always means the **System Audio Recording** permission
wasn't granted. **System Settings → Privacy & Security → Screen & System Audio Recording** →
enable "System Audio Recording Only" for the app you started the recording from
(Terminal/iTerm2, or the `.venv` Python when started from the menu bar/dashboard). Then
stop and start the recording again. Also check the meeting app isn't muted. In BlackHole
mode, the same warning means your Mac's output isn't routed to the Multi-Output Device.

**"[note] com.microsoft.teams2 is not running — capturing all system audio instead."**
Harmless. You set `tap_process` but started recording before Teams was open, so
Notetaker fell back to all system audio for this session. Start Teams first next time.

**My own voice is missing from the transcript.**
Check `capture_microphone: true` in the config, that **Microphone** permission is granted
(below), and that **System Settings → Sound → Input** points at your microphone or
headset rather than BlackHole. If your Bluetooth headset disconnects mid-meeting the
transcript gets a "[warning] microphone stopped delivering audio" line — reconnect it,
then stop and start the recording.

**Other people's words appear twice, once as `Me:`.**
Your microphone is picking up your speakers. Notetaker removes most of these echoes
(a `Me:` line that closely matches an `Others:` line at the same time), but garbled ones
can slip through. Use headphones, or set `capture_microphone: false` if you don't need
your side recorded.

**"system_audio: tap requires macOS 14.2+".**
Older macOS: use the [BlackHole fallback](#fallback-blackhole-loopback-instead-of-the-audio-tap).

**"BlackHole not found." / "BlackHole is installed but not active yet."** (BlackHole mode only)
Run `brew install blackhole-2ch`, reboot, then `notetaker init` again.

**No sound during the meeting after switching to the Multi-Output Device.** (BlackHole mode only)
Double check both your real output *and* BlackHole 2ch are checked in Audio MIDI
Setup's Multi-Output Device configuration. Also confirm the Multi-Output Device — not
plain BlackHole 2ch — is selected in **System Settings → Sound → Output**.

**Microphone permission was denied and I want to fix it.**
**System Settings → Privacy & Security → Microphone** → toggle on the terminal app you
run `notetaker` from (Terminal, iTerm2, etc.). You may need to quit and reopen the
terminal app afterward.

**`notetaker init` fails on the Python version check.**
```bash
brew install python@3.11
./install.sh
```

**Whisper model download seems stuck / fails.**
`notetaker init` downloads the model over the network the first time only; it's cached
under `~/.cache/huggingface` (faster-whisper's default cache) afterward. Check your
network connection and retry. If huggingface.co is blocked on this Mac, copy the model
from another machine and set `whisper_model_path` — see
[Locked-down / corporate Macs](#locked-down--corporate-macs).

**"apfel is installed but its service is not running."**
```bash
brew services start apfel
```

**"apple_local model is not available".**
Enable Apple Intelligence in **System Settings → Apple Intelligence & Siri**, set the
Device Language and Siri Language to the same supported language, and wait for the
on-device model to finish downloading (~3–4 GB), then re-run `notetaker init`.

**Summaries are "Summarization failed: …" for every long meeting.**
With the on-device model this used to happen when a transcript overflowed its context
window; current versions summarize in chunks. If you still see it, run
`notetaker resummarize <note-id>` once `apfel` is running, or switch to the Claude
provider for that note.

**Summarization failed but I don't want to lose the meeting.**
You won't — the transcript is always saved even if the AI call fails. Fix the issue
(e.g. `brew services start apfel`, or `notetaker set-api-key` if a Claude key was wrong or
expired) then run:
```bash
notetaker resummarize <note-id>
```

**`brew services` shows a service as "error" after `install.sh`.**
Check the log files (see [above](#running-the-menu-bar-app--dashboard-permanently-via-brew-services)).
The most common cause is the formula's baked-in path being stale after moving/re-cloning
the repo — re-run `./install.sh`, re-copy the formulas into the tap, and
`brew uninstall`/`brew install` again.

**Menu bar notifications never appear.**
Expected sometimes — the menu bar app runs as a plain Python script rather than a
signed `.app` bundle, so macOS notification delivery isn't guaranteed and Notetaker
treats it as best-effort (it won't crash if a notification fails to show). Use
`notetaker list` or the dashboard to confirm whether a note actually saved.

**Dashboard won't load / connection refused.**
Make sure you actually started it (`notetaker dashboard` or
`brew services start notetaker-dashboard`), and that you're browsing to
`http://127.0.0.1:8420` exactly (it refuses non-localhost hosts by design).

---

## Recording consent — read this

Notetaker captures your Mac's **system audio directly** (through a Core Audio tap, or
BlackHole in fallback mode) plus your microphone — Microsoft Teams (or any other meeting
app) has no idea this is happening, and it will **not** show its own "this meeting is
being recorded" indicator to other participants.
`notetaker start` prints a reminder every time, but it is on you to let participants
know you're recording, per your organization's policy and applicable local law before
you start.
