---
status: accepted
---

# Local AI provider is opt-in, not default; chunking lives above the Provider interface

Added a second Provider implementation, `AppleLocalProvider`, which reaches Apple's on-device Foundation Models via apfel's local OpenAI-compatible server — giving a fully-offline path with no cloud call and no API key. Two decisions followed from evaluating it:

- **ClaudeProvider stays the default; `apple_local` is an explicit opt-in.** The on-device model is smaller and far more context-constrained (a combined input+output ceiling of 4096-8192 tokens) than Claude, and depends on hardware/OS (Apple Silicon, macOS 26+, Apple Intelligence enabled) that not every clone of this repo will have. Defaulting to it would trade "clone and run reliably" for privacy-by-default; opt-in keeps the zero-config path working everywhere while still making the fully-local option first-class, not a second-class afterthought.
- **The Provider interface stays a trivial one-shot `summarize(transcript) -> Summary` call for every implementation.** The map-reduce chunking needed to fit a long Transcript into AppleLocalProvider's small context window lives one layer above, in the summarizer, not inside the interface itself. This keeps Provider a clean seam rather than growing per-implementation capability flags — ClaudeProvider never needs to know chunking exists.

## Considered and rejected

Evaluated pairing apfel with a third-party tool ("Natively", an open-source "AI interview copilot") as a possible local-AI component. Rejected on two independent grounds: its own README markets stealth features (process disguise, screen-share-hiding, dock-hiding) explicitly for undetectable use during interviews and exams, which isn't something to build on; and even setting that aside, it's a full standalone Electron/Rust app with its own complete audio-capture-through-summarization pipeline, not a library or service this project could call into — adopting it would mean replacing this project, not extending it.
