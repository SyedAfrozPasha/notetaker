# Notetaker — Full Installation & Usage Guide (macOS)

Notetaker is a macOS-only CLI that records a meeting's **system audio** (e.g. a
Microsoft Teams call), transcribes it locally with `faster-whisper`, and turns the
finished transcript into an AI-generated summary saved as a Markdown note.

This guide covers everything end-to-end: macOS-level setup (audio routing, permissions),
installing the tool, configuring an AI provider, day-to-day usage from the CLI, and the
optional menu bar app / web dashboard. See also [README.md](README.md) for the condensed
version and [CONTEXT.md](CONTEXT.md) for terminology.

---

## Table of contents

1. [How it works, in one paragraph](#how-it-works-in-one-paragraph)
2. [Requirements](#requirements)
3. [Step 1 — Install BlackHole (virtual audio loopback)](#step-1--install-blackhole-virtual-audio-loopback)
4. [Step 2 — Set up a Multi-Output Device](#step-2--set-up-a-multi-output-device-so-you-can-still-hear-the-meeting)
5. [Step 3 — Clone the repo and run the installer](#step-3--clone-the-repo-and-run-the-installer)
6. [Step 4 — Choose and configure an AI provider](#step-4--choose-and-configure-an-ai-provider)
7. [Step 5 — Run `notetaker init`](#step-5--run-notetaker-init)
8. [macOS permission prompts you'll see](#macos-permission-prompts-youll-see)
9. [Day-to-day usage (CLI)](#day-to-day-usage-cli)
10. [Where notes are stored, and their format](#where-notes-are-stored-and-their-format)
11. [Menu bar app](#menu-bar-app)
12. [Web dashboard](#web-dashboard)
13. [Running the menu bar app / dashboard permanently via `brew services`](#running-the-menu-bar-app--dashboard-permanently-via-brew-services)
14. [Configuration reference (`~/.notetaker/config.yaml`)](#configuration-reference-notetakerconfigyaml)
15. [Updating Notetaker](#updating-notetaker)
16. [Uninstalling](#uninstalling)
17. [Troubleshooting](#troubleshooting)
18. [Recording consent — read this](#recording-consent--read-this)

---

## How it works, in one paragraph

You run `notetaker start "Meeting title"` before or as your Teams call begins. A
background **Recorder** process reads audio from a virtual device called BlackHole
(which macOS is routing your system's sound into), splits it into ~10 second chunks, and
a **Transcriber** turns each chunk into timestamped text using a local Whisper model —
no audio ever leaves your machine at this stage. When you run `notetaker stop`, the full
transcript is sent to your chosen AI provider (Claude, by default) to produce a summary,
action items, and tags, and everything is saved as one Markdown file. If the Recorder
process ever crashes mid-meeting, the partial transcript is automatically salvaged into a
note the next time you run a command — you never lose the record of a meeting.

---

## Requirements

- **macOS.** This tool is macOS-only; the audio capture layer (BlackHole + Core Audio)
  has no other-platform equivalent in this project.
- **Apple Silicon or Intel Mac** — either works for the default Claude provider. The
  optional fully-local Apple Foundation Models provider requires **Apple Silicon and
  macOS 26+** (see [Step 4](#step-4--choose-and-configure-an-ai-provider)).
- **Homebrew** (https://brew.sh) — used to install BlackHole and, optionally, to run the
  menu bar app/dashboard as background services.
- **Python 3.10 or 3.11** specifically. Not 3.12/3.13 — `faster-whisper`'s `PyAV`
  dependency doesn't reliably build on newer Python. Install one if you don't have it:
  ```bash
  brew install python@3.11
  ```
- **git**, to clone the repository.
- An **Anthropic (Claude) API key** if using the default cloud provider — get one at
  https://console.anthropic.com/.

---

## Step 1 — Install BlackHole (virtual audio loopback)

Notetaker can't read a Teams call's audio directly — no macOS API exposes "the sound
this one app is playing." Instead it reads from **BlackHole**, a free virtual audio
device that you route your Mac's system output into.

```bash
brew install blackhole-2ch
```

**You must reboot your Mac after installing BlackHole.** Its audio driver only becomes
active after a restart — running `notetaker` before rebooting will report it as
"installed but not active."

After rebooting, you can confirm it's there:
- Open **System Settings → Sound → Output**, and you should see **BlackHole 2ch** listed
  as an available output device.
- Or open **Audio MIDI Setup** (Applications → Utilities → Audio MIDI Setup.app) and
  check it appears in the device list on the left.

---

## Step 2 — Set up a Multi-Output Device (so you can still hear the meeting)

If you simply switch your Mac's output to BlackHole, Notetaker will capture the audio
but **you won't hear anything** — BlackHole is a virtual device, not a speaker. The fix
is a **Multi-Output Device** that plays sound out of both your real speakers/headphones
*and* BlackHole simultaneously. This step is manual (macOS doesn't expose an API to
automate it) and only needs to be done once.

1. Open **Audio MIDI Setup** (Applications → Utilities → Audio MIDI Setup.app).
2. Click the **+** button in the bottom-left corner → **Create Multi-Output Device**.
3. In the device list on the right, check both:
   - Your normal output (e.g. **MacBook Pro Speakers**, or your headphones/AirPods)
   - **BlackHole 2ch**
4. (Optional but recommended) Right-click your normal output in the list and check
   **"Use this device for sound output"** so it stays your monitor/master clock, and
   rename the Multi-Output Device to something memorable like "Meeting Recording."
5. During a meeting you want to record: open the **Sound** menu bar item (or **System
   Settings → Sound → Output**) and select your new **Multi-Output Device** as the
   output. Switch back to your normal output when you're done, or you'll be routing
   through BlackHole for everything, including music.

**Tip:** you only need to do this switch while actually recording a meeting. It's fine
to leave your normal output selected the rest of the time.

---

## Step 3 — Clone the repo and run the installer

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
- Generates two personal Homebrew formula files under `Formula/` for the menu bar app
  and dashboard (see [Running via `brew services`](#running-the-menu-bar-app--dashboard-permanently-via-brew-services) — optional, skip if you only want the CLI).
- If you're re-running it after already having `notetaker-dashboard`/`notetaker-menubar`
  running as `brew services`, it stops them first, rebuilds the virtualenv, and restarts
  them automatically.

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

## Step 4 — Choose and configure an AI provider

Notetaker needs an AI provider to turn a transcript into a summary. You have two
options:

### Option A — Claude (default, cloud-based)

Recommended for most people; requires an Anthropic API key and sends the finished
transcript to Anthropic's API when you stop a recording (nothing is sent while
recording — only at `notetaker stop`, and only the transcript, never raw audio).

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

### Option B — Apple Foundation Models (opt-in, fully local, no API key, no network)

Everything — recording, transcription, *and* summarization — stays on your Mac. Trade-off:
requires newer hardware/OS, and summarization quality/speed differs from Claude.

Requirements:
- **Apple Silicon Mac**
- **macOS 26 or later**
- **Apple Intelligence enabled**: **System Settings → Apple Intelligence & Siri** →
  turn on Apple Intelligence (requires being signed into iCloud and the feature being
  available for your region/language).

Setup:
```bash
brew install apfel
brew services start apfel
```
Then edit `~/.notetaker/config.yaml` and set:
```yaml
ai_provider: apple_local
ai_model: apple-foundationmodel
```

You can also switch providers later from the [web dashboard's Settings page](#web-dashboard).

---

## Step 5 — Run `notetaker init`

This writes the default config file (if one doesn't exist yet), checks that BlackHole
and your AI provider are ready, and downloads the Whisper transcription model (only
happens once — it's cached locally afterwards).

```bash
notetaker init
```

Expected output looks like:
```
Wrote default config to /Users/you/.notetaker/config.yaml
BlackHole is installed and active.
ANTHROPIC_API_KEY is set.
Loading Whisper model 'base.en' (downloads on first run)...
Whisper model ready.
```

If something's missing, `init` tells you exactly what and how to fix it (e.g. "BlackHole
not found. Install it with: brew install blackhole-2ch", or "reboot your Mac, then
re-run `notetaker init`"). Re-run `notetaker init` any time after fixing a reported
issue — it's safe to run repeatedly and won't overwrite an existing config.

---

## macOS permission prompts you'll see

The first time you run `notetaker start`, expect the following system prompts. Approve
all of them — declining any will break recording or notifications.

| Prompt | When | What to do |
|---|---|---|
| **Microphone access** | First `notetaker start` | macOS treats reading BlackHole as "microphone" access at the OS permission layer. Click **Allow**. If you miss it, go to **System Settings → Privacy & Security → Microphone** and enable it for your Terminal app (Terminal.app, iTerm2, etc. — whichever you ran `notetaker` from). |
| **Notifications** (menu bar app only) | First time the menu bar app tries to notify you | Go to **System Settings → Notifications** and make sure notifications aren't blocked for your terminal/Python process if alerts on save-complete/failure don't appear. Because the menu bar app runs as an unbundled Python script rather than a signed `.app`, macOS notification delivery is best-effort — Notetaker won't crash if a notification silently doesn't show, but you can rely on `notetaker list`/the dashboard to confirm a note saved. |
| **Local network / incoming connections** (dashboard only) | First time `notetaker dashboard` binds a port | It's bound to `127.0.0.1` only (not reachable from other devices) but macOS may still ask about your terminal accepting incoming connections. Allow it. |

No **Screen Recording** or **Accessibility** permission is required — Notetaker only
captures audio, never the screen or keystrokes.

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
```

A typical session:
1. Switch your Mac's sound output to your **Multi-Output Device** (see Step 2).
2. Join/start the Teams meeting as normal.
3. `notetaker start "Sprint Planning"` — you'll see reminders about microphone access
   and about telling participants they're being recorded (Teams shows **no** indicator
   of its own — see [Recording consent](#recording-consent--read-this)).
4. Let the meeting run. Notetaker transcribes continuously in ~10s rolling chunks in the
   background; it doesn't block your terminal usage other than the one it's running in.
5. `notetaker stop` when the meeting ends. This shows a progress spinner while it
   finalizes the transcript and calls your AI provider, then prints the saved note's path.
6. Switch your sound output back to normal if you want.

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
<AI-generated summary text>

## Action Items
- [ ] Follow up with design on the new onboarding flow
- [ ] File a ticket for the flaky CI job

## Transcript
[00:00:03] ...
[00:00:14] ...
```

Alongside each note, a `<note-id>.transcript.txt` sidecar file is also kept (not
deleted) so `notetaker resummarize` can regenerate the summary later without needing to
re-transcribe anything — useful if you change AI providers or the first summarization
attempt failed.

If summarization fails (e.g. network issue, invalid API key), the transcript is **still
saved** — the Summary section will note the failure instead of losing the meeting
record. Fix the underlying issue, then run `notetaker resummarize <note-id>`.

---

## Menu bar app

An optional `rumps`-based menu bar app gives you one-click start/stop without opening a
terminal, a live elapsed-time display in the menu bar, and native notifications when a
recording saves, when summarization fails, or when a crash is detected and salvaged.

Run it directly (blocks the terminal it's run from — use this to try it out, or use
`brew services` below to run it permanently in the background):
```bash
notetaker menubar
```

You'll see a small icon in your macOS menu bar showing either **Notetaker** (idle) or
**⏺ MM:SS** (recording, live-updating). Click it for a **Start Recording** / **Stop
Recording** menu item (recordings started this way get an auto-generated title like
"Meeting 2026-09-11 09:30") and a **Quit** item.

---

## Web dashboard

A local-only web UI (bound to `127.0.0.1` — never reachable from other devices on your
network) at **http://127.0.0.1:8420**, for starting/stopping recordings from a browser
and for browsing, searching, editing, deleting, and resummarizing past notes, plus a
Settings page.

Run it directly:
```bash
notetaker dashboard
```
Then open http://127.0.0.1:8420 in your browser.

What it offers:
- **Home page** — live status (recording/idle, elapsed time, transcript preview so far),
  **Start**/**Stop**/**Cancel** buttons. Unlike the CLI, **Cancel** is available here —
  it discards an in-progress session with no note produced. The page polls its own
  status every 2 seconds, and — like the menu bar app — this is also when it checks for
  and salvages any crashed session.
- **Notes** (`/notes`) — browse and search all saved notes by text, tag, or date range.
- **Note detail** (`/notes/<id>`) — full note view with copy buttons, an **Edit** page
  (title, tags, summary text, action items), **Delete**, and **Resummarize**.
- **Settings** (`/settings`) — change `notes_dir`, `whisper_model`, and `ai_provider`
  without hand-editing the YAML file, and set/replace your Claude API key (masked
  display, live-validated against the Keychain).

---

## Running the menu bar app / dashboard permanently via `brew services`

Running `notetaker menubar`/`notetaker dashboard` directly is fine for trying them out,
but it ties up a terminal tab and won't restart itself if it crashes or after a reboot.
`install.sh` already generated two **personal, unpublished** Homebrew formulas
(`Formula/notetaker-dashboard.rb`, `Formula/notetaker-menubar.rb`) that wrap the same
`.venv/bin/notetaker <subcommand>` — they build nothing of their own, they just let
`brew services` supervise these two long-running processes the way it would any other
background service (see `docs/adr/0003-brew-services-for-ui-processes-no-app-packaging.md`
for the reasoning — this approach was chosen specifically to avoid `.app` packaging or
hand-rolled `launchd` plists).

One-time setup:
```bash
# Create a local (unpublished) tap. `brew tap-new` is used deliberately —
# tapping directly from a path/URL would silently drop the gitignored formula files.
brew tap-new syedafrozpasha/notetaker --no-git

# Copy the generated formulas into that tap:
cp Formula/*.rb "$(brew --repository syedafrozpasha/notetaker)/Formula/"

brew install syedafrozpasha/notetaker/notetaker-dashboard
brew install syedafrozpasha/notetaker/notetaker-menubar
```

Start/stop/check status:
```bash
brew services start notetaker-dashboard   # now always on at http://127.0.0.1:8420
brew services start notetaker-menubar     # menu bar icon always present

brew services list                        # check status of both
brew services stop notetaker-dashboard    # or notetaker-menubar
```

**After moving or re-cloning this repo:** the formulas bake in this machine's absolute
path to the repo, so re-run `./install.sh` (regenerates them), re-copy them into the tap
(the `cp` step above), then `brew uninstall` + `brew install` again for each service.

Logs (if a service silently isn't behaving as expected):
```bash
tail -f "$(brew --prefix)/var/log/notetaker-dashboard.log"
tail -f "$(brew --prefix)/var/log/notetaker-menubar.log"
```

---

## Configuration reference (`~/.notetaker/config.yaml`)

Created by `notetaker init` with these defaults:

```yaml
notes_dir: ~/notetaker-notes
whisper_model: base.en          # tiny/base/small/medium
ai_provider: claude
ai_model: claude-sonnet-5
api_key_env: ANTHROPIC_API_KEY  # read key from this env var; never stored in the file
```

| Key | Meaning | Notes |
|---|---|---|
| `notes_dir` | Where finished `.md` notes (and `.transcript.txt` sidecars) are saved | Editable via the dashboard's Settings page or by hand |
| `whisper_model` | Local Whisper model size used for transcription | Larger = more accurate but slower and more memory; `tiny`/`base`/`small`/`medium` — `.en`-suffixed variants (e.g. `base.en`) are faster/more accurate for English-only meetings |
| `ai_provider` | `claude` or `apple_local` | See [Step 4](#step-4--choose-and-configure-an-ai-provider) |
| `ai_model` | Model name passed to the provider | e.g. `claude-sonnet-5`, or `apple-foundationmodel` for the local provider |
| `api_key_env` | Name of the env var Notetaker falls back to if nothing is in the Keychain | Only relevant for `claude`; change this if you want to use a differently-named env var |

The Claude API key itself is **never** stored in this file — only in the macOS Keychain
(service name `notetaker`) or read from the environment at runtime. `notes_dir`,
`whisper_model`, and `ai_provider` can be changed with `update_config`-backed tools (the
dashboard's Settings page); `ai_model` and `api_key_env` currently require hand-editing
the YAML file directly.

---

## Updating Notetaker

```bash
cd notetaker-app
git pull
./install.sh
```
`install.sh` detects and safely restarts any running `notetaker-dashboard`/
`notetaker-menubar` `brew services` around the virtualenv rebuild. If a recording is
currently in progress, `install.sh` will refuse to run and tell you to `notetaker stop`
first — it never touches an in-progress session's files.

---

## Uninstalling

```bash
# Stop and remove the background services, if you set them up:
brew services stop notetaker-dashboard
brew services stop notetaker-menubar
brew uninstall syedafrozpasha/notetaker/notetaker-dashboard
brew uninstall syedafrozpasha/notetaker/notetaker-menubar
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

# Optionally remove BlackHole and the Multi-Output Device:
brew uninstall blackhole-2ch
# Then delete the Multi-Output Device in Audio MIDI Setup manually.
```

---

## Troubleshooting

**"BlackHole not found."**
Run `brew install blackhole-2ch`, then reboot, then `notetaker init` again.

**"BlackHole is installed but not active yet."**
You installed it but haven't rebooted since. Reboot, then re-run `notetaker init`.

**No sound during the meeting after switching to the Multi-Output Device.**
Double check both your real output *and* BlackHole 2ch are checked in Audio MIDI
Setup's Multi-Output Device configuration (Step 2). Also confirm the Multi-Output
Device — not plain BlackHole 2ch — is selected in **System Settings → Sound → Output**
during the meeting.

**Recording produces an empty/near-silent transcript.**
Usually means your Mac's output wasn't actually routed to the Multi-Output Device when
the meeting audio played, or the meeting app's own volume was muted. Confirm the sound
output selection *before* joining.

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
network connection and retry; consider `whisper_model: tiny` in `config.yaml` temporarily
if it's a bandwidth/disk-space issue.

**Summarization failed but I don't want to lose the meeting.**
You won't — the transcript is always saved even if the AI call fails. Fix the issue
(e.g. `notetaker set-api-key` if the key was wrong or expired) then run:
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

Notetaker captures your Mac's **system audio directly** through BlackHole — Microsoft
Teams (or any other meeting app) has no idea this is happening, and it will **not**
show its own "this meeting is being recorded" indicator to other participants.
`notetaker start` prints a reminder every time, but it is on you to let participants
know you're recording, per your organization's policy and applicable local law before
you start.
