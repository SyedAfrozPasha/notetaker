# Notetaker CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `notetaker` macOS CLI end to end: record a meeting's system audio, transcribe it locally, summarize it (cloud Claude by default, fully-local Apple Foundation Models via `apfel` as an opt-in), and save it as a Markdown note.

**Architecture:** A flat Python package (`notetaker/`) with one module per responsibility, coordinating entirely through `~/.notetaker/` on the filesystem (PID/session file, per-session working directory, `config.yaml`) — no daemon socket, no database. Two run modes: a short-lived foreground CLI (`init`/`start`/`stop`/`list`/`show`) and a detached background recorder process spawned by `start` and stopped via `SIGTERM`.

**Tech Stack:** Python 3.10/3.11, Typer (CLI), PyYAML (config/frontmatter), `sounddevice` + stdlib `wave` (audio capture), `faster-whisper` (local transcription), `anthropic` SDK (Claude provider), stdlib `urllib` (Apple-local provider — no extra HTTP dependency), `pytest` + `pytest-mock` (tests).

**Spec:** [docs/superpowers/specs/2026-09-11-notetaker-cli-design.md](../specs/2026-09-11-notetaker-cli-design.md) — read alongside this plan. Domain vocabulary: [CONTEXT.md](../../../CONTEXT.md). Rationale for two decisions this plan implements: [docs/adr/0001](../../adr/0001-generic-audio-capture-not-teams-specific.md), [docs/adr/0002](../../adr/0002-local-provider-opt-in-chunking-above-interface.md).

## Global Constraints

