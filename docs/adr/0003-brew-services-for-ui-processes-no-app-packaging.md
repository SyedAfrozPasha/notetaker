# Run the menu bar app and dashboard via `brew services`; don't package as a Homebrew formula or macOS app bundle

The target machine is a corporate MacBook that disallows installing apps via the App Store or downloading installers directly from websites, but permits Homebrew. The menu bar app and web Dashboard are long-running background processes, so instead of hand-rolled LaunchAgent plists or asking the user to keep a terminal open, they're managed through `brew services` (a local/personal formula) — reusing a tool IT has already sanctioned, with auto-start and crash-restart as a side effect.

The core CLI install stays as-is (git clone + `install.sh` + pip/venv): it already doesn't touch the App Store or a downloaded installer, so there's no compliance reason to also wrap it in a Homebrew formula or a signed `.app` bundle. Packaging a full formula/tap is real ongoing maintenance (versioning, bottle builds) that only pays off if this is distributed to other people — revisit then.

## Considered Options

- Manual subcommands (`notetaker menubar`, `notetaker serve`) run/backgrounded by hand — rejected: no auto-start or crash-restart, and doesn't match the "always available, just like a local web app" requirement.
- Hand-rolled `launchd` LaunchAgent plists installed by `install.sh` — rejected: reinvents what `brew services` already provides, with no compliance benefit over it.
- Full Homebrew formula/tap or a native `.app` bundle for the whole tool — rejected for now: unnecessary packaging overhead for a single-user tool; the App Store/installer restriction doesn't apply to the pip/venv path anyway.

## Amendment (2026-09-13): one service, not two

Originally the menu bar app and the web Dashboard were two `brew services` entries (`notetaker-menubar`, `notetaker-dashboard`). In practice the menu bar service was never set up on the target machine — the tap/copy/install steps are manual and easy to skip — so a recording started from the Dashboard had nothing watching it in the menu bar, which is the one place you glance at mid-meeting.

Now `notetaker dashboard` runs both in **one process**: uvicorn on a daemon thread, the `rumps` menu bar app on the main thread (Cocoa requires it). If the Dashboard is up, the menu bar item is up; one `brew services` entry, one log, one lifetime (`brew services stop` sends SIGTERM and both go). `install.sh` generates only `notetaker-dashboard.rb` and retires a leftover `notetaker-menubar` service, since two services would mean two menu bar icons. `notetaker menubar` still exists as a menu-bar-only mode for anyone who never wants the browser UI, but it has no service of its own.

Considered and rejected:

- Dashboard spawns/tracks a separate menu bar process on a toggle — adds a PID file, duplicate-launch checks and a "is the other one running" state for no user-visible gain over one process.
- Fixing only the install so the separate service always runs — leaves the menu bar uncontrollable from the UI and still depends on `brew services` being set up correctly for two entries.
- Failure modes accepted: if port 8420 is taken, `notetaker dashboard` exits non-zero *before* showing the menu bar (a menu bar icon with a dead "Open Dashboard" behind it would be worse than a clear error; under `brew services` `keep_alive` it retries, same as before). No `--port`/`--no-menubar` flags until something needs them.
