from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
import uvicorn

from .config import settings
from .models import (
    DownloadRequest,
    DownloadTaskResponse,
    HealthResponse,
    SearchRequest,
    SearchResponse,
    SearchTaskResponse,
)
from .service import AppState, search_task_to_response, session_to_response, task_to_response


app = FastAPI(title="musicdl_api", version="0.1.0")
state = AppState()


@app.get("/health", response_model=HealthResponse)
def health() -> dict:
    return {
        "status": "ok",
        "downloadRoot": str(settings.download_root),
        "sessionTtlSeconds": settings.session_ttl_seconds,
        "defaultSources": settings.default_sources,
    }


@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> dict:
    sources = request.sources or settings.default_sources
    if not sources:
        raise HTTPException(status_code=400, detail="No sources configured")
    items = await run_in_threadpool(state.facade.search, request.keyword, sources)
    session = state.sessions.create(
        keyword=request.keyword,
        sources=sources,
        items=items,
    )
    return session_to_response(session)


@app.post("/searches", response_model=SearchTaskResponse, status_code=202)
def create_search(request: SearchRequest) -> dict:
    sources = request.sources or settings.default_sources
    if not sources:
        raise HTTPException(status_code=400, detail="No sources configured")
    task = state.searches.create(keyword=request.keyword, sources=sources)
    return search_task_to_response(task, state.sessions)


@app.get("/searches/{search_id}", response_model=SearchTaskResponse)
def get_search(search_id: str) -> dict:
    task = state.searches.get(search_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Search task not found")
    return search_task_to_response(task, state.sessions)


@app.get("/sessions/{session_id}", response_model=SearchResponse)
def get_session(session_id: str) -> dict:
    session = state.sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Search session not found or expired")
    return session_to_response(session)


@app.post("/downloads", response_model=DownloadTaskResponse, status_code=202)
def create_download(request: DownloadRequest) -> dict:
    session = state.sessions.get(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Search session not found or expired")
    item = session.items.get(request.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Search item not found in session")
    task = state.downloads.create(
        session_id=session.session_id,
        item_id=request.item_id,
        item_data=item,
    )
    return task_to_response(task)


@app.get("/downloads/{task_id}", response_model=DownloadTaskResponse)
def get_download(task_id: str) -> dict:
    task = state.downloads.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Download task not found")
    return task_to_response(task)


@app.get("/downloads/{task_id}/file")
def get_download_file(
    task_id: str,
    disposition: str = Query(default="attachment", pattern="^(attachment|inline)$"),
) -> FileResponse:
    task = state.downloads.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Download task not found")
    if task.status != "completed":
        raise HTTPException(status_code=409, detail="Download task is not completed")

    candidate_path = None
    if task.result and task.result.get("savePath"):
        candidate_path = task.result["savePath"]
    elif task.save_path:
        candidate_path = task.save_path
    if not candidate_path:
        raise HTTPException(status_code=404, detail="Downloaded file path is unavailable")

    file_path = Path(candidate_path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Downloaded file not found")

    media_type, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(
        path=file_path,
        media_type=media_type or "application/octet-stream",
        filename=file_path.name,
        content_disposition_type=disposition,
    )


def main() -> None:
    host = os.environ.get("MUSICDL_API_HOST", "127.0.0.1")
    port = int(os.environ.get("MUSICDL_API_PORT", "8000"))
    reload_enabled = os.environ.get("MUSICDL_API_RELOAD", "false").lower() == "true"
    uvicorn.run(
        "musicdl_api.main:app",
        host=host,
        port=port,
        reload=reload_enabled,
    )


if __name__ == "__main__":
    main()
