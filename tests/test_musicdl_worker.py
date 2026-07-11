import os

import pytest

from musicdl_api.service import MusicdlWorkerError, _run_musicdl_worker


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
