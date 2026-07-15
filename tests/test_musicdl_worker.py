import os

import pytest

from musicdl_api.service import (
    MusicdlFacade,
    MusicdlWorkerError,
    _reporting_progress_class,
    _run_musicdl_worker,
)


def test_worker_failure_does_not_redirect_parent_console(tmp_path) -> None:
    """The worker may silence itself, but must never alter Uvicorn's fds."""
    stdout_before = os.fstat(1)
    stderr_before = os.fstat(2)

    with pytest.raises(MusicdlWorkerError, match="Unsupported musicdl operation"):
        _run_musicdl_worker(
            "invalid",
            {},
            timeout_seconds=10,
            log_path=tmp_path / "musicdl.log",
        )

    stdout_after = os.fstat(1)
    stderr_after = os.fstat(2)
    assert (stdout_after.st_dev, stdout_after.st_ino) == (
        stdout_before.st_dev,
        stdout_before.st_ino,
    )
    assert (stderr_after.st_dev, stderr_after.st_ino) == (
        stderr_before.st_dev,
        stderr_before.st_ino,
    )
    assert (tmp_path / "musicdl.log").is_file()


def test_reporting_progress_emits_structured_task_updates() -> None:
    events = []
    progress_class = _reporting_progress_class(events.append)

    with progress_class(disable=True) as progress:
        task_id = progress.add_task("KuwoMusicClient.search >>> Completed (0/5) Search URLs", total=5)
        progress.advance(task_id, 2)

    assert events[-1] == {
        "taskId": task_id,
        "description": "KuwoMusicClient.search >>> Completed (0/5) Search URLs",
        "completed": 2,
        "total": 5,
        "source": "KuwoMusicClient",
        "stage": "sourceSearchUrls",
        "indeterminate": False,
    }


def test_reporting_progress_marks_result_processing_as_indeterminate() -> None:
    events = []
    progress_class = _reporting_progress_class(events.append)

    with progress_class(disable=True) as progress:
        task_id = progress.add_task(
            "KuwoMusicClient._search >>> Start to process the 0th search result on page 1",
            total=None,
        )
        progress.update(
            task_id,
            description="KuwoMusicClient._search >>> Start to process the 5th search result on page 1",
            completed=5,
            total=5,
        )

    assert events[-1]["source"] == "KuwoMusicClient"
    assert events[-1]["stage"] == "processingResults"
    assert events[-1]["currentItem"] == 5
    assert events[-1]["page"] == 1
    assert events[-1]["total"] is None
    assert events[-1]["indeterminate"] is True


def test_search_skips_failed_source_and_keeps_other_source_results(monkeypatch, tmp_path) -> None:
    facade = MusicdlFacade()
    facade.download_root = tmp_path
    calls = []

    def fake_worker(operation, payload, timeout_seconds, log_path, progress_callback):
        source = payload["sources"][0]
        calls.append((source, timeout_seconds, log_path))
        if source == "BrokenMusicClient":
            raise TimeoutError("timed out")
        progress_callback({"taskId": 0, "description": "search"})
        return [{"itemId": "1", "source": source, "songInfo": {}}]

    monkeypatch.setattr("musicdl_api.service._run_musicdl_worker", fake_worker)

    progress = []
    items = facade.search(
        "test",
        ["WorkingMusicClient", "BrokenMusicClient"],
        timeout_seconds=12,
        log_id="search_test",
        progress_callback=progress.append,
    )

    assert items == [{"itemId": "1", "source": "WorkingMusicClient", "songInfo": {}}]
    assert {source for source, _, _ in calls} == {"WorkingMusicClient", "BrokenMusicClient"}
    assert all(timeout == 12 for _, timeout, _ in calls)
    assert progress == [{"taskId": 0, "description": "search", "source": "WorkingMusicClient"}]
