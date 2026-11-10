import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402
from kaibot_api.main import app  # noqa: E402

c = TestClient(app)


def test_health_is_paper_only():
    assert c.get("/api/v1/health").json() == {"ok": True, "paper_only": True}


def test_controls_need_idempotency_key():
    assert c.post("/api/v1/controls/pause").status_code == 400
    assert c.post("/api/v1/controls/pause", headers={"Idempotency-Key": "k1"}).status_code == 200
