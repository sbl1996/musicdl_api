from pathlib import Path

from musicdl_api.config import Settings
from musicdl_api.models import DownloadRequest, SearchRequest


def test_project_root_is_repository_root(monkeypatch) -> None:
    monkeypatch.delenv("MUSICDL_API_DOWNLOAD_ROOT", raising=False)
    settings = Settings()

    assert settings.project_root == Path(__file__).resolve().parents[1]
    assert settings.download_root == settings.project_root / "var" / "downloads"


def test_request_timeout_overrides_accept_positive_seconds() -> None:
    search = SearchRequest.model_validate({"keyword": "numb", "timeoutSeconds": 180})
    download = DownloadRequest.model_validate(
        {"sessionId": "session_1", "itemId": "1", "timeoutSeconds": 1800}
    )

    assert search.timeout_seconds == 180
    assert download.timeout_seconds == 1800
