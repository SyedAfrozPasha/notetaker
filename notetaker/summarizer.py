import json
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
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
