from __future__ import annotations

import multiprocessing
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import settings


SearchQueryKey = tuple[str, tuple[str, ...]]
ACTIVE_SEARCH_STATUSES = {"queued", "running"}
TERMINAL_SEARCH_STATUSES = {"completed", "failed"}
REUSABLE_DOWNLOAD_STATUSES = {"queued", "running", "completed"}
def _utcnow() -> datetime:
    return datetime.now(UTC)


def search_query_key(keyword: str, sources: list[str]) -> SearchQueryKey:
    return (keyword.strip(), tuple(sources))


from musicdl.musicdl import MusicClient  # noqa: E402
from musicdl.modules.utils.data import SongInfo  # noqa: E402
import musicdl.musicdl as musicdl_module  # noqa: E402


class MusicdlWorkerError(RuntimeError):
    """A musicdl worker failed, exited unexpectedly, or exceeded its timeout."""


_SOURCE_PROGRESS_RE = re.compile(r"^(?P<source>\w+)\.(?P<operation>_?search) >>>")
_RESULT_PROGRESS_RE = re.compile(
    r"process the (?P<item>\d+)(?:st|nd|rd|th) search result on page (?P<page>\d+)"
)


def _progress_task_snapshot(task_id: int, task: Any) -> dict[str, Any]:
    """Normalize musicdl's Rich task into an API-safe progress event."""
    description = task.description
    snapshot: dict[str, Any] = {
        "taskId": task_id,
        "description": description,
        "completed": task.completed,
        "total": task.total,
        "indeterminate": False,
    }
    if description.startswith("Search From Sources >>>"):
        snapshot["stage"] = "sourceSearchUrls"
        return snapshot
    if match := _SOURCE_PROGRESS_RE.match(description):
        snapshot["source"] = match["source"]
        if match["operation"] == "_search":
            snapshot["stage"] = "processingResults"
            # musicdl sets total to the current item number for this task, so it
            # is not a real denominator and must not be rendered as a percentage.
            snapshot["total"] = None
            snapshot["indeterminate"] = True
            if result_match := _RESULT_PROGRESS_RE.search(description):
                snapshot["currentItem"] = int(result_match["item"])
                snapshot["page"] = int(result_match["page"])
        else:
            snapshot["stage"] = "sourceSearchUrls"
    return snapshot


def _reporting_progress_class(report_progress: Any) -> type[Any]:
    """Create a Rich Progress subclass that exposes its live task snapshots."""
    base_progress = musicdl_module.Progress

    class ReportingProgress(base_progress):
        def _report_task(self, task_id: int) -> None:
            report_progress(_progress_task_snapshot(task_id, self.tasks[task_id]))

        def add_task(self, *args: Any, **kwargs: Any) -> int:
            task_id = super().add_task(*args, **kwargs)
            self._report_task(task_id)
            return task_id

        def update(self, task_id: int, *args: Any, **kwargs: Any) -> None:
            super().update(task_id, *args, **kwargs)
            self._report_task(task_id)

        def advance(self, task_id: int, advance: float = 1) -> None:
            super().advance(task_id, advance)
            self._report_task(task_id)

    return ReportingProgress


def _make_client(download_root: str, sources: list[str]) -> MusicClient:
    return MusicClient(
        music_sources=sources,
        init_music_clients_cfg={
            source: {
                "work_dir": str(Path(download_root) / source),
                "search_size_per_source": 5,
            }
            for source in sources
        },
    )


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _coerce_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _search_in_worker(download_root: str, keyword: str, sources: list[str]) -> list[dict[str, Any]]:
    search_results = _make_client(download_root, sources).search(keyword=keyword)
    flattened: list[dict[str, Any]] = []
    item_index = 1
    for source_items in search_results.values():
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
                    "fileSizeBytes": _coerce_int(song_info.file_size_bytes),
                    "duration": song_info.duration,
                    "durationSeconds": _coerce_int(song_info.duration_s),
                    "extension": song_info.ext,
                    "identifier": _coerce_str(song_info.identifier),
                    "downloadProtocol": song_info.protocol,
                    "coverUrl": _coerce_str(song_info.cover_url),
                    "songInfo": song_info.todict(),
                }
            )
            item_index += 1
    return flattened


