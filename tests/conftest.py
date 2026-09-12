import keyring
import pytest


@pytest.fixture(autouse=True)
def _block_real_keyring_access(monkeypatch):
    """Fails fast if any test reaches the real macOS Keychain without mocking
    it first — the alternative is a silent hang on a permission-prompt dialog.
    """

    def _fail(*args, **kwargs):
        raise AssertionError(
            "A test reached the real keyring backend without mocking it. "
            "Mock keyring.get_password/set_password (or a higher-level wrapper) explicitly."
        )

    monkeypatch.setattr(keyring, "get_password", _fail)
    monkeypatch.setattr(keyring, "set_password", _fail)
