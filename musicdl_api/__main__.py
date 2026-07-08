from __future__ import annotations

import os

import uvicorn


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
