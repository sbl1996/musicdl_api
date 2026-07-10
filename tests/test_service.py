from datetime import UTC, datetime

from musicdl_api.service import DownloadTask, DownloadTaskStore, _directory_usage, task_to_response


def test_completed_download_progress_uses_result_path(tmp_path) -> None:
    predicted_path = tmp_path / "predicted.mp3"
    actual_path = tmp_path / "actual.mp3"
    actual_path.write_bytes(b"downloaded")
    now = datetime.now(UTC)
    task = DownloadTask(
        task_id="task_1",
        session_id="session_1",
        item_id="1",
        status="completed",
        created_at=now,
        updated_at=now,
        save_path=str(predicted_path),
        total_bytes=len(b"downloaded"),
        result={"savePath": str(actual_path)},
    )

    response = task_to_response(task)

    assert response["progress"] == {
        "savePath": str(actual_path),
        "fileExists": True,
        "downloadedBytes": len(b"downloaded"),
        "totalBytes": len(b"downloaded"),
        "percent": 100.0,
    }


def test_directory_usage_counts_regular_files_only(tmp_path) -> None:
    (tmp_path / "song.mp3").write_bytes(b"song")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "cover.jpg").write_bytes(b"art")
    (tmp_path / "link.mp3").symlink_to(tmp_path / "song.mp3")

    assert _directory_usage(tmp_path) == {"usedBytes": 7, "fileCount": 2}


def test_cleanup_removes_terminal_task_dirs_and_keeps_active_ones(tmp_path) -> None:
    store = DownloadTaskStore.__new__(DownloadTaskStore)
    store.facade = type("Facade", (), {"download_root": tmp_path})()
    store._lock = __import__("threading").Lock()
    store._maintenance_lock = __import__("threading").Lock()
    now = datetime.now(UTC)
    completed = DownloadTask("task_done", "session_1", "1", "completed", now, now)
    running = DownloadTask("task_active", "session_1", "2", "running", now, now)
    store._tasks = {completed.task_id: completed, running.task_id: running}
    (tmp_path / "tasks" / "session_1" / "task_done").mkdir(parents=True)
    (tmp_path / "tasks" / "session_1" / "task_done" / "song.mp3").write_bytes(b"done")
    (tmp_path / "tasks" / "session_1" / "task_active").mkdir()
    (tmp_path / "tasks" / "session_1" / "task_active" / "song.mp3").write_bytes(b"active")

    assert store.cleanup_completed() == {
        "deletedBytes": 4,
        "deletedFileCount": 1,
        "deletedTaskCount": 1,
        "skippedActiveTaskCount": 1,
    }
    assert not (tmp_path / "tasks" / "session_1" / "task_done").exists()
    assert (tmp_path / "tasks" / "session_1" / "task_active").is_dir()
