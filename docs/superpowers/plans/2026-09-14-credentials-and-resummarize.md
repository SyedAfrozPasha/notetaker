# Provider Credentials (Keychain) and Resummarize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the Claude API key off environment variables and into the macOS Keychain (per `CONTEXT.md`'s Provider credential definition), and add Resummarize — replacing a Note's Summary by re-running a Provider against its persisted Transcript (built in the prior plan's sidecar file), covering both retrying a failed Summary and redoing a successful one on demand.

**Architecture:** A new `notetaker/credentials.py` module wraps the `keyring` library for raw Keychain read/write/masking, kept provider-agnostic (keyed by the config's `api_key_env` string, not by provider name). `notetaker/summarizer.py`'s `get_provider` checks the Keychain first, falling back to the environment variable for backward compatibility. `notetaker/service.py` adds the business logic on top (validate-before-save, masked display) and a `resummarize_note` function that reuses a `_summarize_or_fallback` helper extracted from `stop_session` — the same DRY move Plan 2 made for `_terminate_recorder`. `notetaker/notes.py` gets a `rewrite_note_summary` function that re-renders an existing Note in place (same note ID, same Transcript, new Summary/Action Items/tags) by extracting `write_note`'s body-rendering logic into a shared `_render_note_body` helper.

**Tech Stack:** Python 3.10/3.11, pytest, `keyring` (new dependency, macOS Keychain backend).

**Spec:** `docs/superpowers/specs/2026-09-12-notetaker-ui-design.md` (Provider credential management, Resummarize) and `CONTEXT.md` (Provider credential, Resummarize, Transcript definitions)

## Global Constraints

- Python `>=3.10,<3.12` (per `pyproject.toml`).
- macOS only.
- Run tests with `.venv/bin/pytest -m "not integration" -q` (fast suite).
- New dependency: `keyring>=24`.
- **CRITICAL test-isolation requirement, carried forward from the prior plan's lesson:** every test that exercises `get_provider`'s or `check_setup`'s `"claude"` branch (including two EXISTING tests this plan doesn't otherwise touch) must mock the Keychain lookup. Without it, a test would call the real `keyring.get_password`, which queries this machine's actual macOS Keychain — at best a slow/flaky test, at worst a permission-prompt dialog that hangs a non-interactive test run. Every task below that touches these functions calls this out explicitly.
- This plan intentionally changes two existing error messages (in `get_provider` and `check_setup`) to mention the new `notetaker set-api-key` command — a deliberate improvement, not refactor drift. One existing test's assertion text must be updated to match (called out in Task 5).
- CLI spinners (`rich`-based loading indicators) are explicitly OUT of scope for this plan — split into a separate, smaller follow-up plan since this plan's Keychain and Resummarize work is substantial enough on its own.

---

## File Structure

- Create: `notetaker/credentials.py` — raw Keychain get/set/mask, provider-agnostic.
- Create: `tests/test_credentials.py`.
- Modify: `notetaker/summarizer.py` — `get_provider` checks Keychain first; new `validate_claude_api_key`.
- Modify: `tests/test_summarizer.py` — mock Keychain in the two existing `get_provider` claude-path tests; add tests for `validate_claude_api_key`.
- Modify: `notetaker/notes.py` — extract `_render_note_body`; add `rewrite_note_summary`.
- Modify: `tests/test_notes.py` — tests for `rewrite_note_summary`.
- Modify: `notetaker/service.py` — Keychain-aware `check_setup`; `save_provider_credential`/`get_masked_provider_credential`; extract `_summarize_or_fallback` from `stop_session`; add `resummarize_note`.
- Modify: `tests/test_service.py` — mock Keychain in the two existing `check_setup` claude-path tests, update one assertion text; tests for the new functions.
- Modify: `notetaker/cli.py` — `set-api-key`, `show-api-key`, `resummarize` commands.
- Modify: `tests/test_cli.py` — tests for the three new commands.
- Modify: `pyproject.toml` — add `keyring` dependency.
- Modify: `CLAUDE.md` — document the new capabilities.

---

### Task 1: Add the `keyring` dependency

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- None — dependency addition only.

- [ ] **Step 1: Add `keyring` to `pyproject.toml`'s dependencies**

In `pyproject.toml`, change:

```toml
dependencies = [
    "typer>=0.12",
    "pyyaml>=6.0",
    "sounddevice>=0.4",
    "numpy>=1.26",
    "faster-whisper==1.0.3",
    "ctranslate2==4.6.0",
    "av==12.3.0",
    "requests>=2.28",
    "anthropic>=0.34",
]
```

to:

```toml
dependencies = [
    "typer>=0.12",
    "pyyaml>=6.0",
    "sounddevice>=0.4",
    "numpy>=1.26",
    "faster-whisper==1.0.3",
    "ctranslate2==4.6.0",
    "av==12.3.0",
    "requests>=2.28",
    "anthropic>=0.34",
    "keyring>=24",
]
```

