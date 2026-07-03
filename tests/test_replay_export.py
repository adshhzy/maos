from pathlib import Path

import pytest

from web.replay_export import ReplayExportError, build_replay_snapshots, export_task_replay


def _completed_task() -> dict:
    return {
        "task_id": "task-replay-test",
        "status": "completed",
        "state": {
            "graph_id": "replay-test",
            "graph_name": "Replay Test",
            "workflow_status": "completed",
            "levels": [["start"], ["review"], ["final"]],
            "nodes": [
                {
                    "id": "start",
                    "label": "Start Work",
                    "status": "completed",
                    "backend": "simulator",
                    "visits": 1,
                    "max_visits": 1,
                    "heartbeat_count": 1,
                    "instances": [
                        {
                            "id": "start#1",
                            "node_id": "start",
                            "visit": 1,
                            "kind": "agent",
                            "status": "completed",
                            "started_at": "2026-01-01T00:00:00Z",
                            "finished_at": "2026-01-01T00:00:03Z",
                        }
                    ],
                },
                {
                    "id": "review",
                    "label": "Review",
                    "status": "completed",
                    "backend": "claude",
                    "visits": 1,
                    "max_visits": 2,
                    "heartbeat_count": 2,
                    "instances": [
                        {
                            "id": "review#1",
                            "node_id": "review",
                            "visit": 1,
                            "kind": "agent",
                            "status": "completed",
                            "started_at": "2026-01-01T00:00:04Z",
                            "finished_at": "2026-01-01T00:00:08Z",
                        }
                    ],
                },
                {
                    "id": "final",
                    "label": "Final",
                    "status": "completed",
                    "backend": "claude",
                    "visits": 1,
                    "max_visits": 1,
                    "heartbeat_count": 1,
                    "instances": [
                        {
                            "id": "final#1",
                            "node_id": "final",
                            "visit": 1,
                            "kind": "agent",
                            "status": "completed",
                            "started_at": "2026-01-01T00:00:09Z",
                            "finished_at": "2026-01-01T00:00:12Z",
                        }
                    ],
                },
            ],
            "edges": [
                {"from": "start", "to": "review", "label": "next", "taken_count": 1},
                {"from": "review", "to": "final", "label": "approved", "when": "result.ok", "taken_count": 1},
            ],
            "instances": [
                {
                    "id": "start#1",
                    "node_id": "start",
                    "visit": 1,
                    "kind": "agent",
                    "status": "completed",
                    "started_at": "2026-01-01T00:00:00Z",
                    "finished_at": "2026-01-01T00:00:03Z",
                },
                {
                    "id": "review#1",
                    "node_id": "review",
                    "visit": 1,
                    "kind": "agent",
                    "status": "completed",
                    "started_at": "2026-01-01T00:00:04Z",
                    "finished_at": "2026-01-01T00:00:08Z",
                },
                {
                    "id": "final#1",
                    "node_id": "final",
                    "visit": 1,
                    "kind": "agent",
                    "status": "completed",
                    "started_at": "2026-01-01T00:00:09Z",
                    "finished_at": "2026-01-01T00:00:12Z",
                },
            ],
        },
    }


def test_build_replay_snapshots_reconstructs_state_changes():
    snapshots = build_replay_snapshots(_completed_task())

    assert len(snapshots) >= 4
    assert snapshots[0]["state"]["nodes"][0]["status"] == "pending"
    assert snapshots[-1]["state"]["nodes"][-1]["status"] == "completed"
    assert snapshots[-1]["state"]["edges"][0]["taken_count"] == 1


def test_export_task_replay_writes_frames_and_gif(tmp_path):
    result = export_task_replay(_completed_task(), output_dir=str(tmp_path), frame_duration_ms=300)

    assert result["ok"] is True
    assert Path(result["gif_path"]).is_file()
    assert Path(result["frames_dir"]).is_dir()
    assert result["frame_count"] == len(result["frame_paths"])
    assert all(Path(path).is_file() for path in result["frame_paths"])


def test_export_task_replay_rejects_running_task(tmp_path):
    task = _completed_task()
    task["status"] = "running"

    with pytest.raises(ReplayExportError):
        export_task_replay(task, output_dir=str(tmp_path))


def test_export_task_replay_rejects_failed_task(tmp_path):
    task = _completed_task()
    task["status"] = "failed"

    with pytest.raises(ReplayExportError):
        export_task_replay(task, output_dir=str(tmp_path))
