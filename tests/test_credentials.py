from notetaker.credentials import KEYCHAIN_SERVICE, get_provider_credential, mask_credential, set_provider_credential


def test_get_provider_credential_returns_none_when_not_stored(monkeypatch):
    monkeypatch.setattr("notetaker.credentials.keyring.get_password", lambda service, key: None)
    assert get_provider_credential("ANTHROPIC_API_KEY") is None


def test_get_provider_credential_returns_stored_value(monkeypatch):
    calls = {}

    def fake_get_password(service, key):
        calls["args"] = (service, key)
        return "sk-ant-secret"

    monkeypatch.setattr("notetaker.credentials.keyring.get_password", fake_get_password)
    assert get_provider_credential("ANTHROPIC_API_KEY") == "sk-ant-secret"
    assert calls["args"] == (KEYCHAIN_SERVICE, "ANTHROPIC_API_KEY")


def test_set_provider_credential_stores_under_notetaker_service(monkeypatch):
    calls = {}

    def fake_set_password(service, key, value):
        calls["args"] = (service, key, value)

    monkeypatch.setattr("notetaker.credentials.keyring.set_password", fake_set_password)
    set_provider_credential("ANTHROPIC_API_KEY", "sk-ant-secret")
    assert calls["args"] == (KEYCHAIN_SERVICE, "ANTHROPIC_API_KEY", "sk-ant-secret")


def test_mask_credential_keeps_prefix_and_suffix():
    assert mask_credential("sk-ant-api03-abcdef1234") == "sk-ant••••1234"


def test_mask_credential_fully_masks_short_values():
    assert mask_credential("short") == "•••••"


def test_mask_credential_fully_masks_ten_character_values():
    assert mask_credential("0123456789") == "••••••••••"
