# Capture meeting audio with a Core Audio process tap; keep BlackHole only as a fallback

The original design captured system audio through the BlackHole virtual loopback device. On the target machine (a corporate MacBook, headphones most of the time) that had three costs: BlackHole is a cask that runs a `.pkg` installer (admin password, reboot); it only hears audio routed through a Multi-Output Device that the user must re-select every time headphones connect, silently producing empty transcripts otherwise; and it is one more thing IT can refuse.

macOS 14.2 added Core Audio *process taps* (`AudioHardwareCreateProcessTap` + `CATapDescription`), which the project can already reach because `rumps` pulls in PyObjC. A tap sees every app's audio (or one app's, by bundle id) before it is routed to any output device, and wrapped in a private aggregate device it is just another input to PortAudio. Since `apfel` already pins the project to macOS 26+, the platform requirement is free. A spike (`docs/spikes/coreaudio_process_tap_spike.py`) and end-to-end runs proved capture through PortAudio, per-process isolation, and clean teardown.

Decision: `system_audio: tap` is the default; `system_audio: blackhole` keeps the loopback path for older macOS or an MDM profile that denies "System Audio Recording".

## Consequences and things that bit us

- The tap aggregate is **not bound to an output device** (no `master`/`subdevices` keys): binding would tie it to whatever output was current at start. Cost: it delivers no samples until some process has played audio, so `LiveCapture` pads silence instead of blocking on it.
- **Never `Pa_StopStream` the tap aggregate.** With the mic stream and the transcription worker thread alive, stopping deadlocked inside CoreAudio (`AudioDeviceStop` → `HALB_Mutex::Lock`, IO thread parked on a cycle semaphore). Destroying the aggregate and tap while streams run never hung; the recorder does that, then `os._exit`s.
- **Aggregates outlive the process.** A hard exit left a public aggregate in the system device list until destroyed. Teardown must always destroy the aggregate explicitly, even on the error path.
- Core Audio publishes a new device asynchronously (~0.5s); PortAudio must be re-initialized and polled before the aggregate is visible.
- Permission is "System Audio Recording Only" (TCC `kTCCServiceAudioCapture`). From Terminal the prompt is attributed to Terminal; under `brew services` (launchd) it may not appear at all — untested at the time of this decision; the README tells the user to run one CLI recording first.

## Considered Options

- Keep BlackHole only — rejected: admin/reboot install and the Multi-Output Device dance were the top usability failures for the headphones-on-a-corporate-Mac use case.
- ScreenCaptureKit audio capture — rejected: heavier API surface and a "Screen Recording" permission that reads worse to IT than audio-only.
- Aggregate bound to the built-in speakers as clock — rejected: delivered far fewer cycles than the unbound aggregate in testing and still nothing before first audio; no upside over padding.
