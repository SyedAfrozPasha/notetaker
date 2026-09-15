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
    path.write_text("notes_dir: /custom\nai_provider: claude\nai_model: x\napi_key_env: Y\n")
    assert write_default_config(path) is False
    assert "custom" in path.read_text()


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")


def test_load_config_parses_valid_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/notetaker-notes\n"
        "ai_provider: claude\n"
        "ai_model: claude-sonnet-5\n"
        "api_key_env: ANTHROPIC_API_KEY\n"
    )
    config = load_config(path)
    assert isinstance(config, Config)
    assert config.notes_dir.is_absolute()


def test_load_config_missing_keys_raises(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("notes_dir: ~/x\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_rejects_unknown_provider(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/x\nai_provider: bogus\n"
        "ai_model: x\napi_key_env: Y\n"
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_update_config_changes_only_given_fields(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/notetaker-notes\n"
        "ai_provider: claude\n"
        "ai_model: claude-sonnet-5\n"
        "api_key_env: ANTHROPIC_API_KEY\n"
    )

    config = update_config({"notes_dir": "~/custom-notes", "capture_microphone": False}, path)

    assert str(config.notes_dir).endswith("custom-notes")
    assert config.capture_microphone is False
    assert config.ai_provider == "claude"  # untouched
    assert config.ai_model == "claude-sonnet-5"  # untouched, not a supported update key


def test_update_config_persists_to_disk(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/notetaker-notes\nai_provider: claude\n"
        "ai_model: claude-sonnet-5\napi_key_env: ANTHROPIC_API_KEY\n"
    )

    update_config({"ai_provider": "apple_local"}, path)

    reloaded = load_config(path)
    assert reloaded.ai_provider == "apple_local"


def test_update_config_rejects_invalid_value_without_writing_the_file(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    original_text = (
        "notes_dir: ~/notetaker-notes\nai_provider: claude\n"
        "ai_model: claude-sonnet-5\napi_key_env: ANTHROPIC_API_KEY\n"
    )
    path.write_text(original_text)

    with pytest.raises(ConfigError, match="invalid value"):
        update_config({"notes_dir": None}, path)

    # The bad value must never be persisted — a later load must still succeed.
    assert path.read_text() == original_text
    assert load_config(path).notes_dir is not None


def test_update_config_rejects_unsupported_field(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    write_default_config(path)

    with pytest.raises(ConfigError, match="api_key_env"):
        update_config({"api_key_env": "SOMETHING_ELSE"}, path)


def test_update_config_accepts_ai_model_field(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    write_default_config(path)

    config = update_config({"ai_model": "claude-opus-5"}, path)

    assert config.ai_model == "claude-opus-5"


def test_update_config_switching_provider_resets_model_to_provider_default(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/n\nai_provider: claude\n"
        "ai_model: claude-sonnet-5\napi_key_env: ANTHROPIC_API_KEY\n"
    )

    config = update_config({"ai_provider": "apple_local"}, path)

    assert config.ai_model == "apple-foundationmodel"
    # And back again, with an explicit model this time — explicit wins.
    config = update_config({"ai_provider": "claude", "ai_model": "claude-opus-5"}, path)
    assert config.ai_model == "claude-opus-5"


def test_default_config_is_fully_on_device_and_records_microphone(tmp_path):
    path = tmp_path / "config.yaml"
    write_default_config(path)

    config = load_config(path)

    assert config.ai_provider == "apple_local"
    assert config.ai_model == "apple-foundationmodel"
    assert config.capture_microphone is True


def test_load_config_reads_optional_mic_key(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/n\nai_provider: apple_local\nai_model: m\napi_key_env: K\n"
        "capture_microphone: false\n"
    )

    config = load_config(path)

    assert config.capture_microphone is False


def test_load_config_rejects_non_boolean_capture_microphone(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/n\nai_provider: apple_local\nai_model: m\napi_key_env: K\n"
        "capture_microphone: maybe\n"
    )
    with pytest.raises(ConfigError, match="capture_microphone"):
        load_config(path)


def test_update_config_rejects_invalid_provider(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    write_default_config(path)

    with pytest.raises(ConfigError, match="Unknown ai_provider"):
        update_config({"ai_provider": "bogus"}, path)


def test_update_config_validates_full_config_not_just_changed_keys(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "notes_dir: ~/notetaker-notes\nai_provider: already-bogus\n"
        "ai_model: claude-sonnet-5\napi_key_env: ANTHROPIC_API_KEY\n"
    )

    with pytest.raises(ConfigError, match="Unknown ai_provider"):
        update_config({"capture_microphone": False}, path)


def test_update_config_raises_when_file_missing(tmp_path):
    from notetaker.config import update_config

    with pytest.raises(ConfigError):
        update_config({"capture_microphone": False}, tmp_path / "missing.yaml")


def test_update_config_raises_on_malformed_yaml(tmp_path):
    from notetaker.config import update_config

    path = tmp_path / "config.yaml"
    path.write_text("notes_dir: [unclosed\n")

    with pytest.raises(ConfigError, match="not valid YAML"):
        update_config({"capture_microphone": False}, path)


def test_load_config_raises_on_malformed_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("notes_dir: [unclosed\n")

    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(path)
