from __future__ import annotations

import contextlib
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import settings


SearchQueryKey = tuple[str, tuple[str, ...]]
ACTIVE_SEARCH_STATUSES = {"queued", "running"}
TERMINAL_SEARCH_STATUSES = {"completed", "failed"}


@contextlib.contextmanager
def _suppress_console_output():
    stdout_fd = os.dup(1)
    stderr_fd = os.dup(2)
    try:
        with open(os.devnull, "w") as devnull:
            os.dup2(devnull.fileno(), 1)
            os.dup2(devnull.fileno(), 2)
            yield
    finally:
        os.dup2(stdout_fd, 1)
        os.dup2(stderr_fd, 2)
        os.close(stdout_fd)
        os.close(stderr_fd)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def search_query_key(keyword: str, sources: list[str]) -> SearchQueryKey:
    return (keyword.strip(), tuple(sources))


from musicdl.musicdl import MusicClient  # noqa: E402
from musicdl.modules.utils.data import SongInfo  # noqa: E402


@dataclass
class SearchSession:
    session_id: str
    keyword: str
    sources: list[str]
    created_at: datetime
    expires_at: datetime
    items: dict[str, dict[str, Any]]


@dataclass
class SearchTask:
    search_id: str
    status: str
    keyword: str
    sources: list[str]
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    session_id: str | None = None


@dataclass
class DownloadTask:
    task_id: str
    session_id: str
    item_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    save_path: str | None = None
    total_bytes: int | None = None
    error: str | None = None
    result: dict[str, Any] | None = None


