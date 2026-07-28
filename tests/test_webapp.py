from __future__ import annotations

import pytest

from pipeline import db

pytest.importorskip("fastapi")


def _db_reachable() -> bool:
    try:
        with db.engine.connect():
            pass
        return True
    except Exception:  # noqa: BLE001 - any failure here just means "skip these tests"
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="PostgreSQL not reachable at DATABASE_URL"
)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from webapp.main import app

    return TestClient(app)


def test_list_pets_returns_200(client):
    response = client.get("/api/pets")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_unknown_pet_returns_404(client):
    response = client.get("/api/pets/PET-DOES-NOT-EXIST")
    assert response.status_code == 404


def test_get_unknown_job_video_returns_404(client):
    response = client.get("/api/jobs/999999999/video")
    assert response.status_code == 404


def test_regenerate_scene_on_unknown_job_returns_404(client):
    response = client.post(
        "/api/jobs/999999999/regenerate-scene",
        json={"scene_id": 1, "subtitle": "test"},
    )
    assert response.status_code == 404
