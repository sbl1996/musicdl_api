from __future__ import annotations

import os
from pathlib import Path


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _positive_int(name: str, default: str) -> int:
    value = int(os.environ.get(name, default))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


class Settings:
    def __init__(self) -> None:
        root_dir = Path(__file__).resolve().parents[1]
        self.project_root = root_dir
        self.download_root = Path(
            os.environ.get(
                "MUSICDL_API_DOWNLOAD_ROOT",
                str(root_dir / "var" / "downloads"),
            )
        ).resolve()
        self.session_ttl_seconds = _positive_int(
            "MUSICDL_API_SESSION_TTL_SECONDS", "3600"
        )
        self.default_sources = _split_csv(
            os.environ.get(
                "MUSICDL_API_DEFAULT_SOURCES",
                "NeteaseMusicClient,QianqianMusicClient,MiguMusicClient,QQMusicClient,KuwoMusicClient",
            )
        )
        self.max_download_workers = _positive_int(
            "MUSICDL_API_MAX_DOWNLOAD_WORKERS", "2"
        )
        self.search_timeout_seconds = _positive_int(
            "MUSICDL_API_SEARCH_TIMEOUT_SECONDS", "300"
        )
        self.download_timeout_seconds = _positive_int(
            "MUSICDL_API_DOWNLOAD_TIMEOUT_SECONDS", "900"
        )


settings = Settings()
