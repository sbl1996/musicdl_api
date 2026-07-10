from __future__ import annotations

import os
from pathlib import Path


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


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
        self.session_ttl_seconds = int(
            os.environ.get("MUSICDL_API_SESSION_TTL_SECONDS", "3600")
        )
        self.default_sources = _split_csv(
            os.environ.get(
                "MUSICDL_API_DEFAULT_SOURCES",
                "NeteaseMusicClient,QianqianMusicClient,MiguMusicClient,QQMusicClient,KuwoMusicClient",
            )
        )
        self.max_download_workers = int(
            os.environ.get("MUSICDL_API_MAX_DOWNLOAD_WORKERS", "2")
        )


settings = Settings()