class MusicdlFacade:
    def __init__(self) -> None:
        self.download_root = settings.download_root
        self.download_root.mkdir(parents=True, exist_ok=True)

    def create_client(self, sources: list[str]) -> MusicClient:
        init_music_clients_cfg = {
            source: {
                "work_dir": str(self.download_root / source),
                "search_size_per_source": 5,
            }
            for source in sources
        }
        return MusicClient(
            music_sources=sources,
            init_music_clients_cfg=init_music_clients_cfg,
        )

    def search(self, keyword: str, sources: list[str]) -> list[dict[str, Any]]:
        client = self.create_client(sources)
        with _suppress_console_output():
            search_results = client.search(keyword=keyword)
        flattened: list[dict[str, Any]] = []
        item_index = 1
        for _, source_items in search_results.items():
            for song_info in source_items:
                if not isinstance(song_info, SongInfo) or not song_info.with_valid_download_url:
                    continue
                flattened.append(
                    {
                        "itemId": str(item_index),
                        "songName": song_info.song_name,
                        "singers": song_info.singers,
                        "album": song_info.album,
                        "source": song_info.source,
                        "rootSource": song_info.root_source,
                        "fileSize": song_info.file_size,
                        "fileSizeBytes": self._coerce_int(song_info.file_size_bytes),
                        "duration": song_info.duration,
                        "durationSeconds": self._coerce_int(song_info.duration_s),
                        "extension": song_info.ext,
                        "identifier": self._coerce_str(song_info.identifier),
                        "downloadProtocol": song_info.protocol,
                        "coverUrl": self._coerce_str(song_info.cover_url),
                        "songInfo": song_info.todict(),
                    }
                )
                item_index += 1
        return flattened

    def download(self, item_data: dict[str, Any], session_id: str, task_id: str) -> dict[str, Any]:
        song_info = SongInfo.fromdict(item_data["songInfo"])
        task_dir = self.download_root / "tasks" / session_id / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        song_info.work_dir = str(task_dir)
        client = self.create_client([song_info.source])
        with _suppress_console_output():
            downloaded = client.download(song_infos=[song_info])
        if not downloaded:
            raise RuntimeError("musicdl returned no downloaded files")
        output = downloaded[0]
        return {
            "songName": output.song_name,
            "singers": output.singers,
            "album": output.album,
            "source": output.source,
            "extension": output.ext,
            "savePath": output.save_path,
            "fileSize": output.file_size,
            "duration": output.duration,
        }

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_str(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class SearchSessionStore:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._sessions: dict[str, SearchSession] = {}
        self._query_index: dict[SearchQueryKey, str] = {}

    def create(self, keyword: str, sources: list[str], items: list[dict[str, Any]]) -> SearchSession:
        now = _utcnow()
        session = SearchSession(
            session_id=f"session_{uuid.uuid4().hex}",
            keyword=keyword,
            sources=sources,
            created_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
            items={item["itemId"]: item for item in items},
        )
        with self._lock:
            self._evict_expired_locked(now)
            self._sessions[session.session_id] = session
            self._query_index[search_query_key(keyword, sources)] = session.session_id
        return session

    def get(self, session_id: str) -> SearchSession | None:
        now = _utcnow()
        with self._lock:
            self._evict_expired_locked(now)
            return self._sessions.get(session_id)

    def get_by_query(self, keyword: str, sources: list[str]) -> SearchSession | None:
        now = _utcnow()
        with self._lock:
            self._evict_expired_locked(now)
            session_id = self._query_index.get(search_query_key(keyword, sources))
            if session_id is None:
                return None
            return self._sessions.get(session_id)

    def _evict_expired_locked(self, now: datetime) -> None:
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for session_id in expired:
            session = self._sessions.pop(session_id, None)
            if session is not None:
                key = search_query_key(session.keyword, session.sources)
                if self._query_index.get(key) == session_id:
                    self._query_index.pop(key, None)


class SearchTaskStore:
    def __init__(
        self,
        facade: MusicdlFacade,
        sessions: SearchSessionStore,
        max_workers: int,
    ) -> None:
        self.facade = facade
        self.sessions = sessions
        self._lock = threading.Lock()
        self._tasks: dict[str, SearchTask] = {}
        self._inflight_queries: dict[SearchQueryKey, str] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def create(self, keyword: str, sources: list[str]) -> SearchTask:
        now = _utcnow()
        key = search_query_key(keyword, sources)
        task = SearchTask(
            search_id=f"search_{uuid.uuid4().hex}",
            status="queued",
            keyword=keyword,
            sources=sources,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            existing_search_id = self._inflight_queries.get(key)
            if existing_search_id is not None:
                existing_task = self._tasks.get(existing_search_id)
                if existing_task is not None and existing_task.status in ACTIVE_SEARCH_STATUSES:
                    return existing_task
                self._inflight_queries.pop(key, None)
            self._tasks[task.search_id] = task
            self._inflight_queries[key] = task.search_id
        self._executor.submit(self._run_task, task.search_id)
        return task

    def create_completed(self, keyword: str, sources: list[str], session_id: str) -> SearchTask:
        now = _utcnow()
        task = SearchTask(
            search_id=f"search_{uuid.uuid4().hex}",
            status="completed",
            keyword=keyword,
            sources=sources,
            created_at=now,
            updated_at=now,
            session_id=session_id,
        )
        with self._lock:
            self._tasks[task.search_id] = task
        return task

    def get(self, search_id: str) -> SearchTask | None:
        with self._lock:
            return self._tasks.get(search_id)

    def _run_task(self, search_id: str) -> None:
        self._update(search_id, status="running")
        try:
            task = self.get(search_id)
            assert task is not None
            items = self.facade.search(task.keyword, task.sources)
            session = self.sessions.create(
                keyword=task.keyword,
                sources=task.sources,
                items=items,
            )
            self._update(search_id, status="completed", session_id=session.session_id)
        except Exception as exc:
            self._update(search_id, status="failed", error=str(exc))

    def _update(self, search_id: str, **changes: Any) -> None:
        with self._lock:
            task = self._tasks[search_id]
            for key, value in changes.items():
                setattr(task, key, value)
            task.updated_at = _utcnow()
            if task.status in TERMINAL_SEARCH_STATUSES:
                query_key = search_query_key(task.keyword, task.sources)
                if self._inflight_queries.get(query_key) == search_id:
                    self._inflight_queries.pop(query_key, None)


class DownloadTaskStore:
    def __init__(self, facade: MusicdlFacade, max_workers: int) -> None:
        self.facade = facade
        self._lock = threading.Lock()
        self._tasks: dict[str, DownloadTask] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def create(self, session_id: str, item_id: str, item_data: dict[str, Any]) -> DownloadTask:
        now = _utcnow()
        song_info = SongInfo.fromdict(item_data["songInfo"])
        task_dir = self.facade.download_root / "tasks" / session_id / f"task_{uuid.uuid4().hex}"
        task_dir.mkdir(parents=True, exist_ok=True)
        song_info.work_dir = str(task_dir)
        predicted_save_path = song_info.save_path
        task = DownloadTask(
            task_id=task_dir.name,
            session_id=session_id,
            item_id=item_id,
            status="queued",
            created_at=now,
            updated_at=now,
            save_path=predicted_save_path,
            total_bytes=item_data.get("fileSizeBytes"),
        )
        with self._lock:
            self._tasks[task.task_id] = task
        self._executor.submit(self._run_task, task.task_id, item_data)
        return task

    def get(self, task_id: str) -> DownloadTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def _run_task(self, task_id: str, item_data: dict[str, Any]) -> None:
        self._update(task_id, status="running")
        try:
            task = self.get(task_id)
            assert task is not None
            result = self.facade.download(
                item_data=item_data,
                session_id=task.session_id,
                task_id=task.task_id,
            )
            self._update(task_id, status="completed", result=result)
        except Exception as exc:
            self._update(task_id, status="failed", error=str(exc))

    def _update(self, task_id: str, **changes: Any) -> None:
        with self._lock:
            task = self._tasks[task_id]
            for key, value in changes.items():
                setattr(task, key, value)
            task.updated_at = _utcnow()


class AppState:
    def __init__(self) -> None:
        self.facade = MusicdlFacade()
        self.sessions = SearchSessionStore(ttl_seconds=settings.session_ttl_seconds)
        self.searches = SearchTaskStore(
            facade=self.facade,
            sessions=self.sessions,
            max_workers=settings.max_download_workers,
        )
        self.downloads = DownloadTaskStore(
            facade=self.facade,
            max_workers=settings.max_download_workers,
        )


def session_to_response(session: SearchSession) -> dict[str, Any]:
    items = []
    for item in session.items.values():
        public_item = {k: v for k, v in item.items() if k != "songInfo"}
        items.append(public_item)
    return {
        "sessionId": session.session_id,
        "keyword": session.keyword,
        "sources": session.sources,
        "createdAt": session.created_at,
        "expiresAt": session.expires_at,
        "itemCount": len(items),
        "items": items,
    }


def search_task_to_response(task: SearchTask, sessions: SearchSessionStore) -> dict[str, Any]:
    result = None
    if task.session_id:
        session = sessions.get(task.session_id)
        if session is not None:
            result = session_to_response(session)
    return {
        "searchId": task.search_id,
        "status": task.status,
        "keyword": task.keyword,
        "sources": task.sources,
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "error": task.error,
        "result": result,
    }


def task_to_response(task: DownloadTask) -> dict[str, Any]:
    downloaded_bytes = 0
    file_exists = False
    if task.save_path:
        save_path = Path(task.save_path)
        if save_path.exists():
            file_exists = True
            try:
                downloaded_bytes = save_path.stat().st_size
            except OSError:
                downloaded_bytes = 0
    percent = None
    if task.total_bytes and task.total_bytes > 0:
        percent = round(min(downloaded_bytes / task.total_bytes, 1.0) * 100, 2)
    return {
        "taskId": task.task_id,
        "status": task.status,
        "sessionId": task.session_id,
        "itemId": task.item_id,
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "error": task.error,
        "progress": {
            "savePath": task.save_path,
            "fileExists": file_exists,
            "downloadedBytes": downloaded_bytes,
            "totalBytes": task.total_bytes,
            "percent": percent,
        },
        "result": task.result,
    }
