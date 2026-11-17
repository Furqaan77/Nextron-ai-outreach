"""The AI content view (FR-71, AD-13, UX-DR30; Story 4.4): ``GET /ai`` and ``POST /ai/check``
through ``Dashboard(units=units)`` on a temporary database at head. It checks the empty page,
the Sales Playbook block, each route's active tuple and candidates, the check's notices and
alerts, the ``InvalidPrompts`` alert with nothing stored, the 400 and 403 refusals, the
Settings link, that the navigation is unchanged, and ``Cache-Control: no-store``. The loader is
injected: the shipped prompt files with a synthetic Sales Playbook, never the real file."""

import dataclasses
import re
from collections.abc import Callable
from html import unescape
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy import text as sql

from nextron import main
from nextron.app.playbook.api import Playbook
from nextron.db.models.base import new_id
from nextron.db.uow import UnitOfWorkError, UnitOfWorkFactory, current
from nextron.domain.ai import Effort, RouteSpec
from nextron.domain.playbook import (
    PlaybookProblem,
    PlaybookSnapshot,
    SyncTrigger,
    problem_text,
    read_playbook,
)
from nextron.domain.prompts import InvalidPrompts, PromptFile
from nextron.web.csrf import SECURITY_CHECK_FAILED
from nextron.web.forms import MALFORMED_FORM
from tests.asgi import Reply
from tests.dashboard import FORM, TEST_PLAYBOOK, Dashboard
from tests.fakes.clock import DEFAULT_NOW, FrozenClock

ROUTES = ("extract_people", "extract_signals", "signal_inbox_parse")
PROBE = "draft_probe"  # a Playbook route that only these tests define
MARKER = "PlaybookTextMarker"
EDITED = f"---\nversion: 0.3.0\n---\n\n{MARKER}: an edited Playbook.\n"
Loaded = tuple[tuple[PromptFile, ...], tuple[RouteSpec, ...], PlaybookSnapshot | PlaybookProblem]


def loader(playbook: PlaybookSnapshot | PlaybookProblem) -> Callable[[], Loaded]:
    def load() -> Loaded:
        files, defaults = main.load_prompts()
        return files, defaults, playbook

    return load


