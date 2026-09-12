import json
import os
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

import anthropic

from notetaker.config import Config, ConfigError


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
    if estimate_tokens(combined_text) > chunk_token_limit:
        reduced = summarize_transcript(combined_text, provider, chunk_token_limit)
    else:
        reduced = provider.summarize(combined_text)
    tags = sorted({tag for p in partials for tag in p.tags} | set(reduced.tags))
    action_items = _dedupe_preserve_order(
        [item for p in partials for item in p.action_items] + reduced.action_items
    )
    return Summary(text=reduced.text, action_items=action_items, tags=tags)


def _parse_summary_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


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
            max_tokens=4096,
            messages=[
                {"role": "user", "content": SUMMARY_PROMPT_TEMPLATE.format(transcript=transcript)}
            ],
        )
        data = _parse_summary_json(response.content[0].text)
        return Summary(
            text=data["text"],
            action_items=data.get("action_items", []),
            tags=data.get("tags", []),
        )


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


APPLE_LOCAL_BASE_URL = "http://localhost:11434/v1"


class AppleLocalError(Exception):
    pass


class AppleLocalProvider:
    def __init__(self, base_url: str = APPLE_LOCAL_BASE_URL, model: str = "apple-foundationmodel"):
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
        parsed = _parse_summary_json(raw)
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

    model_info = subprocess.run(["apfel", "--model-info"], capture_output=True, text=True)
    if model_info.returncode == 0:
        for line in model_info.stdout.splitlines():
            if "available:" in line and "yes" not in line:
                reason = line.split("available:", 1)[1].strip()
                problems.append(
                    f"apple_local model is not available ({reason}). Enable Apple Intelligence "
                    "in System Settings > Apple Intelligence & Siri, set Device Language and Siri "
                    "Language to the same supported language, and wait for the on-device model "
                    "to download (~3-4GB)."
                )
                break

    return problems


def get_provider(config: Config) -> Provider:
    if config.ai_provider == "claude":
        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            raise ConfigError(f"Environment variable {config.api_key_env} is not set.")
        return ClaudeProvider(api_key=api_key, model=config.ai_model)
    if config.ai_provider == "apple_local":
        return AppleLocalProvider(model=config.ai_model)
    raise ConfigError(f"Unknown ai_provider '{config.ai_provider}'.")
