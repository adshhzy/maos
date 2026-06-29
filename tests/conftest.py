import sys, os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "maos_runtime"))


@pytest.fixture(autouse=True)
def isolated_a2a_task_store(tmp_path, monkeypatch):
    monkeypatch.setenv("A2A_PROVIDER_TASK_DB_FILE", str(tmp_path / "maos_runtime.db"))
    monkeypatch.setenv("A2A_DISABLE_JSON_INVOCATION_MIRROR", "1")
    monkeypatch.setenv("MAOS_DATA_DIR", str(tmp_path / "data"))
    try:
        from maos_runtime.a2a_task_store import TASKS, TASKS_LOCK, clear_persistent_tasks

        with TASKS_LOCK:
            TASKS.clear()
        clear_persistent_tasks()
    except Exception:
        pass
    yield
    try:
        from maos_runtime.a2a_task_store import TASKS, TASKS_LOCK, clear_persistent_tasks

        with TASKS_LOCK:
            TASKS.clear()
        clear_persistent_tasks()
    except Exception:
        pass
