from __future__ import annotations

import time

from fastapi.testclient import TestClient

from daily_rsync.web import create_app


def test_local_web_home_renders_without_remote_access(app_config) -> None:
    client = TestClient(create_app(app_config))
    response = client.get("/")

    assert response.status_code == 200
    assert "Daily Rsync" in response.text
    assert "무작위 Job 선택" in response.text
    assert 'data-action="verify"' in response.text
    assert 'data-action="retention-preview"' in response.text
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


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


def test_dashboard_status_is_local_only(app_config) -> None:
    client = TestClient(create_app(app_config))

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["ssh_host"] == "test-host"
    assert response.json()["jobs"] == 0
    assert response.json()["minimum_free_bytes"] == app_config.minimum_free_bytes


def test_verify_runs_as_background_task(app_config, monkeypatch) -> None:
    application = create_app(app_config)
    monkeypatch.setattr(
        application.state.service,
        "verify",
        lambda **_kwargs: {
            "checked": 7,
            "failed": 0,
            "errors": [],
            "status": "SUCCESS",
        },
    )
    client = TestClient(application)

    started = client.post(
        "/api/verify",
        params={"job": "polybot-king", "strategy": "golden-queen"},
    )
    assert started.status_code == 200
    task_id = started.json()["task_id"]
    task = {}
    for _ in range(30):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] == "SUCCESS":
            break
        time.sleep(0.01)

    assert task["status"] == "SUCCESS"
    assert task["result"]["checked"] == 7
    assert "무결성 검사" in task["label"]


def test_finder_open_rejects_path_outside_data_root(app_config) -> None:
    client = TestClient(create_app(app_config))

    response = client.post("/api/open", json={"path": "/tmp"})

    assert response.status_code == 403
