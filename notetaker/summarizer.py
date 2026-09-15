import json
import os
import re
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol

from keyring.errors import KeyringError

from notetaker.config import Config, ConfigError
from notetaker.credentials import get_provider_credential


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


DEFAULT_CHUNK_TOKEN_LIMIT = 3000

_TIMESTAMP_PREFIX = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] ?", re.MULTILINE)
# Lines the recorder writes about itself ("[warning] ...", "[note] ...",
# "[transcription failed for chunk 3: ...]", "[recording stopped due to error: ...]").
_STATUS_LINE = re.compile(r"^\[(warning|note|transcription failed[^\]]*|recording stopped[^\]]*)\].*$", re.MULTILINE)


def strip_timestamps(transcript: str) -> str:
    """Drops the `[hh:mm:ss] ` prefix from every line, and drops the
    recorder's own status lines entirely. Timestamps are useless to the
    summarizer and cost ~5 tokens per line — on a 4K-context on-device model
    that is a large share of the budget — and a small model will happily
    quote "[warning] no meeting audio detected..." into the minutes.
    """
    without_status = _STATUS_LINE.sub("", transcript)
    stripped = _TIMESTAMP_PREFIX.sub("", without_status)
    return "\n".join(line for line in stripped.splitlines() if line.strip())


_SPEAKER_LABEL = re.compile(r"^(?:Me|Others):\s*")
ECHO_MIN_CHARS = 25  # shorter lines ("Yes.", "everywhere.") are too common to count as copying
ECHO_FALLBACK_TEXT = (
    "Summary unavailable: the AI model repeated the transcript instead of summarizing it. "
    "Use Resummarize to try again."
)


def _normalize(line: str) -> str:
    return " ".join(line.lower().split())


def clean_summary_text(text: str, transcript: str) -> str:
    """Small on-device models sometimes hand back the transcript itself —
    speaker labels and all — as the "minutes". Strip the `Me:`/`Others:`
    labels, drop every line copied verbatim from the transcript, and when
    that was most of the text, say so instead of presenting a transcript as
    a summary (the note still has the real transcript; Resummarize retries).
    """
    spoken = {
        _normalize(_SPEAKER_LABEL.sub("", line.strip()))
        for line in strip_timestamps(transcript).splitlines()
        if line.strip()
    }
    kept: list[str] = []
    copied_chars = total_chars = 0
    for line in text.splitlines():
        bare = _SPEAKER_LABEL.sub("", line.strip())
        total_chars += len(bare)
        if len(bare) >= ECHO_MIN_CHARS and _normalize(bare) in spoken:
            copied_chars += len(bare)
            continue
        if bare or (kept and kept[-1]):  # collapse runs of blank lines
            kept.append(bare)
    result = "\n".join(kept).strip()
    if not result or (total_chars and copied_chars / total_chars >= 0.5):
        return ECHO_FALLBACK_TEXT
    return result