- [ ] **Step 2: Reinstall the project in editable mode so the new dependency resolves**

Run: `.venv/bin/pip install -e ".[dev]"`
Expected: `keyring` (and its transitive dependencies) get installed; no errors.

- [ ] **Step 3: Verify the import works**

Run: `.venv/bin/python -c "import keyring; print(keyring.get_keyring())"`
Expected: prints a macOS Keychain backend (e.g. `keyring.backends.macOS.Keyring`), no error.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add keyring dependency for Keychain-backed credential storage"
```

---

### Task 2: `notetaker/credentials.py` — raw Keychain get/set/mask

**Files:**
- Create: `notetaker/credentials.py`
- Test: `tests/test_credentials.py`

**Interfaces:**
- Produces: `KEYCHAIN_SERVICE = "notetaker"` constant; `get_provider_credential(key_name: str) -> str | None`; `set_provider_credential(key_name: str, value: str) -> None`; `mask_credential(value: str) -> str`.
- These are pure, provider-agnostic wrappers over `keyring` — no `Config`, no validation logic. All tests in this task mock `keyring.get_password`/`keyring.set_password` — never touch the real Keychain.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_credentials.py
from notetaker.credentials import KEYCHAIN_SERVICE, get_provider_credential, mask_credential, set_provider_credential


def test_get_provider_credential_returns_none_when_not_stored(monkeypatch):
    monkeypatch.setattr("notetaker.credentials.keyring.get_password", lambda service, key: None)
    assert get_provider_credential("ANTHROPIC_API_KEY") is None


def test_get_provider_credential_returns_stored_value(monkeypatch):
    calls = {}

    def fake_get_password(service, key):
        calls["args"] = (service, key)
        return "sk-ant-secret"

    monkeypatch.setattr("notetaker.credentials.keyring.get_password", fake_get_password)
    assert get_provider_credential("ANTHROPIC_API_KEY") == "sk-ant-secret"
    assert calls["args"] == (KEYCHAIN_SERVICE, "ANTHROPIC_API_KEY")


def test_set_provider_credential_stores_under_notetaker_service(monkeypatch):
    calls = {}

    def fake_set_password(service, key, value):
        calls["args"] = (service, key, value)

    monkeypatch.setattr("notetaker.credentials.keyring.set_password", fake_set_password)
    set_provider_credential("ANTHROPIC_API_KEY", "sk-ant-secret")
    assert calls["args"] == (KEYCHAIN_SERVICE, "ANTHROPIC_API_KEY", "sk-ant-secret")


def test_mask_credential_keeps_prefix_and_suffix():
    assert mask_credential("sk-ant-api03-abcdef1234") == "sk-ant••••1234"


def test_mask_credential_fully_masks_short_values():
    assert mask_credential("short") == "•••••"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_credentials.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notetaker.credentials'`

- [ ] **Step 3: Write minimal implementation**

```python
# notetaker/credentials.py
import keyring

KEYCHAIN_SERVICE = "notetaker"


def get_provider_credential(key_name: str) -> str | None:
    """Looks up a Provider credential from the macOS Keychain, keyed by the
    config's `api_key_env` name (e.g. "ANTHROPIC_API_KEY"). Returns None if
    nothing is stored.
    """
    return keyring.get_password(KEYCHAIN_SERVICE, key_name)


def set_provider_credential(key_name: str, value: str) -> None:
    """Stores a Provider credential in the macOS Keychain."""
    keyring.set_password(KEYCHAIN_SERVICE, key_name, value)


def mask_credential(value: str) -> str:
    """Masks a credential for display, keeping a short prefix and suffix
    visible (e.g. "sk-ant-api03-abcdef1234" -> "sk-ant••••1234") so a user
    can recognize which key is active without seeing the full value.
    """
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:6]}{'•' * 4}{value[-4:]}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_credentials.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add notetaker/credentials.py tests/test_credentials.py
git commit -m "feat: add credentials module wrapping the macOS Keychain"
```

---

### Task 3: `validate_claude_api_key` in `summarizer.py`

**Files:**
- Modify: `notetaker/summarizer.py`
- Test: `tests/test_summarizer.py`

**Interfaces:**
- Produces: `validate_claude_api_key(api_key: str) -> bool` — makes a minimal live call (`client.models.list()`) to confirm a Claude API key actually works, returning `False` for any failure (invalid key, network issue, etc. — the safe default for a "validate before save" flow). Does not raise.
- Tests mock `notetaker.summarizer.anthropic.Anthropic` itself with a lightweight fake class — do NOT try to construct real `anthropic.AuthenticationError`/`anthropic.APIError` instances (their constructors require real `httpx.Request`/`Response` objects, which is unnecessarily fragile for this test).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_summarizer.py` (near the existing `get_provider` tests at the bottom of the file):

```python
from notetaker.summarizer import validate_claude_api_key


