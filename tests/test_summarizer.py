import json
from unittest.mock import MagicMock

from notetaker.summarizer import (
    ClaudeProvider,
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
    result = summarize_transcript(transcript, provider, chunk_token_limit=135)
    assert result.text == "final"
    assert result.tags == ["t1", "t2", "t3"]
    assert result.action_items == ["a1", "a2", "a3"]
    assert len(provider.calls) == 3


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
