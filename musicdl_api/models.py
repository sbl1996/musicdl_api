from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    sources: list[str] | None = None


class SearchItem(BaseModel):
    item_id: str = Field(alias="itemId")
    song_name: str | None = Field(alias="songName", default=None)
    singers: str | None = None
    album: str | None = None
    source: str | None = None
    root_source: str | None = Field(alias="rootSource", default=None)
    file_size: str | None = Field(alias="fileSize", default=None)
    file_size_bytes: int | None = Field(alias="fileSizeBytes", default=None)
    duration: str | None = None
    duration_seconds: int | None = Field(alias="durationSeconds", default=None)
    extension: str | None = None
    identifier: str | None = None
    download_protocol: str | None = Field(alias="downloadProtocol", default=None)
    cover_url: str | None = Field(alias="coverUrl", default=None)


class SearchResponse(BaseModel):
    session_id: str = Field(alias="sessionId")
    keyword: str
    sources: list[str]
    created_at: datetime = Field(alias="createdAt")
    expires_at: datetime = Field(alias="expiresAt")
    item_count: int = Field(alias="itemCount")
    items: list[SearchItem]


class SearchTaskResponse(BaseModel):
    search_id: str = Field(alias="searchId")
    status: Literal["queued", "running", "completed", "failed"]
    keyword: str
    sources: list[str]
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    error: str | None = None
    result: SearchResponse | None = None


class DownloadRequest(BaseModel):
    session_id: str = Field(alias="sessionId", min_length=1)
    item_id: str = Field(alias="itemId", min_length=1)


class DownloadResult(BaseModel):
    song_name: str | None = Field(alias="songName", default=None)
    singers: str | None = None
    album: str | None = None
    source: str | None = None
    extension: str | None = None
    save_path: str | None = Field(alias="savePath", default=None)
    file_size: str | None = Field(alias="fileSize", default=None)
    duration: str | None = None


class DownloadProgress(BaseModel):
    save_path: str | None = Field(alias="savePath", default=None)
    file_exists: bool = Field(alias="fileExists")
    downloaded_bytes: int = Field(alias="downloadedBytes")
    total_bytes: int | None = Field(alias="totalBytes", default=None)
    percent: float | None = None


class DownloadTaskResponse(BaseModel):
    task_id: str = Field(alias="taskId")
    status: Literal["queued", "running", "completed", "failed"]
    session_id: str = Field(alias="sessionId")
    item_id: str = Field(alias="itemId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    error: str | None = None
    progress: DownloadProgress
    result: DownloadResult | None = None


class DownloadStorageResponse(BaseModel):
    used_bytes: int = Field(alias="usedBytes")
    file_count: int = Field(alias="fileCount")


class DownloadCleanupResponse(BaseModel):
    deleted_bytes: int = Field(alias="deletedBytes")
    deleted_file_count: int = Field(alias="deletedFileCount")
    deleted_task_count: int = Field(alias="deletedTaskCount")
    skipped_active_task_count: int = Field(alias="skippedActiveTaskCount")


class HealthResponse(BaseModel):
    status: Literal["ok"]
    download_root: str = Field(alias="downloadRoot")
    session_ttl_seconds: int = Field(alias="sessionTtlSeconds")
    default_sources: list[str] = Field(alias="defaultSources")
