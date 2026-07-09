# AGENTS.md

This file provides guidance to coding agents when working with code in this repository.

## Overview

A FastAPI service wrapping the `musicdl` library (pinned `musicdl==2.13.0`). It exposes short-lived search sessions, async search/download tasks, and a file-served download endpoint. All state is in-memory; downloads are persisted under `var/downloads/`.

## Commands

```bash
# Install (editable) — uses uv
uv pip install -e ".[dev]"

# Run dev server
uvicorn musicdl_api.main:app --reload

# Run tests (no tests exist yet; pytest + httpx are in the dev extra)
pytest

# Deploy to the production server
git push
sleep 3
ssh ark-1 "zsh -lic 'cd ~/Code/musicdl_api && git restore . && proxy_on && git pull && proxy_off && sleep 1 && bash deploy/deploy.sh'" # if the proxy fails, run `git pull` directly
```

## Configuration

All via environment variables (see `musicdl_api/config.py`), read once at import time into the `settings` singleton:

- `MUSICDL_API_DOWNLOAD_ROOT` (default `<repo>/var/downloads`)
- `MUSICDL_API_SESSION_TTL_SECONDS` (default `900`)
- `MUSICDL_API_DEFAULT_SOURCES` (CSV; default includes Netease/Qianqian/Migu/QQ/Kuwo clients)
- `MUSICDL_API_MAX_DOWNLOAD_WORKERS` (default `2`)

`config.Settings` resolves paths from `parents[2]` of its own file — keep the package nested under the repo root or the default download root drifts.

## Architecture

The package is a thin FastAPI layer (`main.py`) over an in-memory state layer (`service.py`) that wraps `musicdl`. Pydantic models in `models.py` use `alias` for camelCase API I/O.

**State lives in `AppState`** (single module-level instance in `main.py`), holding three stores plus a `MusicdlFacade`:

- `SearchSessionStore` — keyword+sources → session lookup index (`_query_index`) for dedup; sessions TTL-evict lazily on access. This is the cache that lets repeated searches return instantly.
- `SearchTaskStore` — async search tasks on a `ThreadPoolExecutor`. `_inflight_queries` dedupes active searches: a second request for the same `(keyword, sources)` while one is queued/running returns the existing task instead of spawning a duplicate.
- `DownloadTaskStore` — async download tasks. `_item_tasks` keyed by `(session_id, item_id)` reuses existing tasks when the prior task is queued/running, or completed with the output file still on disk (`_is_reusable`). Failed tasks evict the index entry so a retry can start fresh.

**`MusicdlFacade`** adapts `musicdl`'s `MusicClient`/`SongInfo`:
- Builds a fresh `MusicClient` per call (one `work_dir` per source under `download_root/<source>`).
- Search flattens results into item dicts keyed by sequential `itemId`; the full `SongInfo` is serialized into each item's `songInfo` field (via `todict()`), so downloads can later reconstruct it with `SongInfo.fromdict` without re-querying. The `songInfo` field is stripped from API responses in `session_to_response`.
- All `musicdl` calls run inside `_suppress_console_output()`, which swaps fd 1/2 to devnull — `musicdl` prints to stdout/stderr and would corrupt FastAPI's output otherwise. Keep blocking calls wrapped (`run_in_threadpool` for the sync `/search` endpoint; executor for background tasks).
- Download writes to `download_root/tasks/<session_id>/<task_id>/` (the task_id *is* the dir name, `task_<uuid.hex>`).

**Response shaping**: `session_to_response` / `search_task_to_response` / `task_to_response` (in `service.py`) build the JSON dicts returned by handlers. Download progress is computed on-the-fly by stat-ing `task.save_path` against `total_bytes` — there is no progress callback from `musicdl`.

## Conventions

- Use short English commit messages, preferably following Conventional Commits
- IDs are prefixed by type: `session_`, `search_`, `task_`, all `uuid4().hex`.
- New endpoints should return the raw dict produced by the `*_to_response` helpers so FastAPI's `response_model` validates/aliases it — don't construct camelCase dicts inline.
- Background work uses threads, not asyncio, because `musicdl` is synchronous. State mutations go through each store's `threading.Lock`.