def test_validate_claude_api_key_returns_true_when_call_succeeds(monkeypatch):
    class FakeModels:
        def list(self):
            return ["model-a"]

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr("notetaker.summarizer.anthropic.Anthropic", FakeClient)
    assert validate_claude_api_key("sk-ant-real-key") is True


def test_validate_claude_api_key_returns_false_when_call_raises(monkeypatch):
    class FakeModels:
        def list(self):
            raise RuntimeError("invalid x-api-key")

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr("notetaker.summarizer.anthropic.Anthropic", FakeClient)
    assert validate_claude_api_key("sk-ant-bad-key") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_summarizer.py -k validate_claude_api_key -v`
Expected: FAIL with `ImportError: cannot import name 'validate_claude_api_key'`

- [ ] **Step 3: Write minimal implementation**

In `notetaker/summarizer.py`, add this function after the `ClaudeProvider` class definition:

```python
def validate_claude_api_key(api_key: str) -> bool:
    """Confirms a Claude API key actually works via a minimal live API call.
    Returns False for any failure (invalid key, network issue, etc.) — the
    safe default for a "validate before save" flow.
    """
    try:
        client = anthropic.Anthropic(api_key=api_key)
        client.models.list()
        return True
    except Exception:
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_summarizer.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add notetaker/summarizer.py tests/test_summarizer.py
git commit -m "feat: add validate_claude_api_key for a live pre-save check"
```

---

### Task 4: Wire Keychain lookup into `get_provider`

**Files:**
- Modify: `notetaker/summarizer.py`
- Modify: `tests/test_summarizer.py`

**Interfaces:**
- Consumes: `get_provider_credential` from Task 2 (`notetaker.credentials`).
- Produces: `get_provider` now checks the Keychain first, falling back to the environment variable — `api_key = get_provider_credential(config.api_key_env) or os.environ.get(config.api_key_env)`.
- **CRITICAL:** the two existing `get_provider` claude-path tests must be updated to mock the Keychain lookup, or they will query this machine's real Keychain.

- [ ] **Step 1: Add the import**

In `notetaker/summarizer.py`, add this import near the top (alongside the existing `from notetaker.config import Config, ConfigError`):

```python
from notetaker.credentials import get_provider_credential
```

- [ ] **Step 2: Update `get_provider`**

Replace:

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

with:

```python
def get_provider(config: Config) -> Provider:
    if config.ai_provider == "claude":
        api_key = get_provider_credential(config.api_key_env) or os.environ.get(config.api_key_env)
        if not api_key:
            raise ConfigError(
                f"No credential found for '{config.api_key_env}'. Run `notetaker set-api-key <key>`, "
                "or export it as an environment variable."
            )
        return ClaudeProvider(api_key=api_key, model=config.ai_model)
    if config.ai_provider == "apple_local":
        return AppleLocalProvider(model=config.ai_model)
    raise ConfigError(f"Unknown ai_provider '{config.ai_provider}'.")
```

- [ ] **Step 3: Update the two existing tests to mock the Keychain lookup**

In `tests/test_summarizer.py`, replace:

```python
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
```

with:

```python
def test_get_provider_claude_reads_env_key(monkeypatch):
    monkeypatch.setattr("notetaker.summarizer.get_provider_credential", lambda key: None)
    monkeypatch.setenv("MY_KEY", "secret")
    config = Config(
        notes_dir=None, whisper_model="base.en", ai_provider="claude",
        ai_model="claude-sonnet-5", api_key_env="MY_KEY",
    )
    provider = get_provider(config)
    assert isinstance(provider, ClaudeProvider)


def test_get_provider_claude_reads_keychain_credential(monkeypatch):
    monkeypatch.setattr("notetaker.summarizer.get_provider_credential", lambda key: "sk-ant-from-keychain")
    monkeypatch.delenv("MY_KEY", raising=False)
    config = Config(
        notes_dir=None, whisper_model="base.en", ai_provider="claude",
        ai_model="claude-sonnet-5", api_key_env="MY_KEY",
    )
    provider = get_provider(config)
    assert isinstance(provider, ClaudeProvider)


def test_get_provider_claude_missing_env_key_raises(monkeypatch):
    monkeypatch.setattr("notetaker.summarizer.get_provider_credential", lambda key: None)
    monkeypatch.delenv("MISSING_KEY", raising=False)
    config = Config(
        notes_dir=None, whisper_model="base.en", ai_provider="claude",
        ai_model="claude-sonnet-5", api_key_env="MISSING_KEY",
    )
    with pytest.raises(ConfigError):
        get_provider(config)
```

Note the added `test_get_provider_claude_reads_keychain_credential` test, confirming the Keychain path itself works (not just that it's mocked away in the other two).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_summarizer.py -v`
Expected: all pass — in particular, confirm no test in this run makes a real network call or hangs (a hang here would mean a Keychain mock is missing)

