# musicdl_api

Standalone FastAPI wrapper around `musicdl`.

## What it provides

- short-lived search sessions for keyword search
- session-bound item selection using `sessionId + itemId`
- background download tasks
- health and task status endpoints

## Project structure

```text
musicdl_api/
├── pyproject.toml
├── README.md
├── musicdl_api/
│   ├── __init__.py
│   ├── config.py
│   ├── main.py
│   ├── models.py
│   └── service.py
└── var/
    └── downloads/
```

`var/downloads/` is runtime output and should not be committed.

## Configuration

Environment variables:

- `MUSICDL_API_DOWNLOAD_ROOT`: output root for downloads
  - default: `var/downloads`
- `MUSICDL_API_SESSION_TTL_SECONDS`: search session TTL
  - default: `900`
- `MUSICDL_API_DEFAULT_SOURCES`: comma-separated source list
  - default: `NeteaseMusicClient,QianqianMusicClient,MiguMusicClient,QQMusicClient,KuwoMusicClient`
- `MUSICDL_API_HOST`: bind host for the packaged runner
  - default: `127.0.0.1`
- `MUSICDL_API_PORT`: bind port for the packaged runner
  - default: `8000`
- `MUSICDL_API_RELOAD`: enable uvicorn reload in the packaged runner
  - default: `false`

## Install

```bash
uv pip install -e .
```

`musicdl` is declared as a normal dependency and will be installed alongside
this package. The current code has been validated against `musicdl==2.13.0`.

## Run

Using the package entrypoint:

```bash
MUSICDL_API_RELOAD=true musicdl-api
```

Using uvicorn directly:

```bash
uvicorn musicdl_api.main:app --reload
```

## API

### `GET /health`

Returns service health and resolved config.

### `POST /search`

Runs a synchronous search and returns a completed search session. This endpoint
is kept for simple clients. For mobile or weak-network clients, prefer
`POST /searches`.

Request:

```json
{
  "keyword": "halbmond",
  "sources": [
    "QQMusicClient",
    "KuwoMusicClient"
  ]
}
```

`sources` is optional. If omitted, the service uses `MUSICDL_API_DEFAULT_SOURCES`.

Response:

```json
{
  "sessionId": "session_xxx",
  "expiresAt": "2026-07-06T15:00:00Z",
  "items": [
    {
      "itemId": "11",
      "songName": "Halbmond",
      "singers": "Active Planets",
      "album": "xxx",
      "source": "QQMusicClient",
      "fileSize": "21.13 MB",
      "duration": "00:05:52",
      "extension": "flac"
    }
  ]
}
```

### `GET /sessions/{session_id}`

Returns the current unexpired search session and its items.

### `POST /searches`

Starts a background search task.

Request:

```json
{
  "keyword": "halbmond"
}
```

Response:

```json
{
  "searchId": "search_xxx",
  "status": "queued",
  "keyword": "halbmond",
  "sources": ["NeteaseMusicClient"],
  "result": null
}
```

### `GET /searches/{search_id}`

Returns the search task status. When completed, `result` contains the same
payload as `GET /sessions/{session_id}` and can be used with
`POST /downloads`.

### `POST /downloads`

Request:

```json
{
  "sessionId": "session_xxx",
  "itemId": "11"
}
```

Response:

```json
{
  "taskId": "task_xxx",
  "status": "queued"
}
```

### `GET /downloads/{task_id}`

Returns task state plus download result when finished.

While a task is running, the response also includes a `progress` object derived
from the current output file size:

```json
{
  "status": "running",
  "progress": {
    "savePath": "/abs/path/to/file.flac",
    "fileExists": true,
    "downloadedBytes": 7340032,
    "totalBytes": 22156354,
    "percent": 33.13
  }
}
```

### `GET /downloads/{task_id}/file`

Returns the completed downloaded file as a binary response.

Query parameters:

- `disposition`: `attachment` or `inline`
  - default: `attachment`

Behavior:

- validates that the task exists
- requires `status == completed`
- resolves the file path from `result.savePath` or the task fallback path
- returns the file with a guessed media type and the original filename
- supports HTTP Range requests through FastAPI/Starlette `FileResponse`
