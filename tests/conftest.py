import keyring
import pytest


@pytest.fixture(autouse=True)
def _reset_stop_job():
    """The background stop job is process-wide state shared by the menu bar
    and the dashboard; a finished job left over from one test must not be
    reported (or redirected to) by the next."""
    from notetaker import dashboard, service

    service._stop_job = None
    dashboard._handled_stop_job = None
    yield
    job = service._stop_job
    if job is not None and job.thread is not None:
        job.thread.join(timeout=5)
    service._stop_job = None
    dashboard._handled_stop_job = None


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
