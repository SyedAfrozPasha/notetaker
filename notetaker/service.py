import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from faster_whisper import download_model
from keyring.errors import KeyringError

from notetaker.config import (
    CONFIG_PATH,
    Config,
    ConfigError,
    load_config,
    update_config as config_update_config,
    write_default_config,
)
from notetaker.credentials import get_provider_credential, mask_credential, set_provider_credential
from notetaker.notes import (
    NoteMeta,
    find_note_path,
    list_notes,
    parse_note_body,
    parse_note_meta,
    read_note_body,
    rewrite_note_summary,
    search_notes as notes_search_notes,
    update_note_fields,
    write_note,
)
from notetaker.recorder import (
    BlackHoleStatus,
    check_blackhole,
    check_microphone_routing,
    check_output_routing,
    find_blackhole_device_index,
)
from notetaker.systemaudio import tap_support_problem
from notetaker.summarizer import Summary, check_apple_local_preflight, get_provider, summarize_transcript, validate_claude_api_key
from notetaker.transcriber import Transcriber

SESSION_FILE_NAME = "current_session.json"


class ServiceError(Exception):
    """A user-facing failure in a service operation; str(exc) is the display message."""


@dataclass
class SessionInfo:
    pid: int
    title: str
    start_time: datetime
    session_dir: Path
    tags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # audio-routing warnings from start; not persisted


def session_file_path(config_dir: Path) -> Path:
    return config_dir / SESSION_FILE_NAME


def pid_alive(pid: int) -> bool:
    """Whether `pid` is a live process. A Recorder spawned by a long-lived UI
    process (menu bar app, dashboard) is that process's child, and an exited
    child stays a zombie — for which `kill(pid, 0)` still succeeds — until
    someone reaps it. Reap first so a crashed Recorder reads as dead.
    """
    try:
        reaped_pid, _ = os.waitpid(pid, os.WNOHANG)
        if reaped_pid == pid:
            return False
    except ChildProcessError:
        pass  # not our child (CLI process, or already reaped): fall through
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _claim_session_file(config_dir: Path) -> tuple[Path, Path] | None:
    """Atomically claims current_session.json so no other finalizer
    (stop_session, cancel_session, check_and_salvage_orphan) can act on the
    same session concurrently. Returns (session_file, claim_path), or None
    if there was nothing to claim — someone else already claimed or removed
    it first.
    """
    session_file = session_file_path(config_dir)
    claim_path = session_file.with_suffix(".salvaging")
    try:
        session_file.rename(claim_path)
    except FileNotFoundError:
        return None
    return session_file, claim_path


def _release_claim(session_file: Path, claim_path: Path, *, restore: bool) -> None:
    """Releases a claim taken by _claim_session_file. On success (restore=False)
    the claim is simply dropped. On failure (restore=False is not passed —
    restore=True) the claim is put back under its original name so a future
    attempt can retry — UNLESS a new session has since been started at that
    path (session_file now exists again), in which case restoring would
    silently clobber that live session's pointer. In that case the stale
    claim is just dropped instead; the underlying session directory this
    claim pointed to is untouched on disk (stop_session/cancel_session only
    delete it on success), so it remains manually recoverable.
    """
    if not restore:
        claim_path.unlink(missing_ok=True)
        return
    if session_file.exists():
        claim_path.unlink(missing_ok=True)
    else:
        try:
            claim_path.rename(session_file)
        except FileNotFoundError:
            pass