def summarize_transcript(
    transcript: str,
    provider: Provider,
    chunk_token_limit: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> Summary:
    """Map-reduce summarization. The chunk size defaults to the provider's
    own `chunk_token_limit` (an on-device model has a far smaller context
    than a cloud one); `on_progress(done, total)` is called after each
    provider call so a UI can show "Summarizing chunk 3 of 7".
    """
    if chunk_token_limit is None:
        chunk_token_limit = getattr(provider, "chunk_token_limit", DEFAULT_CHUNK_TOKEN_LIMIT)
    transcript = strip_timestamps(transcript)
    chunks = chunk_transcript(transcript, chunk_token_limit)
    total = len(chunks) + (1 if len(chunks) > 1 else 0)
    done = 0

    def _summarize(text: str) -> Summary:
        nonlocal done
        result = provider.summarize(text)
        done += 1
        if on_progress:
            on_progress(done, total)
        return result

    if len(chunks) == 1:
        single = _summarize(chunks[0])
        return Summary(
            text=clean_summary_text(single.text, transcript), action_items=single.action_items, tags=single.tags
        )
    partials = [_summarize(chunk) for chunk in chunks]
    combined_text = "\n\n".join(p.text for p in partials)
    if estimate_tokens(combined_text) > chunk_token_limit:
        reduced = summarize_transcript(combined_text, provider, chunk_token_limit)
    else:
        reduced = _summarize(combined_text)
    tags = sorted({tag for p in partials for tag in p.tags} | set(reduced.tags))
    action_items = _dedupe_preserve_order(
        [item for p in partials for item in p.action_items] + reduced.action_items
    )
    return Summary(text=clean_summary_text(reduced.text, transcript), action_items=action_items, tags=tags)


def _parse_summary_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Small models often wrap the JSON in prose ("Here is the summary: {...}").
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # ... leave trailing commas ("...", ] / "...", }), forget the comma
        # between two adjacent items in an array ("a"\n"b"), or close the
        # last array of the object with "}" instead of "]" (or drop a
        # closer entirely) — all cheap to repair without writing a full
        # JSON parser.
        repaired = _fix_mismatched_brackets(
            _insert_missing_commas(_TRAILING_COMMA.sub(r"\1", candidate))
        )
        return json.loads(repaired)


_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def _fix_mismatched_brackets(text: str) -> str:
    """Tracks the stack of open `{`/`[` outside string literals and, for
    each `}`/`]` closer actually present, swaps it for whichever the stack
    expects — the model sometimes closes the last array in the object with
    "}" instead of "]". Any closers still owed once the text runs out
    (the model stopped one short) are appended at the end.
    """
    out = list(text)
    stack: list[str] = []  # expected closer for each currently-open bracket
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if not stack:
                continue
            if stack[-1] != ch:
                out[i] = stack[-1]
            stack.pop()
    if stack:
        out.append("".join(reversed(stack)))
    return "".join(out)


def _insert_missing_commas(text: str) -> str:
    """Inserts a comma wherever a JSON value ends (a closing quote, `]`, or
    `}`) and, after only whitespace, another value starts with no comma
    between them. Scans character by character tracking string-literal
    state (respecting `\\` escapes) so it never touches text inside a
    string, unlike a plain regex.
    """
    out: list[str] = []
    in_string = False
    escape = False
    last_end = ""  # last closer emitted outside a string: '"', ']', or '}'
    for ch in text:
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
                last_end = '"'
            continue
        if ch.isspace():
            out.append(ch)
            continue
        if last_end and ch not in ",:}]":
            out.append(",")
        if ch == '"':
            in_string = True
            out.append(ch)
            last_end = ""
            continue
        out.append(ch)
        last_end = ch if ch in "]}" else ""
    return "".join(out)


SUMMARY_PROMPT_TEMPLATE = """You are writing the minutes of a meeting (MoM) from a transcript. \
Lines prefixed "Me:" were spoken by the person recording; lines prefixed "Others:" by other participants.

Respond with ONLY a JSON object with exactly these keys, all of them siblings at the top level — do not nest \
any of them inside another object or inside "text":
- "discussion": a JSON list of short bullet-point strings covering what was discussed, in your own words.
- "decisions": a JSON list of short bullet-point strings covering decisions made. Empty list if none.
- "open_questions": a JSON list of short bullet-point strings covering unresolved questions. Empty list if none.
- "action_items": a list of strings, one per task, each formatted "Owner: task (due date if mentioned)". \
Use "Me" when the recorder took the task; leave out the owner only if unknown.
- "tags": a list of 2 to 5 short lowercase topic tags.
Be concrete; keep names, numbers, and dates from the transcript. Never copy sentences from the transcript, and \
never include the "Me:"/"Others:" prefixes. No other text, no markdown fences.

Transcript:
{transcript}
"""


_LEADING_BULLET = re.compile(r"^(?:[-*•]\s+)+")


def _text_as_minutes(value) -> str:
    """The prompt asks for "text" as one string of headed bullet points, but
    a small model often answers with the structure itself: a list of
    points, or a {"Discussion": [...], "Decisions": [...]} mapping (nested
    either way). Render those as Markdown-ish minutes instead of leaking a
    Python repr like "['point one', 'point two']" into the note."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    lines: list[str] = []

    def _bullet(indent: str, item) -> str:
        # The model often bullets its list items itself ("- point"); don't double it.
        return f"{indent}- {_LEADING_BULLET.sub('', str(item).strip())}"

    def _emit(item, depth: int = 0) -> None:
        indent = "  " * depth
        if isinstance(item, dict):
            for key, sub in item.items():
                lines.append(f"{indent}{key}:")
                _emit(sub, depth + 1)
                lines.append("")
        elif isinstance(item, (list, tuple)):
            for sub in item:
                if isinstance(sub, (dict, list, tuple)):
                    _emit(sub, depth)
                else:
                    lines.append(_bullet(indent, sub))
        else:
            lines.append(_bullet(indent, item))

    _emit(value)
    return "\n".join(lines).strip()


_INLINE_HEADING = re.compile(r"(?<!\n)\s+((?:Decisions|Open questions|Action items|Next steps):)")


def format_minutes(text: str) -> str:
    """A small model often runs the requested headings together on one line
    ("Discussion: … Decisions: … Open questions: …"). Put each heading on
    its own paragraph so the note reads as minutes, not a blob."""
    return _INLINE_HEADING.sub(r"\n\n\1", text).strip()


def _summary_from_data(data: dict) -> Summary:
    """Coerces the model's JSON into a Summary, tolerating a null or scalar
    where a list was asked for — a malformed field must not crash note
    rendering after the summarization fallback has already passed.

    "discussion"/"decisions"/"open_questions" are requested as three flat,
    sibling top-level keys rather than nested inside one "text" object or
    string: the on-device model reliably produces valid JSON for a flat
    object of arrays, but asked to nest that same content (as a formatted
    string, or as an object inside "text") it would consistently forget a
    delimiter — a comma between array items, or the closing brace before
    the next sibling key — corrupting the whole response.
    """
    if not isinstance(data, dict) or "discussion" not in data:
        raise ValueError("summary JSON is missing the required 'discussion' key")

    def _as_list(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        # The model sometimes bullets list items itself ("- task"); the note adds its own "- [ ]".
        cleaned = [_LEADING_BULLET.sub("", str(item).strip()) for item in value]
        return [item for item in cleaned if item]

    sections = {
        "Discussion": data.get("discussion") or [],
        "Decisions": data.get("decisions") or [],
        "Open questions": data.get("open_questions") or [],
    }
    return Summary(
        text=format_minutes(_text_as_minutes(sections)),
        action_items=_as_list(data.get("action_items")),
        tags=_as_list(data.get("tags")),
    )


class ClaudeProvider:
    chunk_token_limit = 12000

    def __init__(self, api_key: str, model: str):
        import anthropic  # lazy: the SDK (pydantic, httpx) is slow to import and most commands never need it

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def summarize(self, transcript: str) -> Summary:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=16000,
            messages=[
                {"role": "user", "content": SUMMARY_PROMPT_TEMPLATE.format(transcript=transcript)}
            ],
        )
        if response.stop_reason == "max_tokens":
            raise ValueError("Claude response was cut off at max_tokens; summary JSON is incomplete")
        # Current Claude models think by default, so content[0] may be a
        # thinking block: take the text blocks only.
        text = "".join(block.text for block in response.content if block.type == "text")
        return _summary_from_data(_parse_summary_json(text))


def validate_claude_api_key(api_key: str) -> bool:
    """Confirms a Claude API key actually works via a minimal live API call.
    Returns False for any failure (invalid key, network issue, etc.) — the
    safe default for a "validate before save" flow.
    """
    try:
        import anthropic  # lazy, see ClaudeProvider

        client = anthropic.Anthropic(api_key=api_key)
        client.models.list()
        return True
    except Exception:
        return False


APPLE_LOCAL_BASE_URL = "http://localhost:11434/v1"


class AppleLocalError(Exception):
    pass


class AppleLocalProvider:
    # Apple's on-device foundation model has a 4096-token window shared by
    # prompt AND reply. ~1800 transcript tokens leaves room for the prompt
    # (~200) and a full JSON reply (~800) with margin for our rough estimate.
    chunk_token_limit = 1800

    def __init__(self, base_url: str = APPLE_LOCAL_BASE_URL, model: str = "apple-foundationmodel"):
        self._base_url = base_url
        self._model = model

    MAX_ATTEMPTS = 3  # the on-device model occasionally emits malformed JSON; sampling again usually fixes it

    def summarize(self, transcript: str) -> Summary:
        last_error: Exception | None = None
        for _attempt in range(self.MAX_ATTEMPTS):
            raw = self._complete(transcript)
            try:
                return _summary_from_data(_parse_summary_json(raw))
            except ValueError as exc:  # JSONDecodeError is a ValueError; so is a missing "text" key
                last_error = exc
        raise AppleLocalError(
            f"apfel at {self._base_url} returned malformed summary JSON {self.MAX_ATTEMPTS} times in a row "
            f"(last error: {last_error})"
        )

    def _complete(self, transcript: str) -> str:
        """One chat completion; returns the model's raw message content."""
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
            with urllib.request.urlopen(request, timeout=180) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise AppleLocalError(f"apfel at {self._base_url} returned HTTP {exc.code}: {exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AppleLocalError(f"Could not reach apfel at {self._base_url}: {exc}") from exc
        except ValueError as exc:
            raise AppleLocalError(f"apfel at {self._base_url} returned a non-JSON response: {exc}") from exc
        return data["choices"][0]["message"]["content"]


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

    try:
        model_info = subprocess.run(["apfel", "--model-info"], capture_output=True, text=True)
    except FileNotFoundError:
        problems.append("apfel is installed but not on PATH. Run: brew link apfel")
        return problems
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
        try:
            keychain_credential = get_provider_credential(config.api_key_env)
        except KeyringError:
            keychain_credential = None
        api_key = keychain_credential or os.environ.get(config.api_key_env)
        if not api_key:
            raise ConfigError(
                f"No credential found for '{config.api_key_env}'. Run `notetaker set-api-key <key>`, "
                "or export it as an environment variable."
            )
        return ClaudeProvider(api_key=api_key, model=config.ai_model)
    if config.ai_provider == "apple_local":
        return AppleLocalProvider(model=config.ai_model)
    raise ConfigError(f"Unknown ai_provider '{config.ai_provider}'.")