- macOS only for the MVP (spec's Non-goals).
- Python 3.10 or 3.11 only — `faster-whisper`'s PyAV dependency has documented install breakage on 3.13, and no 3.12 support is confirmed. `install.sh` must check this and fail with a clear message rather than let `pip install` fail deep in a build error.
- `pyproject.toml` pins exact versions of `faster-whisper`, `ctranslate2`, and `av` (the proven-fragile trio); every other dependency uses normal lower-bound ranges.
- No plugin-loading system for AI providers — `Provider` stays a two-implementation seam (`ClaudeProvider`, `AppleLocalProvider`), not a discovery mechanism.
- Chunked polling (~10s rolling audio chunks), not true overlap-merge streaming transcription — see spec's "Approach A" rationale.
- A crashed recorder or a failed AI summarization call must never lose the meeting record — partial transcripts are always salvaged and saved, with the summary section noting the failure if summarization fails.
- `ANTHROPIC_API_KEY` (or whatever `api_key_env` names) is read from the environment and never written to `config.yaml` or prompted for.
- `notetaker init` never overwrites an existing `config.yaml`; `install.sh` is safe to re-run (recreates its venv cleanly).
- `apple_local` preflight checks (apfel installed, service running, macOS ≥ 26, Apple Intelligence enabled) run only when `ai_provider: apple_local` is actually configured — never unconditionally.
- Note frontmatter fields are exactly: `title`, `date`, `duration_minutes`, `tags` (per spec's Note File Format).

---

### Task 1: Project scaffolding + `config.py`

**Files:**
- Create: `pyproject.toml`
- Create: `notetaker/__init__.py`
- Create: `notetaker/config.py`
- Test: `tests/test_config.py`
- Create: `tests/__init__.py`

**Interfaces:**
- Produces: `notetaker.config.Config` (dataclass: `notes_dir: Path`, `whisper_model: str`, `ai_provider: str`, `ai_model: str`, `api_key_env: str`), `notetaker.config.ConfigError(Exception)`, `notetaker.config.CONFIG_DIR: Path`, `notetaker.config.CONFIG_PATH: Path`, `notetaker.config.write_default_config(path: Path = CONFIG_PATH) -> bool`, `notetaker.config.load_config(path: Path = CONFIG_PATH) -> Config`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "notetaker"
version = "0.1.0"
description = "Records, transcribes, and summarizes Microsoft Teams meetings locally on macOS."
requires-python = ">=3.10,<3.12"
dependencies = [
    "typer>=0.12",
    "pyyaml>=6.0",
    "sounddevice>=0.4",
    "numpy>=1.26",
    "faster-whisper==1.0.3",
    "ctranslate2==4.3.1",
    "av==12.3.0",
    "anthropic>=0.34",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.14"]

[project.scripts]
notetaker = "notetaker.cli:app"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["notetaker"]
```

Note for the implementer: verify `faster-whisper==1.0.3` / `ctranslate2==4.3.1` / `av==12.3.0` are still the latest mutually-compatible stable releases on PyPI before committing (`pip index versions faster-whisper` etc.) — bump all three together if not, and smoke-test with `python -c "import faster_whisper"` in a scratch venv. Don't leave them unpinned either way.

- [ ] **Step 2: Create empty package files**

`notetaker/__init__.py` and `tests/__init__.py` — both empty.

- [ ] **Step 3: Write the failing tests**

```python
# tests/test_config.py
import pytest
from notetaker.config import Config, ConfigError, load_config, write_default_config


def test_write_default_config_creates_file(tmp_path):
    path = tmp_path / "config.yaml"
    assert write_default_config(path) is True
    assert path.exists()
    assert "notes_dir" in path.read_text()


def test_write_default_config_is_idempotent(tmp_path):
    path = tmp_path / "config.yaml"
    write_default_config(path)
    path.write_text("notes_dir: /custom\nwhisper_model: tiny\nai_provider: claude\nai_model: x\napi_key_env: Y\n")
    assert write_default_config(path) is False
    assert "custom" in path.read_text()


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")


def test_load_config_parses_valid_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/notetaker-notes\n"
        "whisper_model: base.en\n"
        "ai_provider: claude\n"
        "ai_model: claude-sonnet-5\n"
        "api_key_env: ANTHROPIC_API_KEY\n"
    )
    config = load_config(path)
    assert isinstance(config, Config)
    assert config.whisper_model == "base.en"
    assert config.notes_dir.is_absolute()


def test_load_config_missing_keys_raises(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("notes_dir: ~/x\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_rejects_unknown_provider(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/x\nwhisper_model: base.en\nai_provider: bogus\n"
        "ai_model: x\napi_key_env: Y\n"
    )
    with pytest.raises(ConfigError):
        load_config(path)
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pip install -e ".[dev]" && pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notetaker.config'`

- [ ] **Step 5: Write `notetaker/config.py`**

```python
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_DIR = Path.home() / ".notetaker"
CONFIG_PATH = CONFIG_DIR / "config.yaml"

DEFAULT_CONFIG_YAML = """\
notes_dir: ~/notetaker-notes
whisper_model: base.en          # tiny/base/small/medium
ai_provider: claude
ai_model: claude-sonnet-5
api_key_env: ANTHROPIC_API_KEY  # read key from this env var; never stored in the file
"""

REQUIRED_KEYS = ["notes_dir", "whisper_model", "ai_provider", "ai_model", "api_key_env"]
VALID_PROVIDERS = ("claude", "apple_local")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    notes_dir: Path
    whisper_model: str
    ai_provider: str
    ai_model: str
    api_key_env: str


def write_default_config(path: Path = CONFIG_PATH) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_YAML)
    return True


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise ConfigError(f"No config found at {path}. Run `notetaker init` first.")
    raw = yaml.safe_load(path.read_text()) or {}
    missing = [key for key in REQUIRED_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"Config at {path} is missing keys: {', '.join(missing)}")
    if raw["ai_provider"] not in VALID_PROVIDERS:
        raise ConfigError(
            f"Unknown ai_provider '{raw['ai_provider']}' — expected one of {VALID_PROVIDERS}."
        )
    return Config(
        notes_dir=Path(raw["notes_dir"]).expanduser(),
        whisper_model=raw["whisper_model"],
        ai_provider=raw["ai_provider"],
        ai_model=raw["ai_model"],
        api_key_env=raw["api_key_env"],
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml notetaker/__init__.py notetaker/config.py tests/__init__.py tests/test_config.py
git commit -m "feat: add config loading/validation"
```

---

### Task 2: `summarizer.py` — `Summary`, `Provider`, chunking + map-reduce

**Files:**
- Create: `notetaker/summarizer.py`
- Test: `tests/test_summarizer.py`

**Interfaces:**
- Produces: `Summary` (dataclass: `text: str`, `action_items: list[str]`, `tags: list[str]`), `Provider` (Protocol: `summarize(self, transcript: str) -> Summary`), `estimate_tokens(text: str) -> int`, `chunk_transcript(transcript: str, max_tokens: int = 3000) -> list[str]`, `summarize_transcript(transcript: str, provider: Provider, chunk_token_limit: int = 3000) -> Summary`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_summarizer.py
from notetaker.summarizer import (
    Summary,
    chunk_transcript,
    estimate_tokens,
    summarize_transcript,
)


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def summarize(self, transcript: str) -> Summary:
        self.calls.append(transcript)
        return self.responses.pop(0)


def test_estimate_tokens_roughly_four_chars_per_token():
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("") == 1


def test_chunk_transcript_single_chunk_when_short():
    transcript = "[00:00:01] hello\n[00:00:05] world"
    assert chunk_transcript(transcript, max_tokens=1000) == [transcript]


def test_chunk_transcript_splits_when_over_limit():
    lines = [f"[00:00:{i:02d}] " + ("word " * 20) for i in range(10)]
    transcript = "\n".join(lines)
    chunks = chunk_transcript(transcript, max_tokens=50)
    assert len(chunks) > 1
    assert "\n".join(chunks).replace("\n", " ").split() == transcript.replace("\n", " ").split()


def test_summarize_transcript_single_chunk_calls_provider_once():
    provider = FakeProvider([Summary(text="s", action_items=["a"], tags=["t"])])
    result = summarize_transcript("short transcript", provider, chunk_token_limit=1000)
    assert result.text == "s"
    assert len(provider.calls) == 1


def test_summarize_transcript_reduces_multiple_chunks():
    lines = [f"[00:00:{i:02d}] " + ("word " * 20) for i in range(10)]
    transcript = "\n".join(lines)
    provider = FakeProvider([
        Summary(text="partial1", action_items=["a1"], tags=["t1"]),
        Summary(text="partial2", action_items=["a2"], tags=["t1", "t2"]),
        Summary(text="final", action_items=["a3"], tags=["t3"]),
    ])
    result = summarize_transcript(transcript, provider, chunk_token_limit=50)
    assert result.text == "final"
    assert result.tags == ["t1", "t2", "t3"]
    assert result.action_items == ["a1", "a2", "a3"]
    assert len(provider.calls) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_summarizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notetaker.summarizer'`

- [ ] **Step 3: Write `notetaker/summarizer.py`**

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Summary:
    text: str
    action_items: list[str]
    tags: list[str]


class Provider(Protocol):
    def summarize(self, transcript: str) -> Summary: ...


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def chunk_transcript(transcript: str, max_tokens: int = 3000) -> list[str]:
    lines = transcript.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for line in lines:
        line_tokens = estimate_tokens(line)
        if current and current_tokens + line_tokens > max_tokens:
            chunks.append("\n".join(current))
            current = []
            current_tokens = 0
        current.append(line)
        current_tokens += line_tokens
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def summarize_transcript(
    transcript: str, provider: Provider, chunk_token_limit: int = 3000
) -> Summary:
    chunks = chunk_transcript(transcript, chunk_token_limit)
    if len(chunks) == 1:
        return provider.summarize(chunks[0])
    partials = [provider.summarize(chunk) for chunk in chunks]
    combined_text = "\n\n".join(p.text for p in partials)
    reduced = provider.summarize(combined_text)
    tags = sorted({tag for p in partials for tag in p.tags} | set(reduced.tags))
    action_items = _dedupe_preserve_order(
        [item for p in partials for item in p.action_items] + reduced.action_items
    )
    return Summary(text=reduced.text, action_items=action_items, tags=tags)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_summarizer.py -v`
Expected: PASS (5 tests). Note: `test_summarize_transcript_reduces_multiple_chunks` asserts `tags == ["t1", "t2", "t3"]` — since the reduce implementation sorts the union, confirm this matches; if not, fix the assertion to match sorted-set semantics, not the other way around.

- [ ] **Step 5: Commit**

```bash
git add notetaker/summarizer.py tests/test_summarizer.py
git commit -m "feat: add Provider abstraction with chunked map-reduce summarization"
```

---

### Task 3: `summarizer.py` — `ClaudeProvider`

**Files:**
- Modify: `notetaker/summarizer.py`
- Modify: `tests/test_summarizer.py`

**Interfaces:**
- Consumes: `Summary` (Task 2).
- Produces: `SUMMARY_PROMPT_TEMPLATE: str`, `ClaudeProvider` (class: `__init__(self, api_key: str, model: str)`, `summarize(self, transcript: str) -> Summary`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_summarizer.py
import json
from unittest.mock import MagicMock

from notetaker.summarizer import ClaudeProvider


def test_claude_provider_parses_json_response(monkeypatch):
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [
        MagicMock(text=json.dumps({"text": "summary", "action_items": ["do x"], "tags": ["standup"]}))
    ]
    fake_client.messages.create.return_value = fake_response
    monkeypatch.setattr("notetaker.summarizer.anthropic.Anthropic", lambda api_key: fake_client)

    provider = ClaudeProvider(api_key="fake-key", model="claude-sonnet-5")
    result = provider.summarize("[00:00:01] hello")

    assert result.text == "summary"
    assert result.action_items == ["do x"]
    assert result.tags == ["standup"]
    fake_client.messages.create.assert_called_once()
    assert fake_client.messages.create.call_args.kwargs["model"] == "claude-sonnet-5"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_summarizer.py::test_claude_provider_parses_json_response -v`
Expected: FAIL with `AttributeError` or `ImportError` — `ClaudeProvider`/`anthropic` not present yet.

- [ ] **Step 3: Add to `notetaker/summarizer.py`**

Add near the top of the file:

```python
import json

import anthropic
```

Append after `summarize_transcript`:

```python
SUMMARY_PROMPT_TEMPLATE = """You will be given a meeting transcript. Respond with ONLY a JSON object \
with exactly these keys: "text" (a concise summary, string), "action_items" (a list of strings), \
"tags" (a list of short lowercase topic tags, strings). No other text, no markdown fences.

Transcript:
{transcript}
"""


class ClaudeProvider:
    def __init__(self, api_key: str, model: str):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def summarize(self, transcript: str) -> Summary:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[
                {"role": "user", "content": SUMMARY_PROMPT_TEMPLATE.format(transcript=transcript)}
            ],
        )
        data = json.loads(response.content[0].text)
        return Summary(
            text=data["text"],
            action_items=data.get("action_items", []),
            tags=data.get("tags", []),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_summarizer.py -v`
Expected: PASS (all tests, including the new one)

- [ ] **Step 5: Commit**

```bash
git add notetaker/summarizer.py tests/test_summarizer.py
git commit -m "feat: add ClaudeProvider"
```

---

### Task 4: `summarizer.py` — `AppleLocalProvider` + `check_apple_local_preflight`

**Files:**
- Modify: `notetaker/summarizer.py`
- Modify: `tests/test_summarizer.py`

**Interfaces:**
- Consumes: `Summary`, `SUMMARY_PROMPT_TEMPLATE` (Tasks 2-3).
- Produces: `APPLE_LOCAL_BASE_URL: str`, `AppleLocalError(Exception)`, `AppleLocalProvider` (class: `__init__(self, base_url: str = APPLE_LOCAL_BASE_URL, model: str = "apple-fm")`, `summarize(self, transcript: str) -> Summary`), `check_apple_local_preflight() -> list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_summarizer.py
import io

from notetaker.summarizer import (
    AppleLocalError,
    AppleLocalProvider,
    check_apple_local_preflight,
)


def _fake_urlopen_response(payload: dict):
    return io.BytesIO(json.dumps(payload).encode())


def test_apple_local_provider_parses_openai_shaped_response(monkeypatch):
    response_payload = {
        "choices": [
            {"message": {"content": json.dumps({"text": "s", "action_items": [], "tags": ["x"]})}}
        ]
    }

    class FakeCtx:
        def __enter__(self):
            return _fake_urlopen_response(response_payload)

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("notetaker.summarizer.urllib.request.urlopen", lambda req, timeout=60: FakeCtx())

    provider = AppleLocalProvider()
    result = provider.summarize("[00:00:01] hi")
    assert result.text == "s"
    assert result.tags == ["x"]


def test_apple_local_provider_raises_on_connection_failure(monkeypatch):
    import urllib.error

    def raise_error(req, timeout=60):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("notetaker.summarizer.urllib.request.urlopen", raise_error)
    provider = AppleLocalProvider()
    try:
        provider.summarize("hi")
        assert False, "expected AppleLocalError"
    except AppleLocalError:
        pass


def test_check_apple_local_preflight_flags_non_macos(monkeypatch):
    monkeypatch.setattr("notetaker.summarizer.platform.system", lambda: "Linux")
    problems = check_apple_local_preflight()
    assert any("macOS" in p for p in problems)


def test_check_apple_local_preflight_flags_old_macos(monkeypatch):
    monkeypatch.setattr("notetaker.summarizer.platform.system", lambda: "Darwin")
    monkeypatch.setattr("notetaker.summarizer.platform.mac_ver", lambda: ("15.1", ("", "", ""), ""))
    problems = check_apple_local_preflight()
    assert any("macOS 26" in p for p in problems)


def test_check_apple_local_preflight_flags_missing_apfel(monkeypatch):
    import subprocess as sp

    monkeypatch.setattr("notetaker.summarizer.platform.system", lambda: "Darwin")
    monkeypatch.setattr("notetaker.summarizer.platform.mac_ver", lambda: ("26.0", ("", "", ""), ""))
    monkeypatch.setattr("notetaker.summarizer.shutil.which", lambda name: "/usr/local/bin/brew")
    monkeypatch.setattr(
        "notetaker.summarizer.subprocess.run",
        lambda *a, **k: sp.CompletedProcess(a, returncode=1),
    )
    problems = check_apple_local_preflight()
    assert any("apfel is not installed" in p for p in problems)


def test_check_apple_local_preflight_passes_when_everything_ready(monkeypatch):
    import subprocess as sp

    monkeypatch.setattr("notetaker.summarizer.platform.system", lambda: "Darwin")
    monkeypatch.setattr("notetaker.summarizer.platform.mac_ver", lambda: ("26.0", ("", "", ""), ""))
    monkeypatch.setattr("notetaker.summarizer.shutil.which", lambda name: "/usr/local/bin/brew")
    monkeypatch.setattr(
        "notetaker.summarizer.subprocess.run",
        lambda *a, **k: sp.CompletedProcess(a, returncode=0),
    )

    class FakeCtx:
        def __enter__(self):
            return io.BytesIO(b"{}")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("notetaker.summarizer.urllib.request.urlopen", lambda req, timeout=2: FakeCtx())
    assert check_apple_local_preflight() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_summarizer.py -v -k apple_local`
Expected: FAIL — `AppleLocalProvider`/`check_apple_local_preflight` not defined yet.

- [ ] **Step 3: Add to `notetaker/summarizer.py`**

Add near the top imports:

```python
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
```

Append at the end of the file:

```python
APPLE_LOCAL_BASE_URL = "http://localhost:11434/v1"


class AppleLocalError(Exception):
    pass


class AppleLocalProvider:
    def __init__(self, base_url: str = APPLE_LOCAL_BASE_URL, model: str = "apple-fm"):
        self._base_url = base_url
        self._model = model

    def summarize(self, transcript: str) -> Summary:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "user", "content": SUMMARY_PROMPT_TEMPLATE.format(transcript=transcript)}
            ],
        }
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read())
        except urllib.error.URLError as exc:
            raise AppleLocalError(f"Could not reach apfel at {self._base_url}: {exc}") from exc
        raw = data["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        return Summary(
            text=parsed["text"],
            action_items=parsed.get("action_items", []),
            tags=parsed.get("tags", []),
        )


def check_apple_local_preflight() -> list[str]:
    problems: list[str] = []
    if platform.system() != "Darwin":
        problems.append("apple_local requires macOS.")
        return problems

    major = int(platform.mac_ver()[0].split(".")[0] or 0)
    if major < 26:
        problems.append(f"apple_local requires macOS 26+ (found {platform.mac_ver()[0]}).")

    if shutil.which("brew") is None:
        problems.append("Homebrew is required to install apfel. See https://brew.sh")
        return problems

    installed = subprocess.run(["brew", "list", "apfel"], capture_output=True).returncode == 0
    if not installed:
        problems.append("apfel is not installed. Run: brew install apfel")
        return problems

    try:
        with urllib.request.urlopen(f"{APPLE_LOCAL_BASE_URL}/models", timeout=2):
            pass
    except urllib.error.URLError:
        problems.append(
            "apfel is installed but its service is not running. Run: brew services start apfel"
        )

    return problems
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_summarizer.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add notetaker/summarizer.py tests/test_summarizer.py
git commit -m "feat: add AppleLocalProvider and apple_local preflight checks"
```

---

### Task 5: `summarizer.py` — `get_provider` factory

**Files:**
- Modify: `notetaker/summarizer.py`
- Modify: `tests/test_summarizer.py`

**Interfaces:**
- Consumes: `Config`, `ConfigError` from `notetaker.config` (Task 1); `ClaudeProvider`, `AppleLocalProvider` (Tasks 3-4).
- Produces: `get_provider(config: Config) -> Provider`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_summarizer.py
import pytest

from notetaker.config import Config, ConfigError
from notetaker.summarizer import AppleLocalProvider, ClaudeProvider, get_provider


def test_get_provider_claude_reads_env_key(monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret")
    config = Config(
        notes_dir=None, whisper_model="base.en", ai_provider="claude",
        ai_model="claude-sonnet-5", api_key_env="MY_KEY",
    )
    provider = get_provider(config)
    assert isinstance(provider, ClaudeProvider)


def test_get_provider_claude_missing_env_key_raises(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    config = Config(
        notes_dir=None, whisper_model="base.en", ai_provider="claude",
        ai_model="claude-sonnet-5", api_key_env="MISSING_KEY",
    )
    with pytest.raises(ConfigError):
        get_provider(config)


def test_get_provider_apple_local():
    config = Config(
        notes_dir=None, whisper_model="base.en", ai_provider="apple_local",
        ai_model="apple-fm", api_key_env="UNUSED",
    )
    assert isinstance(get_provider(config), AppleLocalProvider)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_summarizer.py -v -k get_provider`
Expected: FAIL — `get_provider` not defined.

- [ ] **Step 3: Add to `notetaker/summarizer.py`**

Add near the top imports:

```python
import os

from notetaker.config import Config, ConfigError
```

Append at the end of the file:

```python
def get_provider(config: Config) -> Provider:
    if config.ai_provider == "claude":
        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            raise ConfigError(f"Environment variable {config.api_key_env} is not set.")
        return ClaudeProvider(api_key=api_key, model=config.ai_model)
    if config.ai_provider == "apple_local":
        return AppleLocalProvider(model=config.ai_model)
    raise ConfigError(f"Unknown ai_provider '{config.ai_provider}'.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_summarizer.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add notetaker/summarizer.py tests/test_summarizer.py
git commit -m "feat: add provider factory"
```

---

### Task 6: `notes.py`

**Files:**
- Create: `notetaker/notes.py`
- Test: `tests/test_notes.py`

**Interfaces:**
- Consumes: `Summary` from `notetaker.summarizer` (Task 2).
- Produces: `slugify(title: str) -> str`, `note_id_for(title: str, start_time: datetime, notes_dir: Path) -> str`, `NoteMeta` (dataclass: `note_id: str`, `title: str`, `date: datetime`, `duration_minutes: int`, `tags: list[str]`, `path: Path`), `write_note(notes_dir: Path, title: str, start_time: datetime, duration_minutes: int, summary: Summary, transcript_lines: list[str]) -> Path`, `parse_note_meta(path: Path) -> NoteMeta`, `list_notes(notes_dir: Path) -> list[NoteMeta]`, `read_note_body(path: Path) -> str`, `find_note_path(notes_dir: Path, note_id: str) -> Path | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_notes.py
from datetime import datetime

from notetaker.notes import (
    find_note_path,
    list_notes,
    note_id_for,
    parse_note_meta,
    read_note_body,
    slugify,
    write_note,
)
from notetaker.summarizer import Summary


def test_slugify_lowercases_and_dashes():
    assert slugify("Team Standup!!") == "team-standup"


def test_slugify_empty_title_falls_back():
    assert slugify("...") == "untitled"


def test_note_id_for_no_collision(tmp_path):
    note_id = note_id_for("Standup", datetime(2026, 9, 11, 10, 0), tmp_path)
    assert note_id == "2026-09-11-standup"


def test_note_id_for_collision_appends_time(tmp_path):
    (tmp_path / "2026-09-11-standup.md").write_text("existing")
    note_id = note_id_for("Standup", datetime(2026, 9, 11, 14, 30), tmp_path)
    assert note_id == "2026-09-11-standup-1430"


def test_write_note_and_parse_round_trip(tmp_path):
    summary = Summary(text="We discussed X.", action_items=["Follow up with Bob"], tags=["project-x", "planning"])
    path = write_note(
        tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 18, summary,
        ["[00:00:03] hello", "[00:00:07] world"],
    )
    assert path.exists()

    meta = parse_note_meta(path)
    assert meta.title == "Standup"
    assert meta.duration_minutes == 18
    assert meta.tags == ["project-x", "planning"]

    body = read_note_body(path)
    assert "We discussed X." in body
    assert "Follow up with Bob" in body
    assert "[00:00:03] hello" in body


def test_list_notes_sorted_newest_first(tmp_path):
    summary = Summary(text="s", action_items=[], tags=[])
    write_note(tmp_path, "Old", datetime(2026, 9, 1, 9, 0), 5, summary, [])
    write_note(tmp_path, "New", datetime(2026, 9, 11, 9, 0), 5, summary, [])
    notes = list_notes(tmp_path)
    assert [n.title for n in notes] == ["New", "Old"]


def test_list_notes_empty_dir_returns_empty_list(tmp_path):
    assert list_notes(tmp_path / "does-not-exist") == []


def test_find_note_path(tmp_path):
    summary = Summary(text="s", action_items=[], tags=[])
    write_note(tmp_path, "Standup", datetime(2026, 9, 11, 10, 0), 5, summary, [])
    assert find_note_path(tmp_path, "2026-09-11-standup") is not None
    assert find_note_path(tmp_path, "nonexistent") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_notes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notetaker.notes'`

- [ ] **Step 3: Write `notetaker/notes.py`**

```python
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from notetaker.summarizer import Summary


@dataclass
class NoteMeta:
    note_id: str
    title: str
    date: datetime
    duration_minutes: int
    tags: list[str]
    path: Path


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "untitled"


def note_id_for(title: str, start_time: datetime, notes_dir: Path) -> str:
    date_str = start_time.strftime("%Y-%m-%d")
    base_id = f"{date_str}-{slugify(title)}"
    if not (notes_dir / f"{base_id}.md").exists():
        return base_id
    return f"{base_id}-{start_time.strftime('%H%M')}"


def write_note(
    notes_dir: Path,
    title: str,
    start_time: datetime,
    duration_minutes: int,
    summary: Summary,
    transcript_lines: list[str],
) -> Path:
    notes_dir.mkdir(parents=True, exist_ok=True)
    note_id = note_id_for(title, start_time, notes_dir)
    frontmatter = {
        "title": title,
        "date": start_time.isoformat(),
        "duration_minutes": duration_minutes,
        "tags": summary.tags,
    }
    action_items_md = "\n".join(f"- [ ] {item}" for item in summary.action_items) or "- (none)"
    transcript_md = "\n".join(transcript_lines) or "(no transcript captured)"
    body = (
        f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n\n"
        f"## Summary\n{summary.text}\n\n"
        f"## Action Items\n{action_items_md}\n\n"
        f"## Transcript\n{transcript_md}\n"
    )
    path = notes_dir / f"{note_id}.md"
    path.write_text(body)
    return path


def parse_note_meta(path: Path) -> NoteMeta:
    text = path.read_text()
    _, frontmatter_raw, _ = text.split("---", 2)
    fm = yaml.safe_load(frontmatter_raw)
    return NoteMeta(
        note_id=path.stem,
        title=fm["title"],
        date=datetime.fromisoformat(fm["date"]),
        duration_minutes=fm["duration_minutes"],
        tags=fm.get("tags") or [],
        path=path,
    )


def list_notes(notes_dir: Path) -> list[NoteMeta]:
    if not notes_dir.exists():
        return []
    metas = [parse_note_meta(p) for p in sorted(notes_dir.glob("*.md"))]
    return sorted(metas, key=lambda m: m.date, reverse=True)


def read_note_body(path: Path) -> str:
    text = path.read_text()
    _, _, body = text.split("---", 2)
    return body.strip()


def find_note_path(notes_dir: Path, note_id: str) -> Path | None:
    candidate = notes_dir / f"{note_id}.md"
    return candidate if candidate.exists() else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_notes.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add notetaker/notes.py tests/test_notes.py
git commit -m "feat: add note writing, parsing, and listing"
```

---

### Task 7: `transcriber.py`

**Files:**
- Create: `notetaker/transcriber.py`
- Test: `tests/test_transcriber.py`

**Interfaces:**
- Produces: `format_timestamp(seconds: float) -> str`, `append_transcript_line(transcript_path: Path, line: str) -> None`, `Transcriber` (class: `__init__(self, model_size: str, device: str = "cpu", compute_type: str = "int8", _model=None)`, `transcribe_chunk(self, wav_path: Path, elapsed_seconds: float) -> str`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_transcriber.py
from types import SimpleNamespace

from notetaker.transcriber import Transcriber, append_transcript_line, format_timestamp


def test_format_timestamp():
    assert format_timestamp(0) == "00:00:00"
    assert format_timestamp(7) == "00:00:07"
    assert format_timestamp(3665) == "01:01:05"


def test_append_transcript_line_appends(tmp_path):
    path = tmp_path / "transcript.txt"
    append_transcript_line(path, "[00:00:01] hello")
    append_transcript_line(path, "[00:00:05] world")
    assert path.read_text() == "[00:00:01] hello\n[00:00:05] world\n"


class FakeWhisperModel:
    def __init__(self, segments_text):
        self._segments_text = segments_text

    def transcribe(self, wav_path):
        segments = [SimpleNamespace(text=t) for t in self._segments_text]
        return segments, None


def test_transcribe_chunk_returns_timestamped_line(tmp_path):
    wav_path = tmp_path / "chunk_0.wav"
    wav_path.write_bytes(b"")
    transcriber = Transcriber("base.en", _model=FakeWhisperModel([" hello ", " world"]))
    line = transcriber.transcribe_chunk(wav_path, elapsed_seconds=7)
    assert line == "[00:00:07] hello world"


def test_transcribe_chunk_returns_empty_string_for_silence(tmp_path):
    wav_path = tmp_path / "chunk_0.wav"
    wav_path.write_bytes(b"")
    transcriber = Transcriber("base.en", _model=FakeWhisperModel([]))
    assert transcriber.transcribe_chunk(wav_path, elapsed_seconds=0) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_transcriber.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notetaker.transcriber'`

- [ ] **Step 3: Write `notetaker/transcriber.py`**

```python
from pathlib import Path

from faster_whisper import WhisperModel


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def append_transcript_line(transcript_path: Path, line: str) -> None:
    with open(transcript_path, "a") as f:
        f.write(line + "\n")


class Transcriber:
    def __init__(
        self,
        model_size: str,
        device: str = "cpu",
        compute_type: str = "int8",
        _model=None,
    ):
        self._model = _model if _model is not None else WhisperModel(
            model_size, device=device, compute_type=compute_type
        )

    def transcribe_chunk(self, wav_path: Path, elapsed_seconds: float) -> str:
        segments, _ = self._model.transcribe(str(wav_path))
        text = " ".join(segment.text.strip() for segment in segments).strip()
        if not text:
            return ""
        return f"[{format_timestamp(elapsed_seconds)}] {text}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_transcriber.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add notetaker/transcriber.py tests/test_transcriber.py
git commit -m "feat: add faster-whisper transcriber wrapper"
```

---

### Task 8: `recorder.py`

**Files:**
- Create: `notetaker/recorder.py`
- Test: `tests/test_recorder.py`

**Interfaces:**
- Consumes: `Transcriber`, `append_transcript_line` from `notetaker.transcriber` (Task 7).
- Produces: `BlackHoleStatus` (Enum: `NOT_INSTALLED`, `INSTALLED_NOT_ACTIVE`, `ACTIVE`), `find_blackhole_device_index() -> int | None`, `check_blackhole() -> BlackHoleStatus`, `capture_chunk(device_index: int, duration_seconds: int, out_path: Path, sample_rate: int = 16000) -> None`, `run_recorder(session_dir: Path, device_index: int, transcriber: Transcriber, chunk_seconds: int = 10, capture_fn=capture_chunk) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_recorder.py
import os
import signal

from notetaker.recorder import BlackHoleStatus, check_blackhole, run_recorder


class FakeTranscriber:
    def __init__(self, lines):
        self.lines = list(lines)
        self.calls = 0

    def transcribe_chunk(self, wav_path, elapsed_seconds):
        self.calls += 1
        return self.lines.pop(0) if self.lines else ""


def test_check_blackhole_not_installed(monkeypatch):
    import subprocess as sp

    monkeypatch.setattr("notetaker.recorder.subprocess.run", lambda *a, **k: sp.CompletedProcess(a, returncode=1))
    monkeypatch.setattr("notetaker.recorder.find_blackhole_device_index", lambda: None)
    assert check_blackhole() == BlackHoleStatus.NOT_INSTALLED


def test_check_blackhole_installed_not_active(monkeypatch):
    import subprocess as sp

    monkeypatch.setattr("notetaker.recorder.subprocess.run", lambda *a, **k: sp.CompletedProcess(a, returncode=0))
    monkeypatch.setattr("notetaker.recorder.find_blackhole_device_index", lambda: None)
    assert check_blackhole() == BlackHoleStatus.INSTALLED_NOT_ACTIVE


def test_check_blackhole_active(monkeypatch):
    import subprocess as sp

    monkeypatch.setattr("notetaker.recorder.subprocess.run", lambda *a, **k: sp.CompletedProcess(a, returncode=1))
    monkeypatch.setattr("notetaker.recorder.find_blackhole_device_index", lambda: 3)
    assert check_blackhole() == BlackHoleStatus.ACTIVE


def test_run_recorder_stops_on_sigterm_and_appends_transcript(tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    call_count = {"n": 0}

    def fake_capture(device_index, duration_seconds, out_path, sample_rate=16000):
        call_count["n"] += 1
        out_path.write_bytes(b"")
        if call_count["n"] >= 2:
            os.kill(os.getpid(), signal.SIGTERM)

    transcriber = FakeTranscriber(["[00:00:00] hello", "[00:00:10] world"])
    run_recorder(session_dir, device_index=0, transcriber=transcriber, chunk_seconds=10, capture_fn=fake_capture)

    transcript = (session_dir / "transcript.txt").read_text()
    assert "hello" in transcript
    assert "world" in transcript
    assert call_count["n"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_recorder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notetaker.recorder'`

- [ ] **Step 3: Write `notetaker/recorder.py`**

```python
import signal
import subprocess
import sys
import wave
from enum import Enum
from pathlib import Path

import sounddevice as sd

from notetaker.transcriber import Transcriber, append_transcript_line


class BlackHoleStatus(Enum):
    NOT_INSTALLED = "not_installed"
    INSTALLED_NOT_ACTIVE = "installed_not_active"
    ACTIVE = "active"


def find_blackhole_device_index() -> int | None:
    for idx, device in enumerate(sd.query_devices()):
        if "BlackHole" in device.get("name", "") and device.get("max_input_channels", 0) > 0:
            return idx
    return None


def check_blackhole() -> BlackHoleStatus:
    if find_blackhole_device_index() is not None:
        return BlackHoleStatus.ACTIVE
    result = subprocess.run(["brew", "list", "blackhole-2ch"], capture_output=True)
    if result.returncode == 0:
        return BlackHoleStatus.INSTALLED_NOT_ACTIVE
    return BlackHoleStatus.NOT_INSTALLED


def capture_chunk(device_index: int, duration_seconds: int, out_path: Path, sample_rate: int = 16000) -> None:
    frames = sd.rec(
        int(duration_seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        device=device_index,
    )
    sd.wait()
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames.tobytes())


def run_recorder(
    session_dir: Path,
    device_index: int,
    transcriber: Transcriber,
    chunk_seconds: int = 10,
    capture_fn=capture_chunk,
) -> None:
    stop_flag = {"stop": False}

    def handle_sigterm(signum, frame):
        stop_flag["stop"] = True

    signal.signal(signal.SIGTERM, handle_sigterm)

    transcript_path = session_dir / "transcript.txt"
    chunks_dir = session_dir / "chunks"
    chunks_dir.mkdir(exist_ok=True)

    elapsed = 0.0
    index = 0
    while not stop_flag["stop"]:
        chunk_path = chunks_dir / f"chunk_{index:05d}.wav"
        capture_fn(device_index, chunk_seconds, chunk_path)
        line = transcriber.transcribe_chunk(chunk_path, elapsed)
        if line:
            append_transcript_line(transcript_path, line)
        elapsed += chunk_seconds
        index += 1


if __name__ == "__main__":
    _session_dir = Path(sys.argv[1])
    _device_index = int(sys.argv[2])
    _model_size = sys.argv[3]
    run_recorder(_session_dir, _device_index, Transcriber(_model_size))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_recorder.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add notetaker/recorder.py tests/test_recorder.py
git commit -m "feat: add recorder with BlackHole detection and chunked capture loop"
```

---

### Task 9: `cli.py` — `init`

**Files:**
- Create: `notetaker/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `write_default_config`, `load_config`, `CONFIG_DIR` from `notetaker.config` (Task 1); `BlackHoleStatus`, `check_blackhole` from `notetaker.recorder` (Task 8); `check_apple_local_preflight` from `notetaker.summarizer` (Task 4); `Transcriber` from `notetaker.transcriber` (Task 7).
- Produces: `app` (Typer instance) with command `init`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
from typer.testing import CliRunner

from notetaker.cli import app
from notetaker.config import Config
from notetaker.recorder import BlackHoleStatus

runner = CliRunner()


def _patch_config_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.config.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("notetaker.cli.write_default_config", lambda: True)


def test_init_writes_config_and_reports_blackhole_active(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.write_default_config", lambda: True)
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(tmp_path, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )
    monkeypatch.setattr("notetaker.cli.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.cli.Transcriber", lambda model: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "BlackHole is installed and active" in result.output


def test_init_fails_when_api_key_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.write_default_config", lambda: True)
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(tmp_path, "tiny", "claude", "claude-sonnet-5", "MISSING_KEY"),
    )
    monkeypatch.setattr("notetaker.cli.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.delenv("MISSING_KEY", raising=False)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1


def test_init_reports_apple_local_problems(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.write_default_config", lambda: True)
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(tmp_path, "tiny", "apple_local", "apple-fm", "UNUSED"),
    )
    monkeypatch.setattr("notetaker.cli.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.cli.check_apple_local_preflight", lambda: ["apfel is not installed"])

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "apfel is not installed" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notetaker.cli'`

- [ ] **Step 3: Write `notetaker/cli.py`** (init only for now)

```python
import os
from pathlib import Path

import typer

from notetaker.config import CONFIG_DIR, load_config, write_default_config
from notetaker.recorder import BlackHoleStatus, check_blackhole
from notetaker.summarizer import check_apple_local_preflight
from notetaker.transcriber import Transcriber

app = typer.Typer()

SESSION_FILE_NAME = "current_session.json"


@app.command()
def init():
    config_path = CONFIG_DIR / "config.yaml"
    if write_default_config():
        typer.echo(f"Wrote default config to {config_path}")
    else:
        typer.echo(f"Config already exists at {config_path}, skipping.")

    config = load_config()

    status = check_blackhole()
    if status == BlackHoleStatus.NOT_INSTALLED:
        typer.echo("BlackHole not found. Install it with: brew install blackhole-2ch")
    elif status == BlackHoleStatus.INSTALLED_NOT_ACTIVE:
        typer.echo(
            "BlackHole is installed but not active yet — reboot your Mac, then re-run `notetaker init`."
        )
    else:
        typer.echo("BlackHole is installed and active.")

    if config.ai_provider == "claude":
        if not os.environ.get(config.api_key_env):
            typer.echo(
                f"error: {config.api_key_env} is not set. Export it in your shell profile, "
                "then re-run `notetaker init`.",
                err=True,
            )
            raise typer.Exit(1)
        typer.echo(f"{config.api_key_env} is set.")
    elif config.ai_provider == "apple_local":
        problems = check_apple_local_preflight()
        if problems:
            for problem in problems:
                typer.echo(f"error: {problem}", err=True)
            raise typer.Exit(1)
        typer.echo("apfel is installed and running.")

    typer.echo(f"Loading Whisper model '{config.whisper_model}' (downloads on first run)...")
    Transcriber(config.whisper_model)
    typer.echo("Whisper model ready.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add notetaker/cli.py tests/test_cli.py
git commit -m "feat: add notetaker init command"
```

---

### Task 10: `cli.py` — `start`

**Files:**
- Modify: `notetaker/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `find_blackhole_device_index`, `check_blackhole`, `BlackHoleStatus` (Task 8); `load_config` (Task 1).
- Produces: `start(title: str)` command; module-level `SESSION_FILE: Path` computed as `CONFIG_DIR / SESSION_FILE_NAME`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_cli.py
import json
from unittest.mock import MagicMock

from notetaker.recorder import BlackHoleStatus


def test_start_fails_when_blackhole_not_active(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", tmp_path / "current_session.json")
    monkeypatch.setattr("notetaker.cli.check_blackhole", lambda: BlackHoleStatus.NOT_INSTALLED)

    result = runner.invoke(app, ["start", "Standup"])
    assert result.exit_code == 1


def test_start_fails_when_session_already_running(monkeypatch, tmp_path):
    session_file = tmp_path / "current_session.json"
    session_file.write_text(json.dumps({"pid": os_getpid_for_test(), "title": "x", "start_time": "2026-09-11T10:00:00", "session_dir": str(tmp_path)}))
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", session_file)
    monkeypatch.setattr("notetaker.cli.check_blackhole", lambda: BlackHoleStatus.ACTIVE)

    result = runner.invoke(app, ["start", "Standup"])
    assert result.exit_code == 1
    assert "already running" in result.output


def os_getpid_for_test():
    import os
    return os.getpid()


def test_start_spawns_recorder_and_writes_session_file(monkeypatch, tmp_path):
    session_file = tmp_path / "current_session.json"
    monkeypatch.setattr("notetaker.cli.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", session_file)
    monkeypatch.setattr("notetaker.cli.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.cli.find_blackhole_device_index", lambda: 2)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(tmp_path, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )
    fake_proc = MagicMock(pid=12345)
    monkeypatch.setattr("notetaker.cli.subprocess.Popen", lambda *a, **k: fake_proc)

    result = runner.invoke(app, ["start", "Standup"])

    assert result.exit_code == 0
    assert "Recording started" in result.output
    assert "microphone access" in result.output
    assert "Teams will not show" in result.output
    session = json.loads(session_file.read_text())
    assert session["pid"] == 12345
    assert session["title"] == "Standup"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v -k start`
Expected: FAIL — `start` command not registered / `SESSION_FILE` missing.

- [ ] **Step 3: Add to `notetaker/cli.py`**

Add to imports:

```python
import json
import subprocess
import sys
from datetime import datetime

from notetaker.recorder import find_blackhole_device_index
```

Add after `init`:

```python
SESSION_FILE = CONFIG_DIR / SESSION_FILE_NAME


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@app.command()
def start(title: str):
    if SESSION_FILE.exists():
        session = json.loads(SESSION_FILE.read_text())
        if _pid_alive(session["pid"]):
            typer.echo("error: a session is already running. Run `notetaker stop` first.", err=True)
            raise typer.Exit(1)

    status = check_blackhole()
    if status != BlackHoleStatus.ACTIVE:
        typer.echo("error: BlackHole is not active. Run `notetaker init` for setup instructions.", err=True)
        raise typer.Exit(1)
    device_index = find_blackhole_device_index()

    typer.echo("macOS will ask for microphone access to read the BlackHole device — please allow it.")
    typer.echo(
        "Reminder: Teams will not show its own recording indicator for this. "
        "Let participants know you're recording."
    )

    config = load_config()
    start_time = datetime.now()
    session_dir = CONFIG_DIR / "sessions" / start_time.strftime("%Y%m%d-%H%M%S")
    session_dir.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [sys.executable, "-m", "notetaker.recorder", str(session_dir), str(device_index), config.whisper_model],
        start_new_session=True,
    )
    SESSION_FILE.write_text(
        json.dumps(
            {
                "pid": proc.pid,
                "title": title,
                "start_time": start_time.isoformat(),
                "session_dir": str(session_dir),
            }
        )
    )
    typer.echo(f"Recording started: {title}")
```

Note: `SESSION_FILE` is computed once at import time from `CONFIG_DIR`. Since tests monkeypatch `notetaker.cli.SESSION_FILE` directly (not `CONFIG_DIR` alone) for the `start`/`stop` tests, this works without needing a fixture-time re-import — confirm this in Step 4 rather than assuming.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (all tests). If `SESSION_FILE` monkeypatching doesn't take effect because `start`/`stop` read the module-level name via closure at call time (they do, since Python resolves globals at call time, not def time) — this should just work; if not, debug by printing `notetaker.cli.SESSION_FILE` inside the test before invoking.

- [ ] **Step 5: Commit**

```bash
git add notetaker/cli.py tests/test_cli.py
git commit -m "feat: add notetaker start command"
```

---

### Task 11: `cli.py` — `stop`

**Files:**
- Modify: `notetaker/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `get_provider`, `summarize_transcript`, `Summary` from `notetaker.summarizer` (Tasks 2, 5); `write_note` from `notetaker.notes` (Task 6).
- Produces: `stop()` command.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_cli.py
import signal
import time


def test_stop_fails_with_no_active_session(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", tmp_path / "current_session.json")
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 1
    assert "no active session" in result.output


def test_stop_salvages_transcript_and_writes_note(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n[00:00:07] world\n")

    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {
                "pid": 999999,
                "title": "Standup",
                "start_time": "2026-09-11T10:00:00",
                "session_dir": str(session_dir),
            }
        )
    )

    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", session_file)
    monkeypatch.setattr("notetaker.cli._pid_alive", lambda pid: False)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )

    from notetaker.summarizer import Summary

    monkeypatch.setattr("notetaker.cli.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.cli.summarize_transcript",
        lambda transcript, provider: Summary(text="summary text", action_items=["a"], tags=["t"]),
    )

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
    assert not session_file.exists()
    assert not session_dir.exists()
    saved_notes = list(notes_dir.glob("*.md"))
    assert len(saved_notes) == 1
    assert "summary text" in saved_notes[0].read_text()


def test_stop_saves_note_with_error_when_summarization_fails(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions" / "20260911-100000"
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.txt").write_text("[00:00:03] hello\n")

    session_file = tmp_path / "current_session.json"
    session_file.write_text(
        json.dumps(
            {
                "pid": 999999,
                "title": "Standup",
                "start_time": "2026-09-11T10:00:00",
                "session_dir": str(session_dir),
            }
        )
    )

    notes_dir = tmp_path / "notes"
    monkeypatch.setattr("notetaker.cli.SESSION_FILE", session_file)
    monkeypatch.setattr("notetaker.cli._pid_alive", lambda pid: False)
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )
    monkeypatch.setattr("notetaker.cli.get_provider", lambda config: object())

    def raise_error(transcript, provider):
        raise RuntimeError("network down")

    monkeypatch.setattr("notetaker.cli.summarize_transcript", raise_error)

    result = runner.invoke(app, ["stop"])

    assert result.exit_code == 0
    saved_notes = list(notes_dir.glob("*.md"))
    assert len(saved_notes) == 1
    assert "Summarization failed" in saved_notes[0].read_text()
    assert "hello" in saved_notes[0].read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v -k stop`
Expected: FAIL — `stop` command not registered.

- [ ] **Step 3: Add to `notetaker/cli.py`**

Add to imports:

```python
import shutil
import time
from notetaker.notes import write_note
from notetaker.summarizer import Summary, get_provider, summarize_transcript
```

Append:

```python
@app.command()
def stop():
    if not SESSION_FILE.exists():
        typer.echo("error: no active session.", err=True)
        raise typer.Exit(1)

    session = json.loads(SESSION_FILE.read_text())
    pid = session["pid"]
    session_dir = Path(session["session_dir"])

    if _pid_alive(pid):
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            if not _pid_alive(pid):
                break
            time.sleep(1)

    transcript_path = session_dir / "transcript.txt"
    transcript = transcript_path.read_text() if transcript_path.exists() else ""
    transcript_lines = transcript.splitlines()

    start_time = datetime.fromisoformat(session["start_time"])
    duration_minutes = int((datetime.now() - start_time).total_seconds() // 60)

    config = load_config()
    try:
        provider = get_provider(config)
        summary = summarize_transcript(transcript, provider)
    except Exception as exc:
        summary = Summary(text=f"Summarization failed: {exc}", action_items=[], tags=[])

    note_path = write_note(
        config.notes_dir, session["title"], start_time, duration_minutes, summary, transcript_lines
    )

    shutil.rmtree(session_dir, ignore_errors=True)
    SESSION_FILE.unlink()

    typer.echo(f"Saved note: {note_path}")
```

Add `import signal` to the top-level imports (needed here, not just in `recorder.py`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add notetaker/cli.py tests/test_cli.py
git commit -m "feat: add notetaker stop command"
```

---

### Task 12: `cli.py` — `list` and `show`

**Files:**
- Modify: `notetaker/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `list_notes`, `find_note_path`, `read_note_body` from `notetaker.notes` (Task 6).
- Produces: `list_command()` command (registered as `list`), `show(note_id: str)` command.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_cli.py
from datetime import datetime

from notetaker.notes import write_note
from notetaker.summarizer import Summary


def test_list_prints_notes(monkeypatch, tmp_path):
    notes_dir = tmp_path / "notes"
    write_note(notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("s", [], ["proj"]), [])
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "Standup" in result.output
    assert "proj" in result.output


def test_show_prints_note_body(monkeypatch, tmp_path):
    notes_dir = tmp_path / "notes"
    write_note(
        notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5,
        Summary("Summary text", [], []), ["[00:00:01] hi"],
    )
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )
    result = runner.invoke(app, ["show", "2026-09-11-standup"])
    assert result.exit_code == 0
    assert "Summary text" in result.output


def test_show_missing_note_fails(monkeypatch, tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    monkeypatch.setattr(
        "notetaker.cli.load_config",
        lambda: Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    )
    result = runner.invoke(app, ["show", "nonexistent"])
    assert result.exit_code == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v -k "list or show"`
Expected: FAIL — commands not registered.

- [ ] **Step 3: Add to `notetaker/cli.py`**

Add to imports:

```python
from notetaker.notes import find_note_path, list_notes, read_note_body
```

Append:

```python
@app.command(name="list")
def list_command():
    config = load_config()
    for meta in list_notes(config.notes_dir):
        tags = ", ".join(meta.tags)
        typer.echo(f"{meta.note_id}  {meta.title}  [{tags}]")


@app.command()
def show(note_id: str):
    config = load_config()
    path = find_note_path(config.notes_dir, note_id)
    if path is None:
        typer.echo(f"error: no note found with id '{note_id}'.", err=True)
        raise typer.Exit(1)
    typer.echo(read_note_body(path))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add notetaker/cli.py tests/test_cli.py
git commit -m "feat: add notetaker list and show commands"
```

---

### Task 13: `install.sh`

**Files:**
- Create: `install.sh`

**Interfaces:**
- None (shell script, no Python interfaces).

- [ ] **Step 1: Write `install.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

MIN_PY_MINOR=10
MAX_PY_MINOR=11

find_python() {
  for candidate in python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      local major minor
      major=$("$candidate" -c 'import sys; print(sys.version_info.major)')
      minor=$("$candidate" -c 'import sys; print(sys.version_info.minor)')
      if [ "$major" = "3" ] && [ "$minor" -ge "$MIN_PY_MINOR" ] && [ "$minor" -le "$MAX_PY_MINOR" ]; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON_BIN=$(find_python) || {
  echo "error: notetaker requires Python 3.10 or 3.11 (faster-whisper's PyAV dependency" >&2
  echo "       does not reliably install on 3.12+/3.13). Install one, e.g.:" >&2
  echo "         brew install python@3.11" >&2
  echo "       then re-run ./install.sh" >&2
  exit 1
}

echo "Using $PYTHON_BIN ($("$PYTHON_BIN" --version))"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_DIR/.venv"

if [ -d "$VENV_DIR" ]; then
  echo "Existing virtualenv found at $VENV_DIR, recreating it..."
  rm -rf "$VENV_DIR"
fi

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -e "$REPO_DIR"

BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/notetaker" "$BIN_DIR/notetaker"

echo ""
echo "Installed. Make sure $BIN_DIR is on your PATH, then run:"
echo "  notetaker init"
```

- [ ] **Step 2: Make it executable and dry-run it**

Run: `chmod +x install.sh && ./install.sh`
Expected: creates `.venv/`, installs the package, prints the "Installed." message. If `faster-whisper`/`ctranslate2`/`av` fail to build, that's the Task 1 pin-verification note coming due — fix the pins there, not here.

- [ ] **Step 3: Re-run to confirm idempotency**

Run: `./install.sh` again.
Expected: same success output, `.venv/` recreated cleanly, no errors about an existing directory.

- [ ] **Step 4: Commit**

```bash
git add install.sh
git commit -m "feat: add install.sh with Python version check and idempotent venv setup"
```

---

### Task 14: Integration test — recorder + transcriber wiring

**Files:**
- Create: `tests/fixtures/silence.wav`
- Test: `tests/test_integration_transcription.py`

**Interfaces:**
- Consumes: `Transcriber` (Task 7), `run_recorder`, `capture_chunk` (Task 8).

This is the spec's "thin integration test" — it exercises real `faster-whisper` (tiny model) against a real WAV file to catch wiring bugs, not to validate transcription accuracy. It downloads the `tiny` model on first run and is slower than the unit tests; mark it so it can be skipped in fast local loops.

- [ ] **Step 1: Generate the fixture (one-time, not part of the test run)**

```python
# scripts/make_silence_fixture.py — run once locally, not part of pytest
import wave

with wave.open("tests/fixtures/silence.wav", "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(16000)
    wf.writeframes(b"\x00\x00" * 16000 * 2)  # 2 seconds of silence
```

Run: `mkdir -p tests/fixtures && python scripts/make_silence_fixture.py`, then delete `scripts/make_silence_fixture.py` (it was only needed to produce the checked-in fixture).

- [ ] **Step 2: Write the test**

```python
# tests/test_integration_transcription.py
from pathlib import Path

import pytest

from notetaker.transcriber import Transcriber

FIXTURE = Path(__file__).parent / "fixtures" / "silence.wav"


@pytest.mark.integration
def test_transcriber_processes_real_wav_without_error():
    transcriber = Transcriber("tiny")
    line = transcriber.transcribe_chunk(FIXTURE, elapsed_seconds=0)
    # Silence should produce no transcribable speech — the assertion is that
    # this doesn't raise and returns a string (possibly empty), proving the
    # faster-whisper wiring (model load, file read, segment iteration) works.
    assert isinstance(line, str)
```

- [ ] **Step 3: Register the `integration` marker**

Add to `pyproject.toml` under a new `[tool.pytest.ini_options]` table:

```toml
[tool.pytest.ini_options]
markers = ["integration: slower tests that download/run a real faster-whisper model"]
```

- [ ] **Step 4: Run it**

Run: `pytest tests/test_integration_transcription.py -v -m integration`
Expected: PASS (downloads the `tiny` model on first run — requires network the first time only).

Run the full fast suite without it via: `pytest -m "not integration"`

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/silence.wav tests/test_integration_transcription.py pyproject.toml
git commit -m "test: add thin integration test for transcriber wiring"
```

---

### Task 15: `README.md`

**Files:**
- Create: `README.md`

**Interfaces:**
- None.

- [ ] **Step 1: Write `README.md`**

```markdown
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

3. Clone this repo and run the installer:

   ```bash
   git clone <this-repo-url>
   cd notetaker-app
   ./install.sh
   ```

   `install.sh` requires Python 3.10 or 3.11 (not 3.12/3.13 — `faster-whisper`'s PyAV
   dependency doesn't reliably build there) and will tell you clearly if your `python3`
   doesn't qualify.

4. Set your AI provider:
   - **Claude (default, cloud):** export `ANTHROPIC_API_KEY` in your shell profile.
   - **Apple Foundation Models (opt-in, fully local, no API key):** install
     [`apfel`](https://github.com/Arthur-Ficial/apfel) (`brew install apfel`), start it
     as a background service (`brew services start apfel`), and set `ai_provider:
     apple_local` in `~/.notetaker/config.yaml`. Requires macOS 26+, Apple Silicon, and
     Apple Intelligence enabled in System Settings.

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
```

## A note on recording consent

This tool captures system audio directly — Teams (or any other meeting app) has no idea
it's happening and will not show its own "this meeting is being recorded" indicator to
other participants. `notetaker start` prints a reminder each time, but it's on you to let
participants know per your organization's policy and local law.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup and usage instructions"
```

---

## Self-Review Notes

- **Spec coverage:** All CLI commands (`init`/`start`/`stop`/`list`/`show`), both providers, chunked-polling recording, note frontmatter format, and the manual BlackHole/multi-output setup are covered. Config schema matches the spec's YAML exactly.
- **Grilling-session decisions covered:** Python version check (Task 13), pinned fragile deps (Task 1), mic-permission + consent reminders (Task 10), BlackHole three-state detection (Tasks 8-9), idempotent `init`/`install.sh` (Tasks 1, 9, 13), eager model download in `init` (Task 9), note ID collision suffix (Task 6), `apple_local` opt-in with preflight gated on selection (Tasks 4-5, 9), chunking above the `Provider` interface (Task 2).
- **Type consistency check:** `Summary`, `Config`, `NoteMeta`, `BlackHoleStatus`, and `Provider` are each defined once (Tasks 1, 2, 6, 8) and only imported afterward — no redefinitions or renamed fields across later tasks.