def _download_in_worker(
    download_root: str, item_data: dict[str, Any], session_id: str, task_id: str
) -> dict[str, Any]:
    song_info = SongInfo.fromdict(item_data["songInfo"])
    task_dir = Path(download_root) / "tasks" / session_id / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    song_info.work_dir = str(task_dir)
    downloaded = _make_client(download_root, [song_info.source]).download(song_infos=[song_info])
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


def _musicdl_worker(
    connection: Any, operation: str, payload: dict[str, Any], log_path: str | None
) -> None:
    """Run musicdl with isolated console fds, then return only serializable data."""
    connection_lock = threading.Lock()

    def send(message: dict[str, Any]) -> None:
        with connection_lock:
            connection.send(message)

    original_progress = musicdl_module.Progress
    try:
        if operation == "search":
            musicdl_module.Progress = _reporting_progress_class(
                lambda progress: send({"type": "progress", "progress": progress})
            )
        output_path = Path(log_path) if log_path else Path(os.devnull)
        if log_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8", buffering=1) as output:
            os.dup2(output.fileno(), 1)
            os.dup2(output.fileno(), 2)
            if operation == "search":
                result = _search_in_worker(**payload)
            elif operation == "download":
                result = _download_in_worker(**payload)
            else:
                raise ValueError(f"Unsupported musicdl operation: {operation}")
        send({"type": "result", "succeeded": True, "value": result})
    except Exception as exc:
        send({"type": "result", "succeeded": False, "value": f"{type(exc).__name__}: {exc}"})
    finally:
        musicdl_module.Progress = original_progress
        connection.close()