- [ ] **Step 5: Commit**

```bash
git add notetaker/summarizer.py tests/test_summarizer.py
git commit -m "feat: check the Keychain before the environment variable in get_provider"
```

---

### Task 5: Wire Keychain lookup into `check_setup`

**Files:**
- Modify: `notetaker/service.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Consumes: `get_provider_credential` from Task 2 (`notetaker.credentials`).
- Produces: `check_setup`'s claude branch checks the Keychain first, matching `get_provider`'s precedence, with an updated `provider_problems` message.
- **CRITICAL:** the two existing `check_setup` claude-path tests must be updated to mock the Keychain lookup. One assertion's expected text also changes (the message wording is intentionally different now).

- [ ] **Step 1: Add the import**

In `notetaker/service.py`, add this import near the top (alongside the existing `from notetaker.config import Config, write_default_config`):

```python
from notetaker.credentials import get_provider_credential
```

- [ ] **Step 2: Update `check_setup`**

Replace:

```python
def check_setup(config: Config) -> SetupStatus:
    blackhole = check_blackhole()
    if config.ai_provider == "claude":
        if not os.environ.get(config.api_key_env):
            return SetupStatus(
                blackhole=blackhole,
                provider_ready=False,
                provider_problems=[
                    f"{config.api_key_env} is not set. Export it in your shell profile, "
                    "then re-run `notetaker init`."
                ],
            )
        return SetupStatus(blackhole=blackhole, provider_ready=True, provider_problems=[])
    if config.ai_provider == "apple_local":
        problems = check_apple_local_preflight()
        return SetupStatus(blackhole=blackhole, provider_ready=not problems, provider_problems=problems)
    raise ServiceError(f"Unknown ai_provider '{config.ai_provider}'.")
```

with:

```python
def check_setup(config: Config) -> SetupStatus:
    blackhole = check_blackhole()
    if config.ai_provider == "claude":
        if not (get_provider_credential(config.api_key_env) or os.environ.get(config.api_key_env)):
            return SetupStatus(
                blackhole=blackhole,
                provider_ready=False,
                provider_problems=[
                    f"No credential found for {config.api_key_env}. Run `notetaker set-api-key <key>`, "
                    "or export it as an environment variable, then re-run `notetaker init`."
                ],
            )
        return SetupStatus(blackhole=blackhole, provider_ready=True, provider_problems=[])
    if config.ai_provider == "apple_local":
        problems = check_apple_local_preflight()
        return SetupStatus(blackhole=blackhole, provider_ready=not problems, provider_problems=problems)
    raise ServiceError(f"Unknown ai_provider '{config.ai_provider}'.")
```

- [ ] **Step 3: Update the two existing tests**

In `tests/test_service.py`, replace:

```python
def test_check_setup_reports_blackhole_and_ready_claude_provider(monkeypatch, tmp_path):
    from notetaker.service import SetupStatus, check_setup
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    status = check_setup(_config(tmp_path))
    assert status == SetupStatus(blackhole=BlackHoleStatus.ACTIVE, provider_ready=True, provider_problems=[])


def test_check_setup_reports_missing_claude_api_key(monkeypatch, tmp_path):
    from notetaker.service import check_setup
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status = check_setup(_config(tmp_path))
    assert status.provider_ready is False
    assert "ANTHROPIC_API_KEY is not set" in status.provider_problems[0]
```

with:

```python
def test_check_setup_reports_blackhole_and_ready_claude_provider(monkeypatch, tmp_path):
    from notetaker.service import SetupStatus, check_setup
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.get_provider_credential", lambda key: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    status = check_setup(_config(tmp_path))
    assert status == SetupStatus(blackhole=BlackHoleStatus.ACTIVE, provider_ready=True, provider_problems=[])


def test_check_setup_reports_ready_when_keychain_has_credential(monkeypatch, tmp_path):
    from notetaker.service import SetupStatus, check_setup
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.get_provider_credential", lambda key: "sk-ant-from-keychain")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status = check_setup(_config(tmp_path))
    assert status == SetupStatus(blackhole=BlackHoleStatus.ACTIVE, provider_ready=True, provider_problems=[])


