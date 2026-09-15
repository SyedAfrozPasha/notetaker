"""The on-device model's JSON is best-effort: tolerate trailing commas and
self-bulleted lists, and sample again when it is unparseable."""

import io
import json

import pytest

from notetaker.summarizer import AppleLocalError, AppleLocalProvider, _parse_summary_json, _summary_from_data


def test_parse_summary_json_tolerates_trailing_commas():
    raw = '{"discussion": ["a", "b",], "action_items": [], "tags": ["x",],}'

    assert _parse_summary_json(raw) == {"discussion": ["a", "b"], "action_items": [], "tags": ["x"]}


def test_parse_summary_json_tolerates_fence_prose_and_trailing_comma_together():
    raw = 'Here you go:\n```json\n{"discussion": "t", "action_items": ["- one",], "tags": []}\n```'

    assert _parse_summary_json(raw)["action_items"] == ["- one"]


def test_parse_summary_json_tolerates_a_missing_comma_between_array_items():
    raw = '{"discussion": ["a"], "action_items": ["one"\n"two"], "tags": []}'

    assert _parse_summary_json(raw)["action_items"] == ["one", "two"]


def test_summary_from_data_requires_discussion_key():
    with pytest.raises(ValueError, match="discussion"):
        _summary_from_data({"tags": ["x"]})


def test_action_items_and_tags_lose_the_models_own_bullets():
    summary = _summary_from_data(
        {"discussion": ["t"], "action_items": ["- Me: do it", "• Others: review"], "tags": ["- ai"]}
    )

    assert summary.action_items == ["Me: do it", "Others: review"]
    assert summary.tags == ["ai"]


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _apfel_answers(monkeypatch, contents: list[str]):
    """Makes successive apfel calls return the given raw message contents."""
    queue = list(contents)
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(json.loads(request.data))
        body = {"choices": [{"message": {"content": queue.pop(0)}}]}
        return _FakeResponse(json.dumps(body).encode())

    monkeypatch.setattr("notetaker.summarizer.urllib.request.urlopen", fake_urlopen)
    return calls


def test_apple_provider_retries_when_the_model_returns_malformed_json(monkeypatch):
    calls = _apfel_answers(
        monkeypatch,
        ['{"discussion": ["a", "b"', '{"discussion": ["fine"], "action_items": [], "tags": ["t"]}'],
    )

    summary = AppleLocalProvider().summarize("transcript")

    assert "fine" in summary.text
    assert len(calls) == 2


def test_apple_provider_gives_up_after_three_malformed_answers(monkeypatch):
    calls = _apfel_answers(monkeypatch, ["nope", "{broken", '{"no_discussion_key": 1}'])

    with pytest.raises(AppleLocalError, match="3 times in a row"):
        AppleLocalProvider().summarize("transcript")

    assert len(calls) == 3
