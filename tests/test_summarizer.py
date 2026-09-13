import io
import json
from unittest.mock import MagicMock

from notetaker.summarizer import (
    AppleLocalError,
    AppleLocalProvider,
    ClaudeProvider,
    Summary,
    check_apple_local_preflight,
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
    result = summarize_transcript(transcript, provider, chunk_token_limit=135)
    assert result.text == "final"
    assert result.tags == ["t1", "t2", "t3"]
    assert result.action_items == ["a1", "a2", "a3"]
    assert len(provider.calls) == 3


def test_summarize_transcript_reduce_step_recurses_when_combined_exceeds_limit():
    # 3 lines, each a single token-heavy "line" so chunk_transcript splits them
    # into 3 separate initial chunks (each comfortably under the char limit).
    lines = ["a" * 160, "b" * 160, "c" * 160]
    transcript = "\n".join(lines)
    chunk_token_limit = 50
    max_chars = chunk_token_limit * 4

    long_partial_text = "x" * 70  # combined (3 * 70 + separators) exceeds 50 tokens
    responses = [
        Summary(text=long_partial_text, action_items=[], tags=["t1"]),
        Summary(text=long_partial_text, action_items=[], tags=["t2"]),
        Summary(text=long_partial_text, action_items=[], tags=["t3"]),
        Summary(text="ns1", action_items=[], tags=["nt1"]),
        Summary(text="ns2", action_items=[], tags=["nt2"]),
        Summary(text="final", action_items=["fa"], tags=["ft"]),
    ]
    provider = FakeProvider(responses)

    result = summarize_transcript(transcript, provider, chunk_token_limit=chunk_token_limit)

    assert result.text == "final"
    assert len(provider.calls) == 6
    assert all(len(call) <= max_chars for call in provider.calls)


def test_claude_provider_parses_json_response(monkeypatch):
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [
        MagicMock(type="text", text=json.dumps({"text": "summary", "action_items": ["do x"], "tags": ["standup"]}))
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


def test_claude_provider_parses_json_response_wrapped_in_markdown_fences(monkeypatch):
    fake_client = MagicMock()
    fake_response = MagicMock()
    fenced = "```json\n" + json.dumps({"text": "summary", "action_items": ["do x"], "tags": ["standup"]}) + "\n```"
    fake_response.content = [MagicMock(type="text", text=fenced)]
    fake_client.messages.create.return_value = fake_response
    monkeypatch.setattr("notetaker.summarizer.anthropic.Anthropic", lambda api_key: fake_client)

    provider = ClaudeProvider(api_key="fake-key", model="claude-sonnet-5")
    result = provider.summarize("[00:00:01] hello")

    assert result.text == "summary"
    assert result.action_items == ["do x"]
    assert result.tags == ["standup"]
    assert fake_client.messages.create.call_args.kwargs["max_tokens"] == 16000


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

    def fake_run(cmd, **kwargs):
        if cmd[-1] == "--model-info":
            return sp.CompletedProcess(cmd, returncode=0, stdout="available:  yes\n")
        return sp.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr("notetaker.summarizer.subprocess.run", fake_run)

    class FakeCtx:
        def __enter__(self):
            return io.BytesIO(b"{}")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("notetaker.summarizer.urllib.request.urlopen", lambda req, timeout=2: FakeCtx())
    assert check_apple_local_preflight() == []


def test_check_apple_local_preflight_flags_model_not_available(monkeypatch):
    import subprocess as sp

    monkeypatch.setattr("notetaker.summarizer.platform.system", lambda: "Darwin")
    monkeypatch.setattr("notetaker.summarizer.platform.mac_ver", lambda: ("26.0", ("", "", ""), ""))
    monkeypatch.setattr("notetaker.summarizer.shutil.which", lambda name: "/usr/local/bin/brew")

    def fake_run(cmd, **kwargs):
        if cmd[-1] == "--model-info":
            return sp.CompletedProcess(
                cmd, returncode=0, stdout="available:  no (Apple Intelligence not enabled)\n"
            )
        return sp.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr("notetaker.summarizer.subprocess.run", fake_run)

    class FakeCtx:
        def __enter__(self):
            return io.BytesIO(b"{}")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("notetaker.summarizer.urllib.request.urlopen", lambda req, timeout=2: FakeCtx())
    problems = check_apple_local_preflight()
    assert any("Apple Intelligence" in p for p in problems)


# Tests for get_provider
import pytest

from notetaker.config import Config, ConfigError
from notetaker.summarizer import AppleLocalProvider, ClaudeProvider, get_provider


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


def test_get_provider_claude_degrades_to_env_key_when_keychain_raises(monkeypatch):
    from keyring.errors import KeyringError

    def raise_keyring_error(key):
        raise KeyringError("Keychain locked")

    monkeypatch.setattr("notetaker.summarizer.get_provider_credential", raise_keyring_error)
    monkeypatch.setenv("MY_KEY", "secret")
    config = Config(
        notes_dir=None, whisper_model="base.en", ai_provider="claude",
        ai_model="claude-sonnet-5", api_key_env="MY_KEY",
    )
    provider = get_provider(config)
    assert isinstance(provider, ClaudeProvider)


def test_get_provider_apple_local():
    config = Config(
        notes_dir=None, whisper_model="base.en", ai_provider="apple_local",
        ai_model="apple-foundationmodel", api_key_env="UNUSED",
    )
    assert isinstance(get_provider(config), AppleLocalProvider)


# Tests for validate_claude_api_key
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


def test_claude_provider_skips_thinking_blocks(monkeypatch):
    # Current Claude models think by default, so content[0] is a thinking block.
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.stop_reason = "end_turn"
    fake_response.content = [
        MagicMock(type="thinking", thinking="..."),
        MagicMock(type="text", text=json.dumps({"text": "summary", "action_items": None, "tags": "solo"})),
    ]
    fake_client.messages.create.return_value = fake_response
    monkeypatch.setattr("notetaker.summarizer.anthropic.Anthropic", lambda api_key: fake_client)

    result = ClaudeProvider(api_key="k", model="claude-sonnet-5").summarize("hi")

    assert result.text == "summary"
    assert result.action_items == []  # null coerced, not passed through
    assert result.tags == ["solo"]


def test_claude_provider_raises_when_cut_off_at_max_tokens(monkeypatch):
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.stop_reason = "max_tokens"
    fake_response.content = [MagicMock(type="text", text='{"text": "trunc')]
    fake_client.messages.create.return_value = fake_response
    monkeypatch.setattr("notetaker.summarizer.anthropic.Anthropic", lambda api_key: fake_client)

    with pytest.raises(ValueError, match="max_tokens"):
        ClaudeProvider(api_key="k", model="claude-sonnet-5").summarize("hi")


def test_summarize_transcript_strips_timestamps_and_uses_provider_chunk_limit():
    class TinyContextProvider:
        chunk_token_limit = 8  # ~32 chars per chunk

        def __init__(self):
            self.calls = []

        def summarize(self, transcript):
            self.calls.append(transcript)
            return Summary(text="s", action_items=[], tags=[])

    provider = TinyContextProvider()
    transcript = "\n".join(f"[00:00:{i:02d}] Me: line {i}" for i in range(6))

    summarize_transcript(transcript, provider)

    assert all("[00:00" not in call for call in provider.calls)
    assert "Me: line 0" in provider.calls[0]
    assert len(provider.calls) > 1  # chunked at the provider's limit, not the 3000 default


def test_summarize_transcript_reports_progress_per_provider_call():
    class P:
        def summarize(self, transcript):
            return Summary(text="s", action_items=[], tags=[])

    progress = []
    transcript = "\n".join("word " * 20 for _ in range(10))
    summarize_transcript(transcript, P(), chunk_token_limit=60, on_progress=lambda d, t: progress.append((d, t)))

    assert progress[-1][0] == progress[-1][1]  # finished
    assert progress[-1][1] > 1  # chunked: partials + a reduce call
    assert [d for d, _ in progress] == list(range(1, len(progress) + 1))


def test_parse_summary_json_extracts_object_from_surrounding_prose():
    from notetaker.summarizer import _parse_summary_json

    raw = 'Here are the minutes:\n{"text": "t", "action_items": ["Me: send deck"], "tags": ["x"]}\nHope this helps!'
    assert _parse_summary_json(raw)["action_items"] == ["Me: send deck"]


def test_apple_local_provider_uses_small_context_chunk_limit():
    from notetaker.summarizer import AppleLocalProvider, ClaudeProvider

    assert AppleLocalProvider.chunk_token_limit < 2048
    assert ClaudeProvider.chunk_token_limit > AppleLocalProvider.chunk_token_limit


def test_summary_prompt_asks_for_minutes_with_owners():
    from notetaker.summarizer import SUMMARY_PROMPT_TEMPLATE

    prompt = SUMMARY_PROMPT_TEMPLATE.format(transcript="x")
    assert "Owner: task" in prompt
    assert "decisions" in prompt
