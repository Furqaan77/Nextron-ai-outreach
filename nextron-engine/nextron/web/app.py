"""The dashboard's FastAPI application factory (FR-58 seed).

``GET /health`` reports the environment, the engine version and the process uptime in whole
seconds, and nothing else: no personal data, paths, hostnames or settings. The interactive API
docs and the OpenAPI schema are off, because the dashboard serves no third-party scripts
(AD-13). Session, Host-header and CSRF checks arrive in Stories 1.4 and 1.5.
"""

import time
from collections.abc import Callable

from fastapi import FastAPI


def create_app(
    *, env: str, version: str, monotonic: Callable[[], float] = time.monotonic
) -> FastAPI:
    """Build the dashboard app. ``monotonic`` is injectable so tests control the uptime."""
    started = monotonic()
    app = FastAPI(
        title="Nextron Outreach Engine",
        version=version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")
    def health() -> dict[str, str | int]:
        return {"env": env, "version": version, "uptime_s": int(monotonic() - started)}

    return app
