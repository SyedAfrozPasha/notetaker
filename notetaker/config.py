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


UPDATABLE_KEYS = {"notes_dir", "whisper_model", "ai_provider"}


def update_config(updates: dict, path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise ConfigError(f"No config found at {path}. Run `notetaker init` first.")
    unknown = set(updates) - UPDATABLE_KEYS
    if unknown:
        raise ConfigError(f"Cannot update unsupported config field(s): {', '.join(sorted(unknown))}.")
    raw = yaml.safe_load(path.read_text()) or {}
    raw.update(updates)
    if raw.get("ai_provider") not in VALID_PROVIDERS:
        raise ConfigError(f"Unknown ai_provider '{raw.get('ai_provider')}' — expected one of {VALID_PROVIDERS}.")
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return load_config(path)
