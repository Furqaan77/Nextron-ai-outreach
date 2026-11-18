"""Google OAuth contracts (AD-12, AD-13, AD-14, Story 1.9), each with a proof on a probe.

- **Only a POST starts a consent flow.** Every GET route of the dashboard, the polled grant row
  included, called with a session in every state a grant can be in, never builds a listener or
  opens the browser; ``main.build_app``'s own app neither (its ``LoopbackListener`` replaced by
  a recorder). In the source (AST), the only call to ``start_grant`` under ``nextron/`` is in
  ``google_grant_start``, a POST route of ``nextron/web/app.py``.
- **Building the app constructs nothing live**: no HTTP client, no listener, no browser.
- **``main.build_app``'s own ``GoogleAccess`` is wired for the environment**: a consent flow
  started by its Grant POST runs on the real daemon thread and reads the ``sink_allowlist``
  setting from the database there (``read_sink_allowlist``), so outside ``prod`` an allowlisted
  account is granted and any other is refused.
- **The Google keyring names** are valid secret names and collide with no credential's entry
  or the dashboard's one-time code.
"""

import ast
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

import nextron.app.setup.api as setup_api
from nextron import main
from nextron.app.ops.api import OPEN_CODE_SECRET
from nextron.app.setup.api import Setup
from nextron.bootstrap import load_bootstrap
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import credentials as credential_registry
from nextron.domain.google_oauth import (
    CLIENT_META_NAME,
    CLIENT_SECRET_NAME,
    GRANTS,
    ClientConfig,
    GrantMeta,
    encode_client_config,
    encode_grant_meta,
)
from nextron.domain.settings_schema import Key
from nextron.ports.secrets import is_secret_name
from nextron.web.routing import personal_data
from tests.asgi import request
from tests.contracts.support import ENGINE_ROOT, copy_with_probe
from tests.dashboard import FORM, Dashboard, RecordingBrowser, body_hx_headers
from tests.fakes.clock import FrozenClock
from tests.fakes.google_oauth import CLIENT_ID, FakeConsentListener, FakeGoogleOAuth, token_set
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits

START_GRANT: Final = "start_grant"
STARTING_ROUTE: Final = "google_grant_start"
PATH_PARAMETER: Final = re.compile(r"\{([a-z_]+)\}")
PARAMETER_VALUES: Final = {
    "name": ["app.css", "htmx-2.0.11.min.js", "nope.js"],
    "grant": [*(str(spec.grant) for spec in GRANTS), "gmail", "start_grant"],
    "lead_id": ["01KA0000000000000000000000", "start_grant"],
    "contact_id": ["01KA0000000000000000000000", "start_grant"],  # Story 3.13's erase step
    "ticket": ["Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAb7dEf0hIj3", "start_grant"],
}
QUERIES: Final = [b"", b"grant=internal_notify", b"grant=postmaster&confirm=yes&path=C:%5Cx.json"]


def get_paths(app: object) -> list[str]:
    """Every concrete path to GET on ``app``: each GET route, its parameters filled in."""
    paths = []
    for route in getattr(app, "routes"):
        if getattr(route, "methods", None) != {"GET"}:
            continue
        template = str(getattr(route, "path"))
        filled = [template]
        for name in PATH_PARAMETER.findall(template):  # every combination of the values
            filled = [
                path.replace(f"{{{name}}}", value)
                for path in filled
                for value in PARAMETER_VALUES[name]
            ]
        paths += filled
    return paths


def seed(secrets: InMemorySecrets, state: str) -> None:
    """Put a client and grants in every state a GET could find."""
    service = secrets.service
    secrets.store[(service, CLIENT_SECRET_NAME)] = encode_client_config(
        ClientConfig(CLIENT_ID, "Cs4Vq8Lp2Xz6Nw1Rt5Hy")
    )
    if state == "granted":
        for spec in GRANTS:
            secrets.store[(service, spec.secret_name)] = "1//stored"
            secrets.store[(service, spec.meta_name)] = encode_grant_meta(
                GrantMeta("mac@nextronai.com", datetime(2026, 11, 17, 9, 30, tzinfo=UTC))
            )


@pytest.mark.parametrize("state", ["no client", "client only", "granted", "waiting"])
def test_no_get_route_starts_a_flow_or_opens_the_browser(
    state: str, units: UnitOfWorkFactory
) -> None:
    dashboard = Dashboard(units=units)  # the Settings page reads the database
    if state != "no client":
        seed(dashboard.secrets, "granted" if state == "granted" else "client only")
    cookie = dashboard.open_session()
    if state == "waiting":
        token = dashboard.csrf_token(cookie)
        dashboard.post(
            "/credentials/google/grant",
            cookie=cookie,
            token=token,
            headers=[(b"content-type", b"application/x-www-form-urlencoded")],
            body=b"grant=postmaster",
        )
        assert len(dashboard.listeners) == 1 and len(dashboard.browser.urls) == 1
    before = (len(dashboard.listeners), len(dashboard.browser.urls), len(dashboard.jobs))
    paths = get_paths(dashboard.app)

    assert "/credentials/google/grant/internal_notify" in paths
    answered: dict[str, int] = {}
    for path in paths:
        for query in QUERIES:
            answered[path] = dashboard.get(path, cookie=cookie, query=query).status

    assert answered["/credentials"] == answered["/credentials/google/grant/postmaster"] == 200
    assert answered["/credentials/google/grant/gmail"] == 404

    assert (len(dashboard.listeners), len(dashboard.browser.urls), len(dashboard.jobs)) == before
    assert dashboard.oauth.calls == []


