---
status: accepted
---

# Generic system-audio capture instead of Teams-specific integration

Despite targeting Microsoft Teams meetings, the Recorder captures audio at the OS level via BlackHole — it cannot distinguish Teams' audio from any other app's, and there is no Teams window or API integration to auto-detect meeting start/end or pull the meeting title. This is a deliberate MVP scope decision: Teams-specific integration (window automation or Graph API access) is materially more complex than a manual `notetaker start "<title>"` / `stop`, and OS-level capture already satisfies the stated need. Teams-awareness (auto-start/stop, auto-titling) remains a valid future addition, but should be treated as new scope — not a bug in the current Recorder.
