from pathlib import Path

from fastapi.testclient import TestClient

from inklink.web.app import create_app
from inklink.workflow.service import WorkflowService


def test_web_health_and_defaults(tmp_path: Path) -> None:
    app = create_app(
        log_root=tmp_path / "logs",
        static_dir=tmp_path / "missing-dist",
        default_options={
            "input_dir": "novel",
            "config_path": "config.toml",
            "log_root": str(tmp_path / "logs"),
        },
    )

    with TestClient(app) as client:
        health = client.get("/api/health")
        defaults = client.get("/api/defaults")

    assert health.status_code == 200
    assert health.json() == {"ok": True}
    assert defaults.status_code == 200
    assert defaults.json()["input_dir"] == "novel"


def test_web_snapshot_artifact_and_approval(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    (novel / "1.txt").write_text("title: 第一章\n---\n正文", encoding="utf-8")
    log_root = tmp_path / "logs"
    with WorkflowService(log_root=log_root) as service:
        run = service.start_run(novel)
        version = service.update_artifact(
            artifact_id="outline",
            artifact_type="outline",
            payload={"outline": "继续追查。"},
            approval_id="outline",
        )

    app = create_app(log_root=log_root, static_dir=tmp_path / "missing-dist")
    with TestClient(app) as client:
        snapshot = client.get(
            f"/api/runs/{run.runtime_id}/snapshot",
            params={"log_root_query": str(log_root)},
        )
        artifact = client.get(
            f"/api/runs/{run.runtime_id}/artifacts/outline",
            params={"version": version, "log_root_query": str(log_root)},
        )
        approval = client.post(
            f"/api/runs/{run.runtime_id}/approvals/approve",
            params={"log_root_query": str(log_root)},
            json={
                "approval_id": "outline",
                "artifact_id": "outline",
                "artifact_version": version,
            },
        )

    assert snapshot.status_code == 200
    assert snapshot.json()["runtime_id"] == run.runtime_id
    assert artifact.status_code == 200
    assert artifact.json()["payload"]["outline"] == "继续追查。"
    assert approval.status_code == 200
    assert approval.json()["accepted"] is True


def test_web_runtime_control_commands(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    (novel / "1.txt").write_text("title: 第一章\n---\n正文", encoding="utf-8")
    log_root = tmp_path / "logs"
    with WorkflowService(log_root=log_root) as service:
        run = service.start_run(novel)

    app = create_app(log_root=log_root, static_dir=tmp_path / "missing-dist")
    with TestClient(app) as client:
        retry = client.post(
            f"/api/runs/{run.runtime_id}/nodes/retry",
            params={"log_root_query": str(log_root)},
            json={"node_id": "draft_scene:2:2-1"},
        )
        abandon = client.post(
            f"/api/runs/{run.runtime_id}/chapters/2/abandon",
            params={"log_root_query": str(log_root)},
        )
        rewrite = client.post(
            f"/api/runs/{run.runtime_id}/chapters/2/rewrite",
            params={"log_root_query": str(log_root)},
        )

    assert retry.status_code == 200
    assert retry.json()["accepted"] is True
    assert abandon.status_code == 200
    assert abandon.json()["accepted"] is True
    assert rewrite.status_code == 200
    assert rewrite.json()["accepted"] is True