def texts(html: str) -> str:
    """The page's text with its tags dropped and its whitespace squeezed."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def route_section(html: str, route: str) -> str:
    match = re.search(
        rf'<section class="ai-route" aria-labelledby="ai-route-{route}-heading">(.*?)</section>',
        html,
        re.DOTALL,
    )
    assert match is not None, route
    return texts(match.group(1))


def counts(units: UnitOfWorkFactory) -> tuple[int, int, int, int]:
    """How many Playbook snapshots, tuples, checks and activations are stored."""
    with units.open(read_only=True) as unit:
        session = unit.session
        return tuple(  # type: ignore[return-value]
            session.execute(sql(f"SELECT COUNT(*) FROM {table}")).scalar_one()  # noqa: S608
            for table in (
                "playbook_snapshots",
                "ai_route_tuples",
                "playbook_checks",
                "ai_route_activations",
            )
        )


def startup_sync(units: UnitOfWorkFactory, playbook: PlaybookSnapshot | PlaybookProblem) -> None:
    files, defaults = main.load_prompts()
    with units.open():
        Playbook(FrozenClock()).sync_prompts(files, defaults, playbook)


def dashboard_on(units: UnitOfWorkFactory, **changes: Any) -> Dashboard:
    return Dashboard(units=units, clock=FrozenClock(DEFAULT_NOW), **changes)


def check(dashboard: Dashboard, *, fields: str = "", token: bool = True) -> Reply:
    cookie = dashboard.open_session()
    csrf = dashboard.csrf_token(cookie)
    body = (f"csrf_token={csrf}" + (f"&{fields}" if fields else "")).encode("ascii")
    return dashboard.post(
        "/ai/check",
        cookie=cookie,
        token=csrf if token else None,
        headers=[(b"content-type", FORM)],
        body=body,
    )


# The page --------------------------------------------------------------------------------------


def test_the_page_renders_before_anything_is_recorded(units: UnitOfWorkFactory) -> None:
    dashboard = dashboard_on(units)
    cookie = dashboard.open_session()

    reply = dashboard.get("/ai", cookie=cookie)

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    text = texts(reply.text)
    assert "Current version No snapshot is stored yet." in text
    assert "Last check Not checked yet." in text
    assert "No route has a tuple yet." in text
    assert 'hx-post="/ai/check"' in reply.text


def test_the_page_shows_the_playbook_and_each_routes_active_tuple(
    units: UnitOfWorkFactory,
) -> None:
    pinned = read_playbook(TEST_PLAYBOOK.encode("utf-8"))
    startup_sync(units, pinned)
    dashboard = dashboard_on(units)

    html = dashboard.get("/ai", cookie=dashboard.open_session()).text

    text = texts(html)
    assert (
        f"Current version 9.9.9-test ({pinned.sha256[:12]}), captured 2026-11-16 09:00 UTC"
        in text
    )
    assert "Last check 2026-11-16 09:00 UTC, at engine start: read without a problem." in text
    for route in ROUTES:
        section = route_section(html, route)
        assert (
            "Active tuple Model claude-haiku-5-5 Effort medium Prompt version v1 Schema version 1 "
            "Sales Playbook No Playbook Activated 2026-11-16 09:00 UTC (bootstrap, by seed)"
        ) in section
        assert "Candidates (0) No candidates." in section
    assert MARKER not in html and "Test Playbook" not in html  # Playbook text is never shown


def test_a_candidate_is_listed_with_its_fields_and_created_time(
    units: UnitOfWorkFactory,
) -> None:
    startup_sync(units, read_playbook(TEST_PLAYBOOK.encode("utf-8")))
    files, defaults = main.load_prompts()
    changed = tuple(
        dataclasses.replace(s, effort=Effort.HIGH) if s.route == "extract_people" else s
        for s in defaults
    )
    with units.open():
        result = Playbook(FrozenClock(DEFAULT_NOW.replace(hour=11))).sync_prompts(
            files, changed, read_playbook(TEST_PLAYBOOK.encode("utf-8"))
        )
    [(_, tuple_id)] = result.candidates
    dashboard = dashboard_on(units)

    html = dashboard.get("/ai", cookie=dashboard.open_session()).text

    section = route_section(html, "extract_people")
    assert "Effort medium" in section  # the active tuple is unchanged
    assert (
        f"Candidates (1) Tuple id {tuple_id} Model claude-haiku-5-5 Effort high Prompt version "
        "v1 Schema version 1 Sales Playbook No Playbook Created 2026-11-16 11:00 UTC"
    ) in section
    assert section.count("Tuple id") == 1  # the active tuple shows no tuple id


def test_a_playbook_route_shows_its_tested_tuple_and_a_playbook_candidate(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    first = read_playbook(TEST_PLAYBOOK.encode("utf-8"))
    edited = read_playbook(EDITED.encode("utf-8"))
    files, defaults = main.load_prompts()
    probe_file = PromptFile(PROBE, "v1", "Draft one email for the Lead.")
    probe_spec = RouteSpec(PROBE, "claude-opus-5-5", Effort.HIGH, "v1", 1, uses_playbook=True)
    with units.open():
        Playbook(FrozenClock()).sync_prompts((*files, probe_file), (*defaults, probe_spec), first)
    # a tested activation pinned to the first snapshot, as Story 4.5's gate will write one
    with db_engine.connect() as connection:
        connection.exec_driver_sql(
            "INSERT INTO ai_route_activations (id, route, model, effort, prompt_version, "
            "prompt_sha256, playbook_sha256, schema_version, actor, bootstrap, test_run_id, "
            "activated_at, deactivated_at) VALUES (?, ?, 'claude-opus-5-5', 'high', 'v1', ?, ?, "
            "1, 'operator', 0, ?, '2026-11-16T10:00:00.000000Z', NULL)",
            (new_id(), PROBE, probe_file.sha256, first.sha256, new_id()),
        )
        connection.commit()
    with units.open():
        result = Playbook(FrozenClock(DEFAULT_NOW.replace(hour=11))).sync_prompts(
            (*files, probe_file), (*defaults, probe_spec), edited
        )
    [(route, tuple_id)] = result.candidates
    assert route == PROBE
    dashboard = dashboard_on(units)

    html = dashboard.get("/ai", cookie=dashboard.open_session()).text

    section = route_section(html, PROBE)
    assert (
        "Active tuple Model claude-opus-5-5 Effort high Prompt version v1 Schema version 1 "
        f"Sales Playbook version 9.9.9-test ({first.sha256[:12]}) "
        "Activated 2026-11-16 10:00 UTC (tested, by operator)"
    ) in section
    assert (
        f"Candidates (1) Tuple id {tuple_id} Model claude-opus-5-5 Effort high Prompt version v1 "
        f"Schema version 1 Sales Playbook version 0.3.0 ({edited.sha256[:12]}) "
        "Created 2026-11-16 11:00 UTC"
    ) in section
    assert MARKER not in html


def test_the_page_shows_the_last_checks_problem(units: UnitOfWorkFactory) -> None:
    startup_sync(units, PlaybookProblem.NO_VERSION)
    dashboard = dashboard_on(units)

    text = texts(dashboard.get("/ai", cookie=dashboard.open_session()).text)

    assert "Current version No snapshot is stored yet." in text
    assert (
        "Last check 2026-11-16 09:00 UTC, at engine start: "
        + problem_text(PlaybookProblem.NO_VERSION)
    ) in text


def test_the_settings_page_links_to_the_ai_content_page_and_the_nav_is_unchanged(
    units: UnitOfWorkFactory,
) -> None:
    dashboard = dashboard_on(units)
    cookie = dashboard.open_session()

    settings = dashboard.get("/settings", cookie=cookie).text
    page = dashboard.get("/ai", cookie=cookie).text

    assert (
        'AI prompts, the Sales Playbook and route tuples are on the <a href="/ai">AI content '
        "page</a>." in settings
    )
    nav = page[page.index('<nav aria-label="Sections">') : page.index("</nav>")]
    assert "/ai" not in nav and "AI content" not in nav


def test_the_page_without_a_session_is_locked(units: UnitOfWorkFactory) -> None:
    assert dashboard_on(units).get("/ai").status == 401


# Check for changes -----------------------------------------------------------------------------


def test_a_check_syncs_and_renders_the_panel_with_its_notice(units: UnitOfWorkFactory) -> None:
    pinned = read_playbook(TEST_PLAYBOOK.encode("utf-8"))
    dashboard = dashboard_on(units)  # its default loader: the shipped prompts, TEST_PLAYBOOK

    reply = check(dashboard)

    assert reply.status == 200
    assert reply.text.startswith('<div id="ai-content-panel">')
    assert "<html" not in reply.text
    assert (
        '<p class="notice" role="status">Checked for changes: 3 new candidate tuples. Stored '
        f"Sales Playbook version 9.9.9-test ({pinned.sha256[:12]}).</p>"
    ) in reply.text
    assert 'role="alert"' not in reply.text
    assert counts(units) == (1, 3, 1, 3)
    assert "with Check for changes: read without a problem." in texts(reply.text)


def test_a_second_check_with_nothing_changed_says_so(units: UnitOfWorkFactory) -> None:
    dashboard = dashboard_on(units)
    check(dashboard)

    reply = check(dashboard)

    assert "Checked for changes: 0 new candidate tuples.</p>" in reply.text
    assert "Stored Sales Playbook" not in reply.text
    assert counts(units) == (1, 3, 2, 3)


def test_a_check_of_an_edited_playbook_stores_it(units: UnitOfWorkFactory) -> None:
    startup_sync(units, read_playbook(TEST_PLAYBOOK.encode("utf-8")))
    edited = read_playbook(EDITED.encode("utf-8"))
    dashboard = dashboard_on(units, ai_content=loader(edited))

    reply = check(dashboard)

    assert (
        "Checked for changes: 0 new candidate tuples. Stored Sales Playbook version 0.3.0 "
        f"({edited.sha256[:12]})."
    ) in reply.text  # no shipped route uses the Playbook, so no candidate
    assert counts(units) == (2, 3, 2, 3)
    assert f"Current version 0.3.0 ({edited.sha256[:12]})" in texts(reply.text)
    assert MARKER not in reply.text


def test_a_check_whose_read_failed_shows_the_problem(units: UnitOfWorkFactory) -> None:
    dashboard = dashboard_on(units, ai_content=loader(PlaybookProblem.BAD_VERSION))

    reply = check(dashboard)

    assert reply.status == 200
    assert "Checked for changes: 3 new candidate tuples.</p>" in reply.text
    alert = (
        '<p class="notice notice-refused" role="alert">Sales Playbook: '
        f"{problem_text(PlaybookProblem.BAD_VERSION)}</p>"
    )
    assert alert in unescape(reply.text)
    assert counts(units) == (0, 3, 1, 3)


def test_a_check_records_the_operator_as_its_actor(units: UnitOfWorkFactory) -> None:
    check(dashboard_on(units))

    with units.open(read_only=True) as unit:
        rows = unit.session.execute(
            sql("SELECT DISTINCT cause, actor FROM events WHERE entity = 'ai_route_tuples'")
        ).all()
        [trigger] = unit.session.execute(sql('SELECT "trigger" FROM playbook_checks')).scalars()
    assert [tuple(row) for row in rows] == [("operator_check", "operator")]
    assert trigger == SyncTrigger.OPERATOR_CHECK.value


def test_the_loader_runs_outside_any_unit_of_work(units: UnitOfWorkFactory) -> None:
    seen: list[bool] = []

    def load() -> Loaded:
        try:
            current()
        except UnitOfWorkError:
            seen.append(True)
        return loader(read_playbook(TEST_PLAYBOOK.encode("utf-8")))()

    assert check(dashboard_on(units, ai_content=load)).status == 200
    assert seen == [True]


def test_refused_prompts_answer_200_with_an_alert_and_store_nothing(
    units: UnitOfWorkFactory,
) -> None:
    def refused() -> Loaded:
        raise InvalidPrompts("extract_people/v1.md: the file's SHA-256 isn't the one locked")

    reply = check(dashboard_on(units, ai_content=refused))

    assert reply.status == 200
    assert (
        '<p class="notice notice-refused" role="alert">Nothing was stored: '
        "extract_people/v1.md: the file&#39;s SHA-256 isn&#39;t the one locked</p>"
    ) in reply.text
    assert 'role="status"' not in reply.text
    assert counts(units) == (0, 0, 0, 0)


def test_a_rewritten_prompt_found_in_the_sync_stores_nothing(units: UnitOfWorkFactory) -> None:
    startup_sync(units, read_playbook(TEST_PLAYBOOK.encode("utf-8")))

    def rewritten() -> Loaded:
        files, defaults = main.load_prompts()
        changed = tuple(
            PromptFile(f.route, f.version, f.text + "\nEdited.") if f.route == "extract_people"
            else f
            for f in files
        )
        return changed, defaults, read_playbook(EDITED.encode("utf-8"))

    reply = check(dashboard_on(units, ai_content=rewritten))

    assert reply.status == 200
    assert "Nothing was stored: prompt extract_people v1 is stored with another SHA-256" in (
        unescape(reply.text)
    )
    assert counts(units) == (1, 3, 1, 3)  # the edited Playbook wasn't stored either


@pytest.mark.parametrize("fields", ["extra=1", "trigger=startup", "playbook=0.9.9"])
def test_a_check_with_another_field_is_refused_with_400(
    units: UnitOfWorkFactory, fields: str
) -> None:  # Row: Probe POST
    seen: list[bool] = []

    def load() -> Loaded:
        seen.append(True)
        return loader(read_playbook(TEST_PLAYBOOK.encode("utf-8")))()

    reply = check(dashboard_on(units, ai_content=load), fields=fields)

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert seen == []  # refused before the loader ran
    assert counts(units) == (0, 0, 0, 0)


def test_a_check_without_the_csrf_token_is_refused_with_403(units: UnitOfWorkFactory) -> None:
    dashboard = dashboard_on(units)
    cookie = dashboard.open_session()

    reply = dashboard.post(
        "/ai/check", cookie=cookie, headers=[(b"content-type", FORM)], body=b""
    )

    assert (reply.status, reply.text) == (403, SECURITY_CHECK_FAILED)
    assert counts(units) == (0, 0, 0, 0)


# The health panel ------------------------------------------------------------------------------


def test_the_health_panel_shows_a_playbook_problem_until_a_good_read(
    units: UnitOfWorkFactory,
) -> None:
    startup_sync(units, PlaybookProblem.NOT_UTF8)
    dashboard = dashboard_on(units)
    cookie = dashboard.open_session()

    panel = unescape(dashboard.get("/health/panel/live", cookie=cookie).text)

    assert "Sales Playbook: " + problem_text(PlaybookProblem.NOT_UTF8) in panel
    assert (
        "Fix the file, then use Check for changes on the AI content page (/ai) or restart the "
        "engine." in panel
    )
    check(dashboard)  # a good read: TEST_PLAYBOOK
    after = unescape(dashboard.get("/health/panel/live", cookie=cookie).text)
    assert "Sales Playbook: " not in after