def start_session(title: str, config: Config, config_dir: Path, tags: list[str] | None = None) -> SessionInfo:
    session_file = session_file_path(config_dir)
    if session_file.with_suffix(".salvaging").exists():
        raise ServiceError("a session is currently being stopped. Try again in a moment.")
    if session_file.exists():
        try:
            raw = json.loads(session_file.read_text())
            if pid_alive(raw["pid"]):
                raise ServiceError("a session is already running. Run `notetaker stop` first.")
        except (ValueError, KeyError, TypeError, OSError):
            pass

    warnings: list[str] = []
    if config.system_audio == "tap":
        problem = tap_support_problem()
        if problem:
            raise ServiceError(problem)
        system_args = ["--system-audio", "tap"]
        if config.tap_process:
            system_args += ["--tap-process", config.tap_process]
    else:
        status = check_blackhole()
        if status != BlackHoleStatus.ACTIVE:
            raise ServiceError("BlackHole is not active. Run `notetaker init` for setup instructions.")
        device_index = find_blackhole_device_index()
        system_args = ["--system-audio", "blackhole", "--system-device", str(device_index)]
        routing_warning = check_output_routing()
        if routing_warning:
            warnings.append(routing_warning)

    mic_arg = "none"
    if config.capture_microphone:
        mic_warning = check_microphone_routing()
        if mic_warning:
            warnings.append(mic_warning)
        else:
            mic_arg = "default"

    start_time = datetime.now()
    session_dir = config_dir / "sessions" / start_time.strftime("%Y%m%d-%H%M%S")
    session_dir.mkdir(parents=True, exist_ok=True)

    log_path = session_dir / "recorder.log"
    log_file = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "notetaker.recorder",
                "--session-dir", str(session_dir),
                "--model", config.whisper_model,
                "--model-path", config.whisper_model_path or "",
                "--mic", mic_arg,
                *system_args,
            ],
            start_new_session=True,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    finally:
        log_file.close()

    time.sleep(0.5)
    if proc.poll() is not None:
        raise ServiceError(f"recorder failed to start — see {log_path} for details")

    _write_session_file(
        session_file,
        json.dumps(
            {
                "pid": proc.pid,
                "title": title,
                "start_time": start_time.isoformat(),
                "session_dir": str(session_dir),
                "tags": tags or [],
            }
        )
    )
    return SessionInfo(
        pid=proc.pid, title=title, start_time=start_time, session_dir=session_dir, tags=tags or [], warnings=warnings
    )


def read_active_session(config_dir: Path) -> SessionInfo:
    session_file = session_file_path(config_dir)
    if not session_file.exists():
        raise ServiceError("no active session.")
    try:
        raw = json.loads(session_file.read_text())
        return SessionInfo(
            pid=raw["pid"],
            title=raw["title"],
            start_time=datetime.fromisoformat(raw["start_time"]),
            session_dir=Path(raw["session_dir"]),
            tags=raw.get("tags") or [],
        )
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise ServiceError(
            f"session file at {session_file} is corrupt or unreadable. "
            f"Check ~/.notetaker/sessions/ manually for a salvageable transcript, "
            f"then remove {session_file} to reset."
        ) from exc


def _write_session_file(session_file: Path, content: str) -> None:
    """Atomic write, so a concurrent poller never reads a half-written file."""
    tmp_path = session_file.with_suffix(".tmp")
    tmp_path.write_text(content)
    os.replace(tmp_path, session_file)