def test_the_get_check_would_see_a_get_that_starts_a_flow(units: UnitOfWorkFactory) -> None:
    dashboard = Dashboard(units=units)
    seed(dashboard.secrets, "client only")
    cookie = dashboard.open_session()
    app = dashboard.app

    @app.get("/probe-start", name="probe_start")
    @personal_data(True)
    def probe_start() -> str:
        dashboard.google.start_grant("postmaster")
        return "started"

    for path in get_paths(app):
        dashboard.get(path, cookie=cookie)

    assert len(dashboard.listeners) == 1 and len(dashboard.browser.urls) == 1


def test_build_apps_own_app_starts_nothing_on_any_get(
    monkeypatch: pytest.MonkeyPatch, units: UnitOfWorkFactory
) -> None:
    listened: list[object] = []
    monkeypatch.setattr(main, "LoopbackListener", lambda: listened.append("listen"))
    config = load_bootstrap()
    secrets = InMemorySecrets(config.env)
    seed(secrets, "granted")
    browser = RecordingBrowser()
    app = main.build_app(
        config, units=units, secrets=secrets, google_oauth=FakeGoogleOAuth(), browser=browser
    )
    host = f"127.0.0.1:{config.port}".encode()
    entry = main.build_entry(config, secrets=secrets)
    code = entry.issue_code().encode()
    entered = request(app, "/enter", query=b"t=" + code, headers=[(b"host", host)])
    cookie = (entered.header(b"set-cookie") or b"").split(b";", 1)[0]
    assert cookie

    for path in get_paths(app):
        for query in QUERIES:
            request(app, path, query=query, headers=[(b"host", host), (b"cookie", cookie)])

    assert listened == [] and browser.urls == []


def test_building_the_app_constructs_nothing_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """The live HTTP client and the browser stay refused (``tests/live_guard.py``), so building
    and browsing the app would raise if either were reached; the listener is a recorder."""
    listened: list[object] = []
    monkeypatch.setattr(main, "LoopbackListener", lambda: listened.append("listen"))
    config = load_bootstrap()

    app = main.build_app(config, units=FakeUnits(), secrets=InMemorySecrets(config.env))

    assert listened == []
    assert {"google_grant_start", "google_grant_row"} <= {
        getattr(route, "name", None) for route in app.routes
    }


def test_build_apps_own_flow_runs_on_its_thread_and_reads_the_allowlist(
    monkeypatch: pytest.MonkeyPatch, units: UnitOfWorkFactory
) -> None:
    state, verifier = "St3Lm8Qw2Zn6Vb9Hx4Ck" * 2, "Vf2Bn6Qr9Lk3Hs7Wd1Zm" * 4
    monkeypatch.setattr(setup_api, "token_urlsafe", lambda size: {32: state, 64: verifier}[size])
    listeners: list[FakeConsentListener] = []

    def listener() -> FakeConsentListener:
        listeners.append(FakeConsentListener({"state": (state,), "code": ("the-code",)}))
        return listeners[-1]

    monkeypatch.setattr(main, "LoopbackListener", listener)
    clock = FrozenClock(datetime(2026, 11, 17, 9, 30, tzinfo=UTC))
    with units.open():
        Setup(clock).set(Key.SINK_ALLOWLIST, ("mac@nextronai.com",))
    config = load_bootstrap()
    assert config.env == "test"  # the sink applies
    secrets = InMemorySecrets(config.env)
    seed(secrets, "client only")
    oauth = FakeGoogleOAuth()
    oauth.exchanges += [
        token_set(email="mac@nextronai.com", refresh_token="1//internal"),
        token_set(
            scope="https://www.googleapis.com/auth/postmaster.readonly",
            email="other@nextronai.com",
            refresh_token="1//postmaster",
        ),
    ]
    browser = RecordingBrowser()
    app = main.build_app(
        config, units=units, secrets=secrets, clock=clock, google_oauth=oauth, browser=browser
    )
    host = f"127.0.0.1:{config.port}".encode()
    code = main.build_entry(config, secrets=secrets, clock=clock).issue_code().encode()
    entered = request(app, "/enter", query=b"t=" + code, headers=[(b"host", host)])
    cookie = (entered.header(b"set-cookie") or b"").split(b";", 1)[0]
    home = request(app, "/", headers=[(b"host", host), (b"cookie", cookie)])
    token = str(body_hx_headers(home.text)["X-CSRF-Token"])

    def grant_and_wait(grant: str) -> str:
        started = request(
            app,
            "/credentials/google/grant",
            method="POST",
            headers=[
                (b"host", host),
                (b"cookie", cookie),
                (b"origin", b"http://" + host),
                (b"x-csrf-token", token.encode()),
                (b"content-type", FORM),
            ],
            body=f"grant={grant}".encode(),
        )
        assert started.status == 200, started
        deadline = time.monotonic() + 10
        while True:
            row = request(
                app,
                f"/credentials/google/grant/{grant}",
                headers=[(b"host", host), (b"cookie", cookie)],
            )
            if "Waiting for consent" not in row.text:
                return row.text
            assert time.monotonic() < deadline, f"the {grant} flow never ended"
            time.sleep(0.02)

    granted = grant_and_wait("internal_notify")
    refused = grant_and_wait("postmaster")

    assert "Granted for mac@nextronai.com" in granted
    assert "Consent failed: outside prod, the account must be on the Dev and test allowlist" in (
        " ".join(refused.split())
    )
    assert len(listeners) == 2 and all(each.closed for each in listeners)
    assert len(browser.urls) == 2
    assert secrets.store[(secrets.service, "google/internal_notify")] == "1//internal"
    assert (secrets.service, "google/postmaster") not in secrets.store
    assert oauth.revoked == ["1//postmaster"]  # the refused token


