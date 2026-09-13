"""Serving the dashboard from inside the menu bar process (`notetaker dashboard`)."""

import socket
import time
import urllib.request

from notetaker.dashboard import port_in_use_error, serve_in_background


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_port_in_use_error_is_none_for_a_free_port():
    assert port_in_use_error("127.0.0.1", _free_port()) is None


def test_port_in_use_error_names_the_address_when_something_is_listening():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]

        error = port_in_use_error("127.0.0.1", port)

    assert error is not None
    assert f"http://127.0.0.1:{port}" in error
    assert "already running" in error


def test_serve_in_background_serves_the_app_on_a_daemon_thread_and_stops():
    port = _free_port()

    handle = serve_in_background("127.0.0.1", port)
    try:
        assert handle.url == f"http://127.0.0.1:{port}"
        assert handle.thread.daemon is True
        deadline = time.time() + 10
        body = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{handle.url}/static/htmx.min.js", timeout=1) as response:
                    body = response.read(20)
                break
            except OSError:
                time.sleep(0.1)
        assert body is not None and body.startswith(b"var htmx=")
    finally:
        handle.stop()

    assert not handle.thread.is_alive()
