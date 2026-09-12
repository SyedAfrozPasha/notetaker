# Run the menu bar app and dashboard via `brew services`; don't package as a Homebrew formula or macOS app bundle

The target machine is a corporate MacBook that disallows installing apps via the App Store or downloading installers directly from websites, but permits Homebrew. The menu bar app and web Dashboard are long-running background processes, so instead of hand-rolled LaunchAgent plists or asking the user to keep a terminal open, they're managed through `brew services` (a local/personal formula) — reusing a tool IT has already sanctioned, with auto-start and crash-restart as a side effect.

The core CLI install stays as-is (git clone + `install.sh` + pip/venv): it already doesn't touch the App Store or a downloaded installer, so there's no compliance reason to also wrap it in a Homebrew formula or a signed `.app` bundle. Packaging a full formula/tap is real ongoing maintenance (versioning, bottle builds) that only pays off if this is distributed to other people — revisit then.

## Considered Options

- Manual subcommands (`notetaker menubar`, `notetaker serve`) run/backgrounded by hand — rejected: no auto-start or crash-restart, and doesn't match the "always available, just like a local web app" requirement.
- Hand-rolled `launchd` LaunchAgent plists installed by `install.sh` — rejected: reinvents what `brew services` already provides, with no compliance benefit over it.
- Full Homebrew formula/tap or a native `.app` bundle for the whole tool — rejected for now: unnecessary packaging overhead for a single-user tool; the App Store/installer restriction doesn't apply to the pip/venv path anyway.
