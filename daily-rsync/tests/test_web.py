from __future__ import annotations

from fastapi.testclient import TestClient

from daily_rsync.web import create_app


def test_local_web_home_renders_without_remote_access(app_config) -> None:
    client = TestClient(create_app(app_config))
    response = client.get("/")

    assert response.status_code == 200
    assert "daily-rsync" in response.text
    assert "polybot-king" in response.text


def test_account_epoch_can_be_saved_in_web_ui(app_config) -> None:
    client = TestClient(create_app(app_config))
    payload = {
        "job": "polybot-king",
        "strategy": "golden-queen",
        "account_alias": "golden-king",
        "first_build": 1,
    }

    assert client.post("/api/account-epochs", json=payload).status_code == 200
    response = client.get("/api/account-epochs", params={"job": "polybot-king"})

    assert response.status_code == 200
    assert response.json()[0]["account_alias"] == "golden-king"