def test_check_setup_reports_missing_claude_api_key(monkeypatch, tmp_path):
    from notetaker.service import check_setup
    monkeypatch.setattr("notetaker.service.check_blackhole", lambda: BlackHoleStatus.ACTIVE)
    monkeypatch.setattr("notetaker.service.get_provider_credential", lambda key: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status = check_setup(_config(tmp_path))
    assert status.provider_ready is False
    assert "No credential found for ANTHROPIC_API_KEY" in status.provider_problems[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: all pass — confirm no test hangs or makes a real Keychain call

- [ ] **Step 5: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: check the Keychain before the environment variable in check_setup"
```

---

### Task 6: `save_provider_credential` and `get_masked_provider_credential` in `service.py`

**Files:**
- Modify: `notetaker/service.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Consumes: `set_provider_credential`, `get_provider_credential`, `mask_credential` from `notetaker.credentials`; `validate_claude_api_key` from `notetaker.summarizer`.
- Produces: `save_provider_credential(config: Config, api_key: str) -> None` — raises `ServiceError` if `config.ai_provider != "claude"` or if the key fails live validation, otherwise stores it; `get_masked_provider_credential(config: Config) -> str | None` — returns the masked stored credential, or `None` if nothing is stored.
- These two functions are named distinctly from `notetaker.credentials`'s `set_provider_credential`/`get_provider_credential` (imported into this same module) to avoid a naming collision — `service.py`'s versions add validation and masking on top of the raw Keychain operations.

- [ ] **Step 1: Add the imports**

In `notetaker/service.py`, add `mask_credential, set_provider_credential` to the existing `from notetaker.credentials import get_provider_credential` line (making it `from notetaker.credentials import get_provider_credential, mask_credential, set_provider_credential`), and add `validate_claude_api_key` to the existing `from notetaker.summarizer import ...` line.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_service.py`:

```python
def test_save_provider_credential_rejects_non_claude_provider(tmp_path):
    from notetaker.service import save_provider_credential

    config = Config(tmp_path, "tiny", "apple_local", "apple-foundationmodel", "UNUSED")
    with pytest.raises(ServiceError, match="only supported for the 'claude' provider"):
        save_provider_credential(config, "sk-ant-whatever")


def test_save_provider_credential_rejects_invalid_key(monkeypatch, tmp_path):
    from notetaker.service import save_provider_credential

    monkeypatch.setattr("notetaker.service.validate_claude_api_key", lambda key: False)
    with pytest.raises(ServiceError, match="rejected by Anthropic's API"):
        save_provider_credential(_config(tmp_path), "sk-ant-bad-key")


def test_save_provider_credential_stores_valid_key(monkeypatch, tmp_path):
    from notetaker.service import save_provider_credential

    calls = {}
    monkeypatch.setattr("notetaker.service.validate_claude_api_key", lambda key: True)
    monkeypatch.setattr(
        "notetaker.service.set_provider_credential", lambda key_name, value: calls.__setitem__("args", (key_name, value))
    )
    save_provider_credential(_config(tmp_path), "sk-ant-good-key")
    assert calls["args"] == ("ANTHROPIC_API_KEY", "sk-ant-good-key")


def test_get_masked_provider_credential_returns_none_when_unset(monkeypatch, tmp_path):
    from notetaker.service import get_masked_provider_credential

    monkeypatch.setattr("notetaker.service.get_provider_credential", lambda key: None)
    assert get_masked_provider_credential(_config(tmp_path)) is None


def test_get_masked_provider_credential_returns_masked_value(monkeypatch, tmp_path):
    from notetaker.service import get_masked_provider_credential

    monkeypatch.setattr("notetaker.service.get_provider_credential", lambda key: "sk-ant-api03-abcdef1234")
    assert get_masked_provider_credential(_config(tmp_path)) == "sk-ant••••1234"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -k provider_credential -v`
Expected: FAIL with `ImportError: cannot import name 'save_provider_credential'`

- [ ] **Step 4: Write minimal implementation**

```python
# notetaker/service.py (append)
def save_provider_credential(config: Config, api_key: str) -> None:
    if config.ai_provider != "claude":
        raise ServiceError(
            f"Setting a credential is only supported for the 'claude' provider (current: '{config.ai_provider}')."
        )
    if not validate_claude_api_key(api_key):
        raise ServiceError("That API key was rejected by Anthropic's API — check it and try again.")
    set_provider_credential(config.api_key_env, api_key)


def get_masked_provider_credential(config: Config) -> str | None:
    value = get_provider_credential(config.api_key_env)
    if value is None:
        return None
    return mask_credential(value)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: add save_provider_credential and get_masked_provider_credential"
```

---

### Task 7: CLI `set-api-key` and `show-api-key` commands

**Files:**
- Modify: `notetaker/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `service.save_provider_credential(config, api_key)`, `service.get_masked_provider_credential(config)` from Task 6.
- Produces: two new top-level CLI commands, `set-api-key <api_key>` and `show-api-key`, following the existing flat command style (no sub-command groups).

- [ ] **Step 1: Add the commands to `notetaker/cli.py`**

Append after the `show` command:

```python
@app.command("set-api-key")
def set_api_key(api_key: str):
    config = load_config()
    try:
        service.save_provider_credential(config, api_key)
    except ServiceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"{config.api_key_env} saved to the macOS Keychain.")


@app.command("show-api-key")
def show_api_key():
    config = load_config()
    masked = service.get_masked_provider_credential(config)
    if masked is None:
        typer.echo(f"No credential stored for {config.api_key_env}.")
    else:
        typer.echo(masked)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_set_api_key_reports_service_error(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))

    def fail(config, api_key):
        raise ServiceError("That API key was rejected by Anthropic's API — check it and try again.")

    monkeypatch.setattr("notetaker.cli.service.save_provider_credential", fail)

    result = runner.invoke(app, ["set-api-key", "sk-ant-bad-key"])

    assert result.exit_code == 1
    assert "rejected by Anthropic's API" in result.output


def test_set_api_key_prints_confirmation_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.cli.service.save_provider_credential", lambda config, api_key: None)

    result = runner.invoke(app, ["set-api-key", "sk-ant-good-key"])

    assert result.exit_code == 0
    assert "ANTHROPIC_API_KEY saved to the macOS Keychain." in result.output


def test_show_api_key_prints_masked_value(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.cli.service.get_masked_provider_credential", lambda config: "sk-ant••••1234")

    result = runner.invoke(app, ["show-api-key"])

    assert result.exit_code == 0
    assert "sk-ant••••1234" in result.output


def test_show_api_key_reports_when_unset(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("notetaker.cli.service.get_masked_provider_credential", lambda config: None)

    result = runner.invoke(app, ["show-api-key"])

    assert result.exit_code == 0
    assert "No credential stored for ANTHROPIC_API_KEY." in result.output
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -k api_key -v`
Expected: FAIL — no such command 'set-api-key'

- [ ] **Step 4: Run the full fast suite to verify they pass**

Run: `.venv/bin/pytest -m "not integration" -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add notetaker/cli.py tests/test_cli.py
git commit -m "feat: add set-api-key and show-api-key CLI commands"
```

---

### Task 8: `notes.py` — extract `_render_note_body`, add `rewrite_note_summary`

**Files:**
- Modify: `notetaker/notes.py`
- Modify: `tests/test_notes.py`

**Interfaces:**
- Produces: a private `_render_note_body(title, start_time, duration_minutes, summary, transcript_lines) -> str` helper (the exact markdown-construction logic extracted verbatim from `write_note`); `rewrite_note_summary(path: Path, summary: Summary, transcript_lines: list[str]) -> None` — re-renders an existing Note at the same path, keeping its title/date/duration_minutes (read back via `parse_note_meta`) but with a new Summary/Action Items/tags and the given Transcript lines.
- This is a behavior-preserving refactor of `write_note`: its existing tests must keep passing unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_notes.py` (check the file's existing imports first — it likely already imports `write_note`, `Summary`, `datetime`, `Path` for its own tests; add `rewrite_note_summary` to whatever import statement brings in `write_note`):

```python
def test_rewrite_note_summary_replaces_summary_keeping_title_and_date(tmp_path):
    notes_dir = tmp_path / "notes"
    path = write_note(
        notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5,
        Summary("old summary", ["old item"], ["old-tag"]), ["[00:00:01] hello"],
    )

    rewrite_note_summary(path, Summary("new summary", ["new item"], ["new-tag"]), ["[00:00:01] hello"])

    text = path.read_text()
    assert "new summary" in text
    assert "new item" in text
    assert "new-tag" in text
    assert "old summary" not in text
    assert "title: Standup" in text
    assert "duration_minutes: 5" in text


def test_rewrite_note_summary_does_not_change_the_note_id(tmp_path):
    notes_dir = tmp_path / "notes"
    path = write_note(
        notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5,
        Summary("old summary", [], []), ["[00:00:01] hello"],
    )
    original_path = path

    rewrite_note_summary(path, Summary("new summary", [], []), ["[00:00:01] hello"])

    assert path == original_path
    assert len(list(notes_dir.glob("*.md"))) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_notes.py -k rewrite_note_summary -v`
Expected: FAIL with `ImportError: cannot import name 'rewrite_note_summary'`

- [ ] **Step 3: Write minimal implementation**

Replace `write_note` in `notetaker/notes.py`:

```python
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
```

with:

```python
def _render_note_body(
    title: str,
    start_time: datetime,
    duration_minutes: int,
    summary: Summary,
    transcript_lines: list[str],
) -> str:
    frontmatter = {
        "title": title,
        "date": start_time.isoformat(),
        "duration_minutes": duration_minutes,
        "tags": summary.tags,
    }
    action_items_md = "\n".join(f"- [ ] {item}" for item in summary.action_items) or "- (none)"
    transcript_md = "\n".join(transcript_lines) or "(no transcript captured)"
    return (
        f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n\n"
        f"## Summary\n{summary.text}\n\n"
        f"## Action Items\n{action_items_md}\n\n"
        f"## Transcript\n{transcript_md}\n"
    )


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
    path = notes_dir / f"{note_id}.md"
    path.write_text(_render_note_body(title, start_time, duration_minutes, summary, transcript_lines))
    return path


def rewrite_note_summary(path: Path, summary: Summary, transcript_lines: list[str]) -> None:
    """Replaces an existing Note's Summary, Action Items, and tags at the
    same path — keeping its title/date/duration_minutes unchanged — by
    re-rendering the note body. Used by Resummarize.
    """
    meta = parse_note_meta(path)
    path.write_text(_render_note_body(meta.title, meta.date, meta.duration_minutes, summary, transcript_lines))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_notes.py -v`
Expected: all pass, including every pre-existing `write_note`/`list_notes`/`parse_note_meta` test (confirming the extraction didn't change `write_note`'s behavior)

- [ ] **Step 5: Commit**

```bash
git add notetaker/notes.py tests/test_notes.py
git commit -m "feat: extract _render_note_body and add rewrite_note_summary"
```

---

### Task 9: `service.py` — extract `_summarize_or_fallback`, add `resummarize_note`

**Files:**
- Modify: `notetaker/service.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Consumes: `rewrite_note_summary` from Task 8 (`notetaker.notes`).
- Produces: a private `_summarize_or_fallback(transcript: str, config: Config) -> Summary` helper (the exact "empty transcript → fallback Summary; otherwise summarize, catching any Exception into a failure Summary" logic extracted verbatim from `stop_session`); `resummarize_note(config: Config, note_id: str) -> Path` — raises `ServiceError` if the note doesn't exist or has no persisted transcript sidecar (a note from before the sidecar existed), otherwise re-summarizes from the sidecar and calls `rewrite_note_summary`, returning the note's path.
- This is a behavior-preserving refactor of `stop_session`: its existing tests must keep passing unchanged.

- [ ] **Step 1: Add the import**

In `notetaker/service.py`, add `rewrite_note_summary` to the existing `from notetaker.notes import ...` line, making it: `from notetaker.notes import NoteMeta, find_note_path, list_notes, read_note_body, rewrite_note_summary, write_note`.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_service.py`:

```python
def test_resummarize_note_raises_for_missing_note(tmp_path):
    from notetaker.service import resummarize_note

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    with pytest.raises(ServiceError, match="no note found"):
        resummarize_note(Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), "nonexistent")


def test_resummarize_note_raises_when_no_transcript_sidecar(tmp_path):
    from notetaker.service import resummarize_note

    notes_dir = tmp_path / "notes"
    write_note(notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("old", [], []), ["hello"])
    # write_note alone doesn't create the sidecar — only stop_session does — so this note has none
    with pytest.raises(ServiceError, match="no persisted transcript"):
        resummarize_note(
            Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), "2026-09-11-standup"
        )


