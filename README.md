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
  - default: `3600`
- `MUSICDL_API_DEFAULT_SOURCES`: comma-separated source list
  - default: `NeteaseMusicClient,QianqianMusicClient,MiguMusicClient,QQMusicClient,KuwoMusicClient`
## Install

```bash
uv pip install -e .
```

`musicdl` is declared as a normal dependency and will be installed alongside
this package. The current code has been validated against `musicdl==2.13.0`.

## Run

```bash
uvicorn musicdl_api.main:app --reload
```

## Deploy

On a Linux server with `uv` and systemd installed:

```bash
bash deploy/deploy.sh
```

The script creates `.venv`, installs the package, and configures the systemd
service at `http://localhost:8803`.

Optional environment overrides can be placed in `/etc/default/musicdl-api`.
The following limits are configured by default and can be overridden there:

```bash
MUSICDL_API_SEARCH_TIMEOUT_SECONDS=300
MUSICDL_API_DOWNLOAD_TIMEOUT_SECONDS=900
MUSICDL_API_DEBUG_LOGS=true
```

Each musicdl search or download runs in an isolated child process. Its console
output is recorded when `MUSICDL_API_DEBUG_LOGS` is enabled without affecting
Uvicorn's access and error logs, which remain available through
`journalctl -u musicdl-api`. Search logs are written to
`$MUSICDL_API_DOWNLOAD_ROOT/logs/searches/<searchId>.log`; download logs are
written to `$MUSICDL_API_DOWNLOAD_ROOT/tasks/<sessionId>/<taskId>/musicdl.log`.

Requests may override their respective default with a positive `timeoutSeconds`
field. For example: `{"keyword":"numb","timeoutSeconds":180}` for
`POST /searches`, and
`{"sessionId":"...","itemId":"1","timeoutSeconds":1800}` for
`POST /downloads`.

After changing them, restart the service:

```bash
sudo systemctl restart musicdl-api
```

## API

### `GET /health`

Returns service health and resolved config.

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

### `GET /downloads/storage`

Returns the total size and number of regular files under
`MUSICDL_API_DOWNLOAD_ROOT`:

```json
{
  "usedBytes": 22156354,
  "fileCount": 1
}
```

### `DELETE /downloads/storage`

Removes completed and failed song-download task directories under
`MUSICDL_API_DOWNLOAD_ROOT/tasks/`. Queued and running tasks are preserved.
The response reports the actual files and bytes removed:

```json
{
  "deletedBytes": 22156354,
  "deletedFileCount": 1,
  "deletedTaskCount": 1,
  "skippedActiveTaskCount": 0
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
