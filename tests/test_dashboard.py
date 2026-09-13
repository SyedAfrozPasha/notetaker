import pytest
from fastapi.testclient import TestClient

from notetaker.dashboard import app


@pytest.fixture
def client():
    return TestClient(app)


def test_index_page_loads_and_wires_the_status_poller(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Notetaker" in response.text
    assert 'hx-get="/status"' in response.text
    assert 'hx-trigger="load, every 2s"' in response.text


def test_index_page_loads_htmx_from_cdn(client):
    response = client.get("/")

    assert "htmx.org@2.0.10" in response.text
