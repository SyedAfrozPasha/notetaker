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


def test_update_config_changes_only_given_fields(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/notetaker-notes\n"
        "whisper_model: base.en\n"
        "ai_provider: claude\n"
        "ai_model: claude-sonnet-5\n"
        "api_key_env: ANTHROPIC_API_KEY\n"
    )

    config = update_config({"notes_dir": "~/custom-notes", "whisper_model": "small"}, path)

    assert str(config.notes_dir).endswith("custom-notes")
    assert config.whisper_model == "small"
    assert config.ai_provider == "claude"  # untouched
    assert config.ai_model == "claude-sonnet-5"  # untouched, not a supported update key


def test_update_config_persists_to_disk(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/notetaker-notes\nwhisper_model: base.en\nai_provider: claude\n"
        "ai_model: claude-sonnet-5\napi_key_env: ANTHROPIC_API_KEY\n"
    )

    update_config({"ai_provider": "apple_local"}, path)

    reloaded = load_config(path)
    assert reloaded.ai_provider == "apple_local"


def test_update_config_rejects_unsupported_field(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    write_default_config(path)

    with pytest.raises(ConfigError, match="api_key_env"):
        update_config({"api_key_env": "SOMETHING_ELSE"}, path)


def test_update_config_rejects_invalid_provider(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    write_default_config(path)

    with pytest.raises(ConfigError, match="Unknown ai_provider"):
        update_config({"ai_provider": "bogus"}, path)


def test_update_config_raises_when_file_missing(tmp_path):
    from notetaker.config import update_config

    with pytest.raises(ConfigError):
        update_config({"whisper_model": "small"}, tmp_path / "missing.yaml")
