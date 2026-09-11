import json
from dataclasses import dataclass
from typing import Protocol

import anthropic


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