def _run_musicdl_worker(
    operation: str,
    payload: dict[str, Any],
    timeout_seconds: int,
    log_path: Path | None = None,
    progress_callback: Any | None = None,
) -> Any:
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_musicdl_worker,
        args=(child_connection, operation, payload, str(log_path) if log_path else None),
        daemon=True,
    )
    process.start()
    child_connection.close()
    try:
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise TimeoutError(f"musicdl {operation} timed out after {timeout_seconds} seconds")
            if not parent_connection.poll(min(remaining_seconds, 0.25)):
                if not process.is_alive():
                    raise MusicdlWorkerError(
                        f"musicdl {operation} worker exited without returning a result"
                    )
                continue
            message = parent_connection.recv()
            if message["type"] == "progress":
                if progress_callback is not None:
                    progress_callback(message["progress"])
                continue
            if not message["succeeded"]:
                raise MusicdlWorkerError(f"musicdl {operation} failed: {message['value']}")
            return message["value"]
    except EOFError as exc:
        raise MusicdlWorkerError(
            f"musicdl {operation} worker exited without returning a result"
        ) from exc
    finally:
        parent_connection.close()
        process.join(timeout=1)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)


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
    timeout_seconds: int | None = None
    progress: dict[tuple[str, int], dict[str, Any]] = field(default_factory=dict)
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
        return _make_client(str(self.download_root), sources)

    def search(
        self,
        keyword: str,
        sources: list[str],
        timeout_seconds: int | None = None,
        log_id: str | None = None,
        progress_callback: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Search sources independently so one stalled provider cannot block the rest."""
        sources = list(dict.fromkeys(sources))
        source_timeout = timeout_seconds or settings.search_timeout_seconds
        search_log_id = log_id or f"request_{uuid.uuid4().hex}"
        results_by_source: dict[str, list[dict[str, Any]]] = {}

        def search_source(source: str) -> tuple[str, list[dict[str, Any]]]:
            def report(progress: dict[str, Any]) -> None:
                if progress_callback is None:
                    return
                # Rich task IDs restart in each child process. The source keeps
                # otherwise identical task IDs distinct in the API response.
                progress_callback({**progress, "source": progress.get("source") or source})

            return source, _run_musicdl_worker(
                "search",
                {
                    "download_root": str(self.download_root),
                    "keyword": keyword,
                    "sources": [source],
                },
                source_timeout,
                self._search_log_path(search_log_id, source),
                report,
            )

        # Each worker has its own deadline. Failures, including timeouts, are
        # deliberately isolated: successful sources still form a valid session.
        with ThreadPoolExecutor(max_workers=min(len(sources), 10)) as executor:
            futures = [executor.submit(search_source, source) for source in sources]
            for future in as_completed(futures):
                try:
                    source, source_items = future.result()
                except Exception:
                    continue
                results_by_source[source] = source_items

        # Workers number their own items from 1. Renumber after merging to keep
        # session item IDs unique and stable in the caller's source order.
        items: list[dict[str, Any]] = []
        for source in sources:
            items.extend(results_by_source.get(source, []))
        for item_index, item in enumerate(items, start=1):
            item["itemId"] = str(item_index)
        return items

    def download(
        self,
        item_data: dict[str, Any],
        session_id: str,
        task_id: str,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        return _run_musicdl_worker(
            "download",
            {
                "download_root": str(self.download_root),
                "item_data": item_data,
                "session_id": session_id,
                "task_id": task_id,
            },
            timeout_seconds or settings.download_timeout_seconds,
            self._download_log_path(session_id, task_id),
        )

    def _search_log_path(self, log_id: str, source: str | None = None) -> Path | None:
        if not settings.debug_logs_enabled:
            return None
        if source is not None:
            safe_source = re.sub(r"[^A-Za-z0-9_.-]", "_", source)
            return self.download_root / "logs" / "searches" / log_id / f"{safe_source}.log"
        return self.download_root / "logs" / "searches" / f"{log_id}.log"

    def _download_log_path(self, session_id: str, task_id: str) -> Path | None:
        if not settings.debug_logs_enabled:
            return None
        return self.download_root / "tasks" / session_id / task_id / "musicdl.log"


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

    def create(
        self, keyword: str, sources: list[str], timeout_seconds: int | None = None
    ) -> SearchTask:
        now = _utcnow()
        key = search_query_key(keyword, sources)
        task = SearchTask(
            search_id=f"search_{uuid.uuid4().hex}",
            status="queued",
            keyword=keyword,
            sources=sources,
            created_at=now,
            updated_at=now,
            timeout_seconds=timeout_seconds,
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
            items = self.facade.search(
                task.keyword,
                task.sources,
                timeout_seconds=task.timeout_seconds,
                log_id=task.search_id,
                progress_callback=lambda progress: self._update_progress(search_id, progress),
            )
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

    def _update_progress(self, search_id: str, progress: dict[str, Any]) -> None:
        with self._lock:
            task = self._tasks.get(search_id)
            if task is None or task.status not in ACTIVE_SEARCH_STATUSES:
                return
            progress_key = (progress.get("source") or "", progress["taskId"])
            task.progress[progress_key] = progress
            task.updated_at = _utcnow()


class DownloadTaskStore:
    def __init__(self, facade: MusicdlFacade, max_workers: int) -> None:
        self.facade = facade
        self._lock = threading.Lock()
        self._maintenance_lock = threading.Lock()
        self._tasks: dict[str, DownloadTask] = {}
        self._item_tasks: dict[tuple[str, str], str] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def create(
        self,
        session_id: str,
        item_id: str,
        item_data: dict[str, Any],
        timeout_seconds: int | None = None,
    ) -> DownloadTask:
        with self._maintenance_lock:
            now = _utcnow()
            key = (session_id, item_id)
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
                existing_task_id = self._item_tasks.get(key)
                if existing_task_id is not None:
                    existing_task = self._tasks.get(existing_task_id)
                    if existing_task is not None and self._is_reusable(existing_task):
                        return existing_task
                    self._item_tasks.pop(key, None)
                self._tasks[task.task_id] = task
                self._item_tasks[key] = task.task_id
            self._executor.submit(self._run_task, task.task_id, item_data, timeout_seconds)
            return task

    def get(self, task_id: str) -> DownloadTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def storage_usage(self) -> dict[str, int]:
        return _directory_usage(self.facade.download_root)

    def cleanup_completed(self) -> dict[str, int]:
        """Remove managed task directories while preserving queued and running tasks."""
        tasks_root = self.facade.download_root / "tasks"
        with self._maintenance_lock:
            with self._lock:
                active_task_ids = {
                    task.task_id
                    for task in self._tasks.values()
                    if task.status in {"queued", "running"}
                }
            deleted_bytes = 0
            deleted_file_count = 0
            deleted_task_count = 0
            skipped_active_task_count = 0
            if not tasks_root.is_dir():
                return {
                    "deletedBytes": 0,
                    "deletedFileCount": 0,
                    "deletedTaskCount": 0,
                    "skippedActiveTaskCount": 0,
                }
            for task_dir in tasks_root.glob("*/task_*"):
                if not task_dir.is_dir() or task_dir.is_symlink():
                    continue
                if task_dir.name in active_task_ids:
                    skipped_active_task_count += 1
                    continue
                usage = _directory_usage(task_dir)
                try:
                    shutil.rmtree(task_dir)
                except OSError:
                    continue
                deleted_bytes += usage["usedBytes"]
                deleted_file_count += usage["fileCount"]
                deleted_task_count += 1
            for session_dir in tasks_root.iterdir():
                if session_dir.is_dir() and not session_dir.is_symlink():
                    try:
                        session_dir.rmdir()
                    except OSError:
                        pass
            return {
                "deletedBytes": deleted_bytes,
                "deletedFileCount": deleted_file_count,
                "deletedTaskCount": deleted_task_count,
                "skippedActiveTaskCount": skipped_active_task_count,
            }

    def _run_task(
        self, task_id: str, item_data: dict[str, Any], timeout_seconds: int | None
    ) -> None:
        self._update(task_id, status="running")
        try:
            task = self.get(task_id)
            assert task is not None
            result = self.facade.download(
                item_data=item_data,
                session_id=task.session_id,
                task_id=task.task_id,
                timeout_seconds=timeout_seconds,
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
            if task.status == "failed":
                key = (task.session_id, task.item_id)
                if self._item_tasks.get(key) == task_id:
                    self._item_tasks.pop(key, None)

    def _is_reusable(self, task: DownloadTask) -> bool:
        if task.status not in REUSABLE_DOWNLOAD_STATUSES:
            return False
        if task.status != "completed":
            return True
        candidate_path = task.result.get("savePath") if task.result else task.save_path
        return bool(candidate_path and Path(candidate_path).is_file())


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


def _directory_usage(directory: Path) -> dict[str, int]:
    used_bytes = 0
    file_count = 0
    if not directory.is_dir():
        return {"usedBytes": used_bytes, "fileCount": file_count}
    for root, _, files in os.walk(directory, followlinks=False):
        for filename in files:
            path = Path(root, filename)
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                used_bytes += path.stat().st_size
                file_count += 1
            except OSError:
                continue
    return {"usedBytes": used_bytes, "fileCount": file_count}


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
        "progress": (
            {
                "updatedAt": task.updated_at,
                "tasks": [task.progress[task_id] for task_id in sorted(task.progress)],
            }
            if task.progress
            else None
        ),
        "result": result,
    }


def task_to_response(task: DownloadTask) -> dict[str, Any]:
    downloaded_bytes = 0
    file_exists = False
    current_save_path = (
        task.result.get("savePath")
        if task.status == "completed" and task.result
        else task.save_path
    )
    if current_save_path:
        save_path = Path(current_save_path)
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
            "savePath": current_save_path,
            "fileExists": file_exists,
            "downloadedBytes": downloaded_bytes,
            "totalBytes": task.total_bytes,
            "percent": percent,
        },
        "result": task.result,
    }