# Only google_grant_start calls start_grant ------------------------------------------------------


def start_grant_callers(root: Path) -> list[str]:
    """Each call to ``.start_grant(...)`` or ``start_grant(...)`` under ``root/nextron``, as
    ``<posix path>:<enclosing function>``, and whether that function is a POST route."""
    found = []
    for path in sorted((root / "nextron").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        label = path.relative_to(root).as_posix()
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", None)
                )
                if name == START_GRANT and _innermost(function, node, tree):
                    post = any(_is_post_route(d) for d in function.decorator_list)
                    found.append(f"{label}:{function.name}:{'post' if post else 'not a post'}")
    return found


def _innermost(function: ast.AST, call: ast.Call, tree: ast.AST) -> bool:
    """Whether ``function`` is the innermost function around ``call``."""
    for inner in ast.walk(function):
        functions = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        if inner is function or not isinstance(inner, functions):
            continue
        if any(node is call for node in ast.walk(inner)):
            return False
    return True


def _is_post_route(decorator: ast.expr) -> bool:
    call = decorator.func if isinstance(decorator, ast.Call) else decorator
    return isinstance(call, ast.Attribute) and call.attr == "post"


def test_only_the_grant_post_starts_a_flow() -> None:
    assert start_grant_callers(ENGINE_ROOT) == [f"nextron/web/app.py:{STARTING_ROUTE}:post"]


PROBE = '''"""A probe web module for the start_grant check."""

from fastapi import FastAPI

app = FastAPI()
google = None


{body}
'''


@pytest.mark.parametrize(
    ("body", "caller"),
    [
        ('@app.get("/x")\ndef x() -> None:\n    google.start_grant("postmaster")', "x:not a post"),
        ("def helper() -> None:\n    google.start_grant('postmaster')", "helper:not a post"),
        (
            '@app.post("/y")\ndef y() -> None:\n    def inner() -> None:\n'
            "        start_grant('postmaster')\n    inner()",
            "inner:not a post",
        ),
    ],
)
def test_the_source_check_catches_another_caller(body: str, caller: str, tmp_path: Path) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.web", PROBE.format(body=body))

    callers = start_grant_callers(root)

    assert f"nextron/web/_lint_probe.py:{caller}" in callers
    assert f"nextron/web/app.py:{STARTING_ROUTE}:post" in callers


# The keyring names ------------------------------------------------------------------------------


def google_names() -> list[str]:
    names = [CLIENT_SECRET_NAME, CLIENT_META_NAME]
    for spec in GRANTS:
        names += [spec.secret_name, spec.meta_name]
    return names


def name_problems(names: list[str], taken: set[str]) -> list[str]:
    problems = [f"{name} is not a valid secret name" for name in names if not is_secret_name(name)]
    problems += [f"{name} collides with another entry" for name in names if name in taken]
    if len(set(names)) != len(names):
        problems.append("two Google names are the same")
    return problems


def taken_names() -> set[str]:
    taken = {OPEN_CODE_SECRET}
    for found in credential_registry.declared():
        taken |= {found.secret_name, found.meta_name}
    return taken


def test_the_google_keyring_names_are_valid_and_collide_with_nothing() -> None:
    assert google_names() == [
        "google/client_config",
        "meta/google/client_config",
        "google/internal_notify",
        "meta/google/internal_notify",
        "google/postmaster",
        "meta/google/postmaster",
    ]
    assert name_problems(google_names(), taken_names()) == []


def test_the_name_check_catches_a_collision_and_a_bad_name() -> None:
    assert name_problems(
        ["google/client_config", "hunter/api_key", "Google/Bad Name", "google/client_config"],
        taken_names(),
    ) == [
        "Google/Bad Name is not a valid secret name",
        "hunter/api_key collides with another entry",
        "two Google names are the same",
    ]
