"""Route rules for the dashboard (AD-13): what every route declares, and what every GET and POST
gets.

**Personal data.** Every route declares whether it shows personal data, with
``@personal_data(shows)`` under the route decorator::

    @app.get("/", name="home")
    @personal_data(False)
    def home(request: Request) -> HTMLResponse: ...

The guard adds ``Cache-Control: no-store`` to every response unless its endpoint declared
``personal_data(False)``. ``create_app`` calls ``require_declared``, so an undeclared route stops
the app from being built.

**Read-only GETs and checked POSTs.** ``create_app`` sets ``dashboard_route_class(units)`` as
the router's route class, so every route added with ``@app.get``, ``@app.post`` or
``add_api_route`` is built by it:

- A route's methods are exactly ``{"GET"}`` or ``{"POST"}``; anything else (``PUT``, ``HEAD``,
  ``GET`` and ``POST`` together) raises ``ValueError`` when the route is added.
- A GET endpoint is wrapped (``functools.wraps``, so ``@personal_data`` carries over) to run
  inside ``units.open(read_only=True)`` on the handler's own thread: any database write in it
  raises, so no GET writes to the database. (``GET /enter`` is the one GET that changes state by
  design: it consumes the one-time code and creates a session, neither in the database.) The
  endpoint must be a plain sync function or method: an ``async def`` or generator function, or
  any other callable (an object with an ``async def __call__`` would run on the event loop,
  outside the unit), raises ``TypeError``. A GET handler renders inside its body, because the
  unit of work closes before FastAPI serialises what it returns.
- A POST gets ``Depends(require_csrf)`` (``nextron.web.csrf``) first in its dependencies. A
  POST handler opens its own write unit of work in its body (AD-3).

``create_app`` then calls ``require_route_rules``, which raises ``RuntimeError`` naming each
route ``route_rule_problems`` finds: one that isn't an ``APIRoute``, a GET without the read-only
wrapper, a POST whose first dependency isn't ``require_csrf``, or any other method set. That
catches a route added some other way, such as a plain ``APIRoute`` appended to the router.
FastAPI's ``include_router`` adds one opaque route for a whole router, which the rules can't
look inside, so it is refused too: add dashboard routes to the app itself.
"""

import functools
import inspect
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Any, Final, Protocol

from fastapi import Depends
from fastapi.params import Depends as DependsParam
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute
from starlette.types import ASGIApp

from nextron.web.csrf import require_csrf

PERSONAL_DATA_ATTRIBUTE: Final = "__nextron_personal_data__"
READ_ONLY_ATTRIBUTE: Final = "__nextron_read_only__"
GET_ONLY: Final = frozenset({"GET"})
POST_ONLY: Final = frozenset({"POST"})


class Units(Protocol):
    """What the dashboard needs of ``nextron.db.uow.UnitOfWorkFactory``."""

    def open(self, *, read_only: bool = False) -> AbstractContextManager[object]: ...


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
    """``"<methods> <path>"``, or ``"<TypeName>"`` for a route without a path (FastAPI's
    ``include_router`` adds one such route for the whole router)."""
    path = getattr(route, "path", None)
    if path is None:
        return f"<{type(route).__name__}>"
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


# Read-only GETs and checked POSTs ------------------------------------------------------------


def is_read_only(endpoint: object) -> bool:
    """Whether ``endpoint`` is a read-only wrapper made by the dashboard route class. The mark
    names the wrapper itself, so a function that copied the wrapper's attributes doesn't pass."""
    return endpoint is not None and getattr(endpoint, READ_ONLY_ATTRIBUTE, None) is endpoint


def _refuse_async(endpoint: Callable[..., Any]) -> None:
    target = inspect.unwrap(endpoint)
    plain = inspect.isfunction(target) or inspect.ismethod(target)
    for function in (endpoint, target):
        if (
            not plain
            or inspect.iscoroutinefunction(function)
            or inspect.isasyncgenfunction(function)
            or inspect.isgeneratorfunction(function)
        ):
            raise TypeError(
                "a dashboard GET handler is a plain sync def, not an async or generator "
                f"function or another callable: {getattr(endpoint, '__qualname__', endpoint)!r}"
            )


def _read_only_endpoint(endpoint: Callable[..., Any], units: Units) -> Callable[..., Any]:
    """``endpoint`` run inside one read-only unit of work, on the thread that calls it."""
    _refuse_async(endpoint)

    @functools.wraps(endpoint)
    def run_read_only(*args: Any, **kwargs: Any) -> Any:
        with units.open(read_only=True):
            return endpoint(*args, **kwargs)

    setattr(run_read_only, READ_ONLY_ATTRIBUTE, run_read_only)
    return run_read_only


def dashboard_route_class(units: Units) -> type[APIRoute]:
    """The route class of the dashboard: read-only GETs, CSRF-checked POSTs, nothing else."""

    class DashboardRoute(APIRoute):
        def __init__(
            self,
            path: str,
            endpoint: Callable[..., Any],
            *,
            methods: set[str] | list[str] | None = None,
            dependencies: Sequence[DependsParam] | None = None,
            **kwargs: Any,
        ) -> None:
            chosen = frozenset(m.upper() for m in methods) if methods is not None else GET_ONLY
            if chosen == GET_ONLY:
                endpoint = _read_only_endpoint(endpoint, units)
            elif chosen == POST_ONLY:
                dependencies = [Depends(require_csrf), *(dependencies or [])]
            else:
                raise ValueError(
                    f"dashboard route {path} has methods {sorted(chosen)}; a route is exactly "
                    "GET (read-only) or exactly POST (CSRF-checked)"
                )
            super().__init__(
                path, endpoint, methods=methods, dependencies=dependencies, **kwargs
            )

    return DashboardRoute


def route_rule_problems(app: ASGIApp) -> list[str]:
    """Each route of ``app`` that breaks the route rules, as ``"<methods> <path>: <why>"``."""
    problems = []
    routes: list[BaseRoute] = getattr(app, "routes", [])
    for route in routes:
        label = _label(route)
        if not isinstance(route, APIRoute):
            problems.append(f"{label}: not an APIRoute")
            continue
        methods = frozenset(route.methods or ())
        if methods == GET_ONLY:
            if not is_read_only(route.endpoint):
                problems.append(f"{label}: a GET outside a read-only unit of work")
        elif methods == POST_ONLY:
            calls = [dependency.call for dependency in route.dependant.dependencies]
            if calls[:1] != [require_csrf]:
                problems.append(f"{label}: a POST whose first dependency isn't require_csrf")
        else:
            problems.append(f"{label}: methods other than exactly GET or exactly POST")
    return problems


def require_route_rules(app: ASGIApp) -> None:
    """Raise ``RuntimeError`` naming every route of ``app`` that breaks the route rules."""
    problems = route_rule_problems(app)
    if problems:
        raise RuntimeError("dashboard route rules broken: " + "; ".join(problems))