def test_resummarize_note_replaces_summary_from_sidecar(monkeypatch, tmp_path):
    from notetaker.service import resummarize_note

    notes_dir = tmp_path / "notes"
    note_path = write_note(
        notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("old summary", [], []), ["[00:00:01] hello"]
    )
    (notes_dir / f"{note_path.stem}.transcript.txt").write_text("[00:00:01] hello\n")

    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())
    monkeypatch.setattr(
        "notetaker.service.summarize_transcript",
        lambda transcript, provider: Summary(text="new summary", action_items=["new item"], tags=["new-tag"]),
    )

    result_path = resummarize_note(
        Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), "2026-09-11-standup"
    )

    assert result_path == note_path
    text = note_path.read_text()
    assert "new summary" in text
    assert "old summary" not in text


def test_resummarize_note_saves_fallback_summary_when_provider_fails(monkeypatch, tmp_path):
    from notetaker.service import resummarize_note

    notes_dir = tmp_path / "notes"
    note_path = write_note(
        notes_dir, "Standup", datetime(2026, 9, 11, 10, 0), 5, Summary("old summary", [], []), ["[00:00:01] hello"]
    )
    (notes_dir / f"{note_path.stem}.transcript.txt").write_text("[00:00:01] hello\n")

    monkeypatch.setattr("notetaker.service.get_provider", lambda config: object())

    def raise_error(transcript, provider):
        raise RuntimeError("network down")

    monkeypatch.setattr("notetaker.service.summarize_transcript", raise_error)

    resummarize_note(Config(notes_dir, "tiny", "claude", "claude-sonnet-5", "ANTHROPIC_API_KEY"), "2026-09-11-standup")

    assert "Summarization failed" in note_path.read_text()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service.py -k resummarize_note -v`
Expected: FAIL with `ImportError: cannot import name 'resummarize_note'`

- [ ] **Step 4: Write minimal implementation**

First, extract the summarization logic. Replace `stop_session`'s body from:

```python
    if not transcript.strip():
        summary = Summary(text="No audio was captured for this session.", action_items=[], tags=[])
    else:
        try:
            provider = get_provider(config)
            summary = summarize_transcript(transcript, provider)
        except Exception as exc:
            summary = Summary(text=f"Summarization failed: {exc}", action_items=[], tags=[])
