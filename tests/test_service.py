from datetime import UTC, datetime

from musicdl_api.service import DownloadTask, task_to_response


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
