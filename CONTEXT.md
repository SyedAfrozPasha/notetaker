# Notetaker CLI

A macOS CLI that records a meeting's system audio, transcribes it locally, and turns the result into an AI-generated summary saved as a Markdown note.

## Language

### Recording & transcription

**Meeting**:
The real-world conversation being recorded. Has a title and a duration, and exists independently of whether the tool is running.
_Avoid_: Call, conversation

**Session**:
The tool's tracked lifecycle of recording and transcribing one Meeting, from `start` to `stop`. A Session ends when it becomes a Note.
_Avoid_: Recording, run

**Recorder**:
The role that captures a Meeting's system audio (via BlackHole) and divides it into Chunks for the Transcriber.
_Avoid_: Capture process

**Chunk**:
A rolling ~10-second slice of captured audio, the unit the Recorder hands to the Transcriber. Chosen over true overlap-and-merge streaming so "live" means transcript segments appearing every ~10s, not word-by-word — see the design spec's Approach A rationale.
_Avoid_: Segment, clip, buffer

**Transcriber**:
The role that turns each Chunk into timestamped text, appending it to the Session's Transcript as the Meeting proceeds.
_Avoid_: Speech-to-text (that's the underlying technique, not this role)

**Transcript**:
The running, timestamped text produced by transcribing a Session's Chunks in order. Becomes the Transcript section of the finished Note.
_Avoid_: Text, log, minutes

**Salvaged transcript**:
A Transcript recovered from a Session whose Recorder stopped abnormally (crash or kill) before a normal `stop` — used as-is rather than discarding the Session's only record of the Meeting.
_Avoid_: Partial transcript (fine as a plain-English gloss, not as the standalone term)

**BlackHole**:
The third-party virtual loopback audio device the Recorder reads system audio from — the mechanism that makes capturing a Meeting possible without a physical patch cable. Not part of this project; the user installs and enables it as a setup step.
_Avoid_: Loopback device, virtual mic

### Summarization

**Summary**:
The AI-generated result for a finished Meeting — `{text, action_items, tags}` — produced by a Provider from a complete Transcript.
_Avoid_: Report, recap, minutes

**Provider**:
The abstraction (`summarize(transcript) -> Summary`) that turns a Transcript into a Summary. One clean seam, not a plugin system. Shipped variants: `ClaudeProvider` (default, cloud) and `AppleLocalProvider` (opt-in, fully local).
_Avoid_: Backend, model — a Provider may call an AI model, but is not the model itself

**Chunked summarization**:
A map-reduce process, applied above the Provider interface, that splits a long Transcript into pieces sized to fit a Provider's context window, summarizes each piece, then reduces the results into one Summary. Exists because AppleLocalProvider's underlying model has a small combined input+output context ceiling; a no-op in practice for ClaudeProvider. Distinct from a Recorder's Chunks — this operates on transcript text after the Session ends, not on audio during it.
_Avoid_: Batching, splitting — this term always means the map-reduce summarization step specifically

**apfel**:
The third-party local server AppleLocalProvider talks to; exposes Apple's on-device Foundation Models over an OpenAI-compatible HTTP endpoint. Not part of this project.
_Avoid_: Local model, Foundation Models — Foundation Models is Apple's underlying framework; apfel is the specific tool this project depends on to reach it

### Notes & storage

**Note**:
The single Markdown file (YAML frontmatter + Summary + action items + Transcript) that a finished Session becomes. One Note per Meeting.
_Avoid_: Record, entry

**Note ID**:
The identifier used to address a Note via `notetaker show <id>` — the date-and-title slug (`YYYY-MM-DD-slug`), with a time suffix appended only when two Meetings share a date and title slug. Not a separately stored/generated ID.
_Avoid_: Slug alone (the ID includes the date, not just the title portion), filename (the ID and the filename stem are the same thing, but "ID" is the term to use when talking about addressing a Note)