```

with:

```python
    summary = _summarize_or_fallback(transcript, config)
```

Then add the extracted helper just above `stop_session`:

```python
def _summarize_or_fallback(transcript: str, config: Config) -> Summary:
    if not transcript.strip():
        return Summary(text="No audio was captured for this session.", action_items=[], tags=[])
    try:
        provider = get_provider(config)
        return summarize_transcript(transcript, provider)
    except Exception as exc:
        return Summary(text=f"Summarization failed: {exc}", action_items=[], tags=[])
```

Then add `resummarize_note`, near `get_note_body`:

```python
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
    transcript = sidecar_path.read_text()
    summary = _summarize_or_fallback(transcript, config)
    rewrite_note_summary(note_path, summary, transcript.splitlines())
    return note_path
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service.py -v`
Expected: all pass, including every pre-existing `stop_session` test (confirming the extraction didn't change its behavior)

- [ ] **Step 6: Commit**

```bash
git add notetaker/service.py tests/test_service.py
git commit -m "feat: extract _summarize_or_fallback and add resummarize_note"
```

---

### Task 10: CLI `resummarize` command

**Files:**
- Modify: `notetaker/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `service.resummarize_note(config, note_id) -> Path` from Task 9.
- Produces: `notetaker resummarize <note_id>`.

