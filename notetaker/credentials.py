import keyring

KEYCHAIN_SERVICE = "notetaker"


def get_provider_credential(key_name: str) -> str | None:
    """Looks up a Provider credential from the macOS Keychain, keyed by the
    config's `api_key_env` name (e.g. "ANTHROPIC_API_KEY"). Returns None if
    nothing is stored.
    """
    return keyring.get_password(KEYCHAIN_SERVICE, key_name)


def set_provider_credential(key_name: str, value: str) -> None:
    """Stores a Provider credential in the macOS Keychain."""
    keyring.set_password(KEYCHAIN_SERVICE, key_name, value)


def mask_credential(value: str) -> str:
    """Masks a credential for display, keeping a short prefix and suffix
    visible (e.g. "sk-ant-api03-abcdef1234" -> "sk-ant••••1234") so a user
    can recognize which key is active without seeing the full value.
    """
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:6]}{'•' * 4}{value[-4:]}"
