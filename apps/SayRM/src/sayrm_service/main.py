"""Entrypoint for running the SayRM FastAPI service."""

from __future__ import annotations

import uvicorn

from .app import build_app
from .config import SayRMSettings


def main() -> None:
    settings = SayRMSettings()
    app = build_app(settings)
    uvicorn.run(
        app,
        host=settings.service_host,
        port=settings.service_port,
        reload=False,
    )


if __name__ == "__main__":
    main()

