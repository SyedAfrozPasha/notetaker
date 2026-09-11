import pytest
from notetaker.config import Config, ConfigError, load_config, write_default_config


def test_write_default_config_creates_file(tmp_path):
    path = tmp_path / "config.yaml"
    assert write_default_config(path) is True
    assert path.exists()
    assert "notes_dir" in path.read_text()


def test_write_default_config_is_idempotent(tmp_path):
    path = tmp_path / "config.yaml"
    write_default_config(path)
    path.write_text("notes_dir: /custom\nwhisper_model: tiny\nai_provider: claude\nai_model: x\napi_key_env: Y\n")
    assert write_default_config(path) is False
    assert "custom" in path.read_text()


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")


def test_load_config_parses_valid_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/notetaker-notes\n"
        "whisper_model: base.en\n"
        "ai_provider: claude\n"
        "ai_model: claude-sonnet-5\n"
        "api_key_env: ANTHROPIC_API_KEY\n"
    )
    config = load_config(path)
    assert isinstance(config, Config)
    assert config.whisper_model == "base.en"
    assert config.notes_dir.is_absolute()


def test_load_config_missing_keys_raises(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("notes_dir: ~/x\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_rejects_unknown_provider(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/x\nwhisper_model: base.en\nai_provider: bogus\n"
        "ai_model: x\napi_key_env: Y\n"
    )
    with pytest.raises(ConfigError):
        load_config(path)
