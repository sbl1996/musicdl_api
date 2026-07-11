import os

import pytest

from musicdl_api.service import (
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
