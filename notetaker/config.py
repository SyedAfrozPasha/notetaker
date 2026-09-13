from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_DIR = Path.home() / ".notetaker"
CONFIG_PATH = CONFIG_DIR / "config.yaml"

DEFAULT_CONFIG_YAML = """\
notes_dir: ~/notetaker-notes
whisper_model: base.en          # tiny.en/base.en/small.en/medium.en
# whisper_model_path: /path/to/faster-whisper-model   # offline machines: load the model from this
#                                                      # directory instead of downloading from Hugging Face
capture_microphone: true        # also record your own voice from the default input device
ai_provider: apple_local        # apple_local (fully on-device via apfel) or claude (cloud)
ai_model: apple-foundationmodel
api_key_env: ANTHROPIC_API_KEY  # only used by ai_provider: claude; never stored in this file
"""

REQUIRED_KEYS = ["notes_dir", "whisper_model", "ai_provider", "ai_model", "api_key_env"]
VALID_PROVIDERS = ("claude", "apple_local")
PROVIDER_DEFAULT_MODELS = {"claude": "claude-sonnet-5", "apple_local": "apple-foundationmodel"}
UPDATABLE_KEYS = {"notes_dir", "whisper_model", "ai_provider", "ai_model", "capture_microphone", "whisper_model_path"}


class ConfigError(Exception):
    pass


@dataclass
class Config:
    notes_dir: Path
    whisper_model: str
    ai_provider: str
    ai_model: str
    api_key_env: str
    whisper_model_path: str | None = None
    capture_microphone: bool = True


def write_default_config(path: Path = CONFIG_PATH) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_YAML)
    return True


def _load_yaml(path: Path) -> dict:
    """Parses a config file's YAML, raising ConfigError (not a raw
    yaml.YAMLError) for malformed syntax — so a hand-edited config.yaml
    never crashes a caller with an unhandled parser exception.
    """
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Config at {path} is not valid YAML: {exc}") from exc


def _parse_config(raw: dict, path: Path) -> Config:
    """Validates a raw config dict and builds a Config from it — the shared
    validation `load_config` and `update_config` both run, so `update_config`
    can validate a merged dict BEFORE writing it to disk rather than after
    (a bad value must never be persisted).
    """
    missing = [key for key in REQUIRED_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"Config at {path} is missing keys: {', '.join(missing)}")
    if raw["ai_provider"] not in VALID_PROVIDERS:
        raise ConfigError(
            f"Unknown ai_provider '{raw['ai_provider']}' — expected one of {VALID_PROVIDERS}."
        )
    try:
        return Config(
            notes_dir=Path(raw["notes_dir"]).expanduser(),
            whisper_model=raw["whisper_model"],
            ai_provider=raw["ai_provider"],
            ai_model=raw["ai_model"],
            api_key_env=raw["api_key_env"],
            whisper_model_path=raw.get("whisper_model_path") or None,
            capture_microphone=_as_bool(raw.get("capture_microphone", True), "capture_microphone"),
        )
    except TypeError as exc:
        raise ConfigError(f"Config at {path} has an invalid value: {exc}") from exc


def _as_bool(value, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "yes", "no", "on", "off", "1", "0"):
        return value.strip().lower() in ("true", "yes", "on", "1")
    raise TypeError(f"{key} must be true or false, got {value!r}")


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise ConfigError(f"No config found at {path}. Run `notetaker init` first.")
    raw = _load_yaml(path)
    return _parse_config(raw, path)


def update_config(updates: dict, path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise ConfigError(f"No config found at {path}. Run `notetaker init` first.")
    unknown = set(updates) - UPDATABLE_KEYS
    if unknown:
        raise ConfigError(f"Cannot update unsupported config field(s): {', '.join(sorted(unknown))}.")
    raw = _load_yaml(path)
    new_provider = updates.get("ai_provider")
    if new_provider and new_provider != raw.get("ai_provider") and "ai_model" not in updates:
        # Switching provider without naming a model: the old provider's model
        # name is meaningless to the new one, so fall back to its default.
        updates = {**updates, "ai_model": PROVIDER_DEFAULT_MODELS.get(new_provider, raw.get("ai_model"))}
    raw.update(updates)
    if raw.get("whisper_model_path") in ("", None):
        raw.pop("whisper_model_path", None)
    config = _parse_config(raw, path)
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return config