def _terminate_recorder(pid: int, grace_seconds: int = 30) -> None:
    """SIGTERM the Recorder and wait for it to finish its last chunk. If it
    is still alive after the grace period, SIGKILL it: the caller is about
    to read the transcript and delete the session dir, which must not
    happen underneath a still-running Recorder.
    """
    if not pid_alive(pid):
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(grace_seconds):
        if not pid_alive(pid):
            return
        time.sleep(1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return
    for _ in range(5):
        if not pid_alive(pid):
            return
        time.sleep(1)


def _summarize_or_fallback(
    transcript: str, config: Config, on_phase: Callable[[str], None] | None = None
) -> Summary:
    if not transcript.strip():
        return Summary(text="No audio was captured for this session.", action_items=[], tags=[])

    def _progress(done: int, total: int) -> None:
        if on_phase and total > 1:
            on_phase(f"Summarizing... chunk {done} of {total}")

    try:
        provider = get_provider(config)
        return summarize_transcript(transcript, provider, on_progress=_progress)
    except Exception as exc:
        return Summary(text=f"Summarization failed: {exc}", action_items=[], tags=[])


def stop_session(
    info: SessionInfo,
    config: Config,
    config_dir: Path,
    end_time: datetime | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> Path:
    claim = _claim_session_file(config_dir)
    if claim is None:
        raise ServiceError("no active session.")
    session_file, claim_path = claim
    try:
        return _finalize_stop(info, config, claim_path, end_time=end_time, on_phase=on_phase)
    except Exception:
        _release_claim(session_file, claim_path, restore=True)
        raise


def _finalize_stop(
    info: SessionInfo,
    config: Config,
    claim_path: Path,
    end_time: datetime | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> Path:
    if on_phase:
        on_phase("Stopping recorder...")
    _terminate_recorder(info.pid)

    transcript_path = info.session_dir / "transcript.txt"
    transcript = transcript_path.read_text() if transcript_path.exists() else ""
    transcript_lines = transcript.splitlines()

    if end_time is None:
        end_time = datetime.now()
    duration_minutes = int((end_time - info.start_time).total_seconds() // 60)

    if on_phase:
        on_phase("Summarizing...")
    summary = _summarize_or_fallback(transcript, config, on_phase=on_phase)
    if info.tags:
        merged_tags = list(dict.fromkeys(info.tags + summary.tags))
        summary = replace(summary, tags=merged_tags)

    note_path = write_note(
        config.notes_dir, info.title, info.start_time, duration_minutes, summary, transcript_lines
    )
    transcript_sidecar_path = note_path.parent / f"{note_path.stem}.transcript.txt"
    transcript_sidecar_path.write_text(transcript)

    shutil.rmtree(info.session_dir, ignore_errors=True)
    claim_path.unlink(missing_ok=True)

    return note_path


def cancel_session(info: SessionInfo, config_dir: Path) -> None:
    claim = _claim_session_file(config_dir)
    if claim is None:
        raise ServiceError("no active session.")
    session_file, claim_path = claim
    try:
        _terminate_recorder(info.pid)
        shutil.rmtree(info.session_dir, ignore_errors=True)
        claim_path.unlink(missing_ok=True)
    except Exception:
        _release_claim(session_file, claim_path, restore=True)
        raise


def list_all_notes(config: Config) -> list[NoteMeta]:
    return list_notes(config.notes_dir)


def search_notes(
    config: Config,
    *,
    query: str | None = None,
    tag: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[NoteMeta]:
    return notes_search_notes(config.notes_dir, query=query, tag=tag, start_date=start_date, end_date=end_date)


def get_note_body(config: Config, note_id: str) -> str:
    path = find_note_path(config.notes_dir, note_id)
    if path is None:
        raise ServiceError(f"no note found with id '{note_id}'.")
    return read_note_body(path)


def delete_note(config: Config, note_id: str) -> None:
    note_path = find_note_path(config.notes_dir, note_id)
    if note_path is None:
        raise ServiceError(f"no note found with id '{note_id}'.")
    sidecar_path = note_path.parent / f"{note_path.stem}.transcript.txt"
    note_path.unlink()
    sidecar_path.unlink(missing_ok=True)


def get_live_transcript_preview(info: SessionInfo) -> str:
    transcript_path = info.session_dir / "transcript.txt"
    return transcript_path.read_text() if transcript_path.exists() else ""


def resummarize_note(config: Config, note_id: str) -> Path:
    note_path = find_note_path(config.notes_dir, note_id)
    if note_path is None:
        raise ServiceError(f"no note found with id '{note_id}'.")
    sidecar_path = note_path.parent / f"{note_path.stem}.transcript.txt"
    if not sidecar_path.exists():
        raise ServiceError(
            f"no persisted transcript found for '{note_id}' — resummarize needs the "
            f"{sidecar_path.name} sidecar, which this note doesn't have."
        )
    try:
        parse_note_meta(note_path)
    except (ValueError, KeyError) as exc:
        raise ServiceError(f"note '{note_id}' could not be parsed and cannot be resummarized: {exc}") from exc
    transcript = sidecar_path.read_text()
    if not transcript.strip():
        summary = Summary(text="No audio was captured for this session.", action_items=[], tags=[])
    else:
        try:
            provider = get_provider(config)
            summary = summarize_transcript(transcript, provider)
        except Exception as exc:
            raise ServiceError(f"resummarization failed: {exc}") from exc
    rewrite_note_summary(note_path, summary, transcript.splitlines())
    return note_path


def update_note(
    config: Config,
    note_id: str,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
    summary_text: str | None = None,
    action_items: list[str] | None = None,
) -> Path:
    """Edits only the given fields of an existing Note — a parameter left
    as None is unchanged, so pass `tags=[]` (not None) to clear tags, and
    likewise `action_items=[]` to clear action items. Never renames the
    Note (see CONTEXT.md's Note ID definition) and never touches its
    Transcript section.
    """
    note_path = find_note_path(config.notes_dir, note_id)
    if note_path is None:
        raise ServiceError(f"no note found with id '{note_id}'.")
    try:
        update_note_fields(note_path, title=title, tags=tags, summary_text=summary_text, action_items=action_items)
    except (ValueError, KeyError) as exc:
        raise ServiceError(f"note '{note_id}' could not be parsed and cannot be edited: {exc}") from exc
    return note_path


@dataclass
class NoteDetail:
    note_id: str
    title: str
    date: datetime
    duration_minutes: int
    tags: list[str]
    summary_text: str
    action_items: list[str]
    transcript: str
    path: Path


def get_note_detail(config: Config, note_id: str) -> NoteDetail:
    note_path = find_note_path(config.notes_dir, note_id)
    if note_path is None:
        raise ServiceError(f"no note found with id '{note_id}'.")
    try:
        meta = parse_note_meta(note_path)
        summary_text, action_items, transcript = parse_note_body(note_path)
    except (ValueError, KeyError) as exc:
        raise ServiceError(f"note '{note_id}' could not be parsed: {exc}") from exc
    return NoteDetail(
        note_id=meta.note_id,
        title=meta.title,
        date=meta.date,
        duration_minutes=meta.duration_minutes,
        tags=meta.tags,
        summary_text=summary_text,
        action_items=action_items,
        transcript=transcript,
        path=meta.path,
    )


@dataclass
class SetupStatus:
    blackhole: BlackHoleStatus
    provider_ready: bool
    provider_problems: list[str]
    system_audio: str = "blackhole"  # config.system_audio
    system_audio_problem: str | None = None  # tap mode only: why taps can't be used here


def initialize_config(config_path: Path) -> bool:
    return write_default_config(config_path)


def get_config(config_path: Path = CONFIG_PATH) -> Config:
    try:
        return load_config(config_path)
    except ConfigError as exc:
        raise ServiceError(str(exc)) from exc


def update_config(updates: dict, config_path: Path = CONFIG_PATH) -> Config:
    try:
        return config_update_config(updates, config_path)
    except ConfigError as exc:
        raise ServiceError(str(exc)) from exc


def check_setup(config: Config) -> SetupStatus:
    blackhole = check_blackhole()
    audio = {"system_audio": config.system_audio}
    if config.system_audio == "tap":
        audio["system_audio_problem"] = tap_support_problem()
    if config.ai_provider == "claude":
        try:
            keychain_credential = get_provider_credential(config.api_key_env)
        except KeyringError:
            keychain_credential = None
        if not (keychain_credential or os.environ.get(config.api_key_env)):
            return SetupStatus(
                blackhole=blackhole,
                provider_ready=False,
                provider_problems=[
                    f"No credential found for {config.api_key_env}. Run `notetaker set-api-key <key>`, "
                    "or export it as an environment variable, then re-run `notetaker init`."
                ],
                **audio,
            )
        return SetupStatus(blackhole=blackhole, provider_ready=True, provider_problems=[], **audio)
    if config.ai_provider == "apple_local":
        problems = check_apple_local_preflight()
        return SetupStatus(blackhole=blackhole, provider_ready=not problems, provider_problems=problems, **audio)
    raise ServiceError(f"Unknown ai_provider '{config.ai_provider}'.")


def whisper_model_is_cached(config: Config) -> bool:
    """Whether `ensure_whisper_model` can finish without fetching from Hugging
    Face — i.e. the named model is already in the local cache."""
    if config.whisper_model_path:
        return True
    try:
        download_model(config.whisper_model, local_files_only=True)
    except Exception:
        return False
    return True


def _download_error(config: Config, exc: Exception) -> ServiceError:
    return ServiceError(
        f"could not download the Whisper model '{config.whisper_model}' from Hugging Face: {exc}. "
        "If this machine cannot reach huggingface.co, copy a faster-whisper model directory from another "
        "machine (e.g. ~/.cache/huggingface/hub/models--Systran--faster-whisper-base.en/snapshots/<id>/) "
        "and set whisper_model_path in ~/.notetaker/config.yaml to that directory."
    )


def ensure_whisper_model(config: Config, on_phase: Callable[[str], None] | None = None) -> None:
    """Loads (and on first run downloads) the Whisper model, reporting each
    phase through `on_phase` so the UI can show what it is waiting on — the
    download is a one-time multi-hundred-MB fetch that faster-whisper runs
    silently. On a machine that cannot reach Hugging Face the download is
    the step that fails, so the error points at the offline alternative."""
    report = on_phase or (lambda phase: None)
    if config.whisper_model_path:
        report(f"Loading Whisper model from '{config.whisper_model_path}'...")
    elif whisper_model_is_cached(config):
        report(f"Loading Whisper model '{config.whisper_model}' (already downloaded)...")
    else:
        report(f"Downloading Whisper model '{config.whisper_model}' from Hugging Face (one-time)...")
        try:
            download_model(config.whisper_model)
        except Exception as exc:
            raise _download_error(config, exc) from exc
        report(f"Loading Whisper model '{config.whisper_model}'...")
    try:
        Transcriber(config.whisper_model, model_path=config.whisper_model_path)
    except Exception as exc:
        if config.whisper_model_path:
            raise ServiceError(
                f"could not load the Whisper model from whisper_model_path '{config.whisper_model_path}': {exc}. "
                "It must be a faster-whisper (CTranslate2) model directory containing model.bin, config.json, "
                "tokenizer.json and vocabulary.txt."
            ) from exc
        raise _download_error(config, exc) from exc


def check_and_salvage_orphan(config: Config, config_dir: Path) -> Path | None:
    """Detects and salvages an Orphaned session: a Session whose Recorder died
    without a matching `stop` (see CONTEXT.md). Uses the same claim as
    `stop_session`/`cancel_session` (see `_claim_session_file`), so this
    poller can never race a user-initiated stop or cancel that is still in
    flight — whichever caller claims the session file first proceeds; the
    other finds nothing to claim and returns/no-ops. If salvaging itself
    fails, the claim is released so a future attempt can retry rather than
    losing the orphan.
    """
    try:
        info = read_active_session(config_dir)
    except ServiceError:
        return None
    if pid_alive(info.pid):
        return None
    claim = _claim_session_file(config_dir)
    if claim is None:
        return None
    session_file, claim_path = claim
    try:
        claimed_pid = json.loads(claim_path.read_text()).get("pid")
    except (ValueError, AttributeError, OSError):
        claimed_pid = None
    if claimed_pid != info.pid:
        # A stop+start completed between our read and our claim: this is a
        # different, live Session. Hand its file back untouched.
        _release_claim(session_file, claim_path, restore=True)
        return None
    transcript_path = info.session_dir / "transcript.txt"
    if transcript_path.exists():
        end_time = datetime.fromtimestamp(transcript_path.stat().st_mtime)
    else:
        end_time = info.start_time
    try:
        return _finalize_stop(info, config, claim_path, end_time=end_time)
    except Exception:
        _release_claim(session_file, claim_path, restore=True)
        raise


def get_current_session_status(config_dir: Path) -> SessionInfo | None:
    """Returns the currently active Session's info if one is genuinely
    running (a live Recorder process), or None otherwise (no session file, a
    corrupt one, or a dead pid). Never raises — for a poller (e.g. a menu bar
    app) that wants to display status without handling exceptions for the
    common "nothing is recording" case.
    """
    try:
        info = read_active_session(config_dir)
    except ServiceError:
        return None
    if not pid_alive(info.pid):
        return None
    return info


def save_provider_credential(config: Config, api_key: str) -> None:
    if config.ai_provider != "claude":
        raise ServiceError(
            f"Setting a credential is only supported for the 'claude' provider (current: '{config.ai_provider}')."
        )
    if not validate_claude_api_key(api_key):
        raise ServiceError("That API key was rejected by Anthropic's API — check it and try again.")
    try:
        set_provider_credential(config.api_key_env, api_key)
    except KeyringError as exc:
        raise ServiceError(f"Could not save to the macOS Keychain: {exc}") from exc


def get_masked_provider_credential(config: Config) -> str | None:
    try:
        value = get_provider_credential(config.api_key_env)
    except KeyringError:
        return None
    if value is None:
        return None
    return mask_credential(value)
