"""Every route declares whether it shows personal data (AD-13).

Place ``@personal_data(shows)`` under the route decorator::

    @app.get("/", name="home")
    @personal_data(False)
    def home(request: Request) -> HTMLResponse: ...

The guard adds ``Cache-Control: no-store`` to every response unless its endpoint declared
``personal_data(False)``. ``create_app`` calls ``require_declared``, so an undeclared route stops
the app from being built.
"""

from collections.abc import Callable
from typing import Final

from starlette.routing import BaseRoute
from starlette.types import ASGIApp

PERSONAL_DATA_ATTRIBUTE: Final = "__nextron_personal_data__"


def personal_data[F: Callable[..., object]](shows: bool) -> Callable[[F], F]:
    """Declare whether the decorated endpoint shows personal data."""
    if not isinstance(shows, bool):
        raise TypeError(f"personal_data takes a bool, not {type(shows).__name__}")

    def declare(endpoint: F) -> F:
        setattr(endpoint, PERSONAL_DATA_ATTRIBUTE, shows)
        return endpoint

    return declare


def declared_personal_data(endpoint: object) -> bool | None:
    """What ``endpoint`` declared, or ``None`` when it declared nothing."""
    value = getattr(endpoint, PERSONAL_DATA_ATTRIBUTE, None)
    return value if isinstance(value, bool) else None


def _label(route: BaseRoute) -> str:
    path = getattr(route, "path", repr(route))
    methods = sorted(set(getattr(route, "methods", None) or ()) - {"HEAD"})
    return f"{' '.join(methods)} {path}".strip()


def undeclared_routes(app: ASGIApp) -> list[str]:
    """Each route of ``app`` whose endpoint doesn't declare ``personal_data``, as
    ``"<methods> <path>"``. A route without an endpoint (a mount) counts as undeclared."""
    routes: list[BaseRoute] = getattr(app, "routes", [])
    return [
        _label(route)
        for route in routes
        if declared_personal_data(getattr(route, "endpoint", None)) is None
    ]


def require_declared(app: ASGIApp) -> None:
    """Raise ``RuntimeError`` naming every undeclared route of ``app``."""
    undeclared = undeclared_routes(app)
    if undeclared:
        raise RuntimeError(
            "every route must declare @personal_data(True|False); undeclared: "
            + ", ".join(undeclared)
        )
