from pathlib import Path

from musicdl_api.config import Settings


def test_project_root_is_repository_root(monkeypatch) -> None:
    monkeypatch.delenv("MUSICDL_API_DOWNLOAD_ROOT", raising=False)
    settings = Settings()

    assert settings.project_root == Path(__file__).resolve().parents[1]
    assert settings.download_root == settings.project_root / "var" / "downloads"