- [ ] **Step 1: Add the command to `notetaker/cli.py`**

Append after `show_api_key`:

```python
@app.command()
def resummarize(note_id: str):
    config = load_config()
    try:
        note_path = service.resummarize_note(config, note_id)
    except ServiceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Resummarized note: {note_path}")
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_resummarize_reports_service_error(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))

    def fail(config, note_id):
        raise ServiceError(f"no note found with id '{note_id}'.")

    monkeypatch.setattr("notetaker.cli.service.resummarize_note", fail)

    result = runner.invoke(app, ["resummarize", "nonexistent"])

    assert result.exit_code == 1
    assert "no note found" in result.output


def test_resummarize_prints_confirmation_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr("notetaker.cli.load_config", lambda: _config(tmp_path))
    note_path = tmp_path / "notes" / "2026-09-11-standup.md"
    monkeypatch.setattr("notetaker.cli.service.resummarize_note", lambda config, note_id: note_path)

    result = runner.invoke(app, ["resummarize", "2026-09-11-standup"])

    assert result.exit_code == 0
    assert f"Resummarized note: {note_path}" in result.output
```

- [ ] **Step 3: Run the full fast suite**

Run: `.venv/bin/pytest -m "not integration" -q`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add notetaker/cli.py tests/test_cli.py
git commit -m "feat: add resummarize CLI command"
```

---

### Task 11: Update `CLAUDE.md`'s architecture summary

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- None — documentation only.

- [ ] **Step 1: Update the module list**

In `CLAUDE.md`'s "Planned architecture" module list, replace:

```markdown
- `cli.py` — command dispatch (Typer) for `init`, `start`, `stop`, `list`, `show`.
```

with:

```markdown
- `cli.py` — command dispatch (Typer) for `init`, `start`, `stop`, `list`, `show`, `resummarize`, `set-api-key`, `show-api-key`.
```

Then, after the `service.py` bullet, add a new bullet:

```markdown
- `credentials.py` — provider-agnostic Keychain read/write/masking (`get_provider_credential`, `set_provider_credential`, `mask_credential`). Never stores a Provider credential in `config.yaml` or any plaintext file. `get_provider`/`check_setup` check the Keychain first, falling back to the credential's environment variable for backward compatibility.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document credentials.py and resummarize in the architecture summary"
```

---

## Self-Review Notes

- **Spec coverage**: This plan implements the spec's "Provider credential management" and "Resummarize" items in full. It deliberately excludes CLI spinners (split into a smaller follow-up plan) and the dashboard's masked-display UI (Plan 5's job — this plan only builds the service-layer/CLI seam the dashboard will call).
- **Test-isolation discipline carried forward**: every task touching `get_provider`/`check_setup`'s claude branch explicitly mocks the Keychain lookup, matching the lesson from the prior plan's `~/.notetaker` isolation requirement — the same class of risk, a different real-system resource (Keychain instead of the filesystem).
- **Type consistency checked**: `save_provider_credential(config, api_key)`, `get_masked_provider_credential(config)`, `resummarize_note(config, note_id) -> Path`, `rewrite_note_summary(path, summary, transcript_lines)` are used identically across every task and in the `cli.py` dispatch.
- **No placeholders**: every step shows the exact code to write.
- **DRY extractions verified non-premature**: `_summarize_or_fallback` and `_render_note_body` each now have two real call sites (`stop_session`/`resummarize_note`, and `write_note`/`rewrite_note_summary` respectively) — matching the bar Plan 2's `_terminate_recorder` extraction set.
