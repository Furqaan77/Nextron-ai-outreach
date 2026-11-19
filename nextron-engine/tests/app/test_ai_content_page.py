"""The AI content view (FR-71, AD-13, UX-DR30; Story 4.4): ``GET /ai`` and ``POST /ai/check``
through ``Dashboard(units=units)`` on a temporary database at head. It checks the empty page,
the Sales Playbook block, each route's active tuple and candidates, the check's notices and
alerts, the ``InvalidPrompts`` alert with nothing stored, the 400 and 403 refusals, the
Settings link, that the navigation is unchanged, and ``Cache-Control: no-store``. The loader is
injected: the shipped prompt files with a synthetic Sales Playbook, never the real file.

Story 4.5 adds the AI test gate: the "Ungated" and "No test run yet" marks, a run's metrics
against their bars and its failing cases (escaped, inside ``hx-disable``), the error of a set
too small, the rollback form and ``POST /ai/rollback`` (its notice, a refusal's alert and the
400s), Check for changes queueing the runs, and the health panel's failed active tuple. Runs
use the dashboard's ``ScriptedLlm``."""

import dataclasses
import re
from collections.abc import Callable
from html import unescape
from types import MappingProxyType
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy import text as sql

from nextron import main
from nextron.adapters.ai import routes
from nextron.app.playbook.api import Playbook, PromptSync
from nextron.app.queries import playbook as playbook_queries
from nextron.app.setup.api import Setup
from nextron.db.models.base import new_id
from nextron.db.uow import UnitOfWorkError, UnitOfWorkFactory, current
from nextron.domain import seed_yaml
from nextron.domain.ai import Effort, RouteSpec
from nextron.domain.ai_test_cases import TestCase
from nextron.domain.playbook import (
    PlaybookProblem,
    PlaybookSnapshot,
    SyncTrigger,
    problem_text,
    read_playbook,
)
from nextron.domain.prompts import InvalidPrompts, PromptFile
from nextron.domain.segments import validate_segments
from nextron.ports.llm import LlmOk, LlmUsage
from nextron.web.ai_content_page import check_notice
from nextron.web.csrf import SECURITY_CHECK_FAILED
from nextron.web.forms import MALFORMED_FORM
from tests.asgi import Reply
from tests.dashboard import FORM, TEST_PLAYBOOK, Dashboard
from tests.fakes.clock import DEFAULT_NOW, FrozenClock

ROUTES = ("extract_people", "extract_signals", "signal_inbox_parse")


def extraction_prompts() -> tuple[Any, Any]:
    """The extraction routes' shipped prompts and defaults (Story 4.14's grounding routes are
    tested in ``tests/app/test_ai_grounding.py``)."""
    files, defaults = main.load_prompts()
    return (
        tuple(f for f in files if f.route in ROUTES),
        tuple(s for s in defaults if s.route in ROUTES),
    )
PROBE = "draft_probe"  # a Playbook route that only these tests define
MARKER = "PlaybookTextMarker"
EDITED = f"---\nversion: 0.3.0\n---\n\n{MARKER}: an edited Playbook.\n"
Loaded = tuple[tuple[PromptFile, ...], tuple[RouteSpec, ...], PlaybookSnapshot | PlaybookProblem]


def loader(playbook: PlaybookSnapshot | PlaybookProblem) -> Callable[[], Loaded]:
    def load() -> Loaded:
        files, defaults = extraction_prompts()
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
    files, defaults = extraction_prompts()
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
    files, defaults = extraction_prompts()
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
    files, defaults = extraction_prompts()
    probe_file = PromptFile(PROBE, "v1", "Draft one email for the Lead.")
    probe_spec = RouteSpec(PROBE, "claude-opus-5-5", Effort.HIGH, "v1", 1, uses_playbook=True)
    with units.open():
        Playbook(FrozenClock()).sync_prompts((*files, probe_file), (*defaults, probe_spec), first)
    # a tested activation pinned to the first snapshot, as Story 4.5's gate writes one
    run_id = new_id()
    with db_engine.connect() as connection:
        [(tuple_id,)] = connection.exec_driver_sql(
            "SELECT id FROM ai_route_tuples WHERE route = ?", (PROBE,)
        ).all()
        connection.exec_driver_sql(
            "INSERT INTO ai_test_runs (id, tuple_id, test_set, outcome, error, case_count, "
            "passed_count, metrics, results, cost_micros, started_at, finished_at) VALUES "
            "(?, ?, 'drafting_smoke', 'passed', NULL, 10, 10, '[]', '[]', 0, "
            "'2026-11-16T10:00:00.000000Z', '2026-11-16T10:00:00.000000Z')",
            (run_id, tuple_id),
        )
        connection.exec_driver_sql(
            "INSERT INTO ai_route_activations (id, route, model, effort, prompt_version, "
            "prompt_sha256, playbook_sha256, schema_version, actor, bootstrap, test_run_id, "
            "activated_at, deactivated_at) VALUES (?, ?, 'claude-opus-5-5', 'high', 'v1', ?, ?, "
            "1, 'operator', 0, ?, '2026-11-16T10:00:00.000000Z', NULL)",
            (new_id(), PROBE, probe_file.sha256, first.sha256, run_id),
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
        '<p class="notice" role="status">Checked for changes: 6 new candidate tuples. Stored '
        f"Sales Playbook version 9.9.9-test ({pinned.sha256[:12]}). Queued 6 test runs.</p>"
    ) in reply.text  # Story 4.5: each new tuple's paid test run (4.14: grounding; 4.15: drafting)
    assert 'role="alert"' not in reply.text
    assert counts(units) == (1, 6, 1, 3)  # the Playbook tuples are never bootstrap-activated
    assert "with Check for changes: read without a problem." in texts(reply.text)


def test_a_second_check_with_nothing_changed_says_so(units: UnitOfWorkFactory) -> None:
    dashboard = dashboard_on(units)
    check(dashboard)

    reply = check(dashboard)

    assert "Checked for changes: 0 new candidate tuples.</p>" in reply.text
    assert "Stored Sales Playbook" not in reply.text
    assert counts(units) == (1, 6, 2, 3)


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
    assert "Checked for changes: 3 new candidate tuples. Queued 3 test runs.</p>" in reply.text
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
        files, defaults = extraction_prompts()
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


# The AI test gate (Story 4.5) -------------------------------------------------------------------

PEOPLE_ANSWER_NAME = "<b>Priya</b> & Co"  # a model answer is text: always escaped


def gate_world(units: UnitOfWorkFactory, **changes: Any) -> Dashboard:
    """A first start's state (the Segments, the shipped prompts with ``TEST_PLAYBOOK`` and the
    extraction fixtures) and a dashboard on it."""
    segments = validate_segments(
        seed_yaml.parse((main.SEED_DIR / main.SEGMENTS_FILE_NAME).read_text(encoding="utf-8"))
    )
    with units.open():
        Setup(FrozenClock()).sync_segments(segments)
    startup_sync(units, read_playbook(TEST_PLAYBOOK.encode("utf-8")))
    with units.open():
        Playbook(FrozenClock()).sync_test_cases(main.load_test_cases())
    return dashboard_on(units, **changes)


def people_cases() -> list[TestCase]:
    return sorted(
        (case for case in main.load_test_cases() if case.route == "extract_people"),
        key=lambda case: case.case_key,
    )


def people_answers(*, wrong_name: bool = False) -> list[LlmOk]:
    answers = []
    for case in people_cases():
        people = [dict(person) for person in case.expected["people"]]
        if wrong_name and case.case_key == "law_people_team_pages":
            people[0]["name"] = PEOPLE_ANSWER_NAME  # two of seven people missed: under 80%
            people[1]["name"] = PEOPLE_ANSWER_NAME
        answers.append(
            LlmOk(
                {"people": people},
                model="claude-haiku-5-5",
                usage=LlmUsage(requests=1, cost_micros=1_000),
            )
        )
    return answers


def tuple_id_of(units: UnitOfWorkFactory, route: str, effort: str = "medium") -> str:
    with units.open(read_only=True) as unit:
        found = unit.session.execute(
            sql("SELECT id FROM ai_route_tuples WHERE route = :route AND effort = :effort"),
            {"route": route, "effort": effort},
        ).scalar_one()
    return str(found)


def run_people(dashboard: Dashboard, units: UnitOfWorkFactory, tuple_id: str | None = None) -> None:
    chosen = tuple_id or tuple_id_of(units, "extract_people")
    with units.open():
        dashboard.test_runs.run(chosen, "extraction")


def rollback(dashboard: Dashboard, body: str) -> Reply:
    cookie = dashboard.open_session()
    csrf = dashboard.csrf_token(cookie)
    return dashboard.post(
        "/ai/rollback",
        cookie=cookie,
        token=csrf,
        headers=[(b"content-type", FORM)],
        body=(f"csrf_token={csrf}" + (f"&{body}" if body else "")).encode("ascii"),
    )


def test_the_bootstrap_tuples_are_marked_ungated_with_no_test_run_yet(
    units: UnitOfWorkFactory,
) -> None:
    dashboard = gate_world(units)

    html = dashboard.get("/ai", cookie=dashboard.open_session()).text

    for route in ROUTES:
        section = route_section(html, route)
        assert "(bootstrap, by seed) Ungated: this tuple serves without a passing test run." in (
            section
        )
        assert "No test run yet" in section
    assert html.count('class="ai-mark ai-ungated"') == 3
    assert "Roll back to this tuple" not in html


def test_a_passing_run_shows_its_metrics_and_clears_the_ungated_mark(
    units: UnitOfWorkFactory,
) -> None:
    dashboard = gate_world(units)
    dashboard.llm.script.extend(people_answers())
    run_people(dashboard, units)

    html = dashboard.get("/ai", cookie=dashboard.open_session()).text

    section = route_section(html, "extract_people")
    assert "(tested, by system:playbook)" in section
    assert "Ungated" not in section
    assert (
        "Test run: Passed. Extraction fixtures, 2 of 2 cases passed, cost $0.0020, finished "
        "2026-11-16 09:00 UTC."
    ) in section
    assert (
        "Metric Value Bar Result "
        "Quotes that pass the substring check 100% (7 of 7) at least 100% Pass "
        "Labelled people found 100% (7 of 7) at least 80% Pass "
        "Forbidden Signal types proposed 0 at most 0 Pass "
        "Cases answered 100% (2 of 2) at least 100% Pass"
    ) in section
    assert "Failing cases" not in section
    # the bootstrap tuple it replaced is the same tuple: no candidate, so no rollback
    assert "Candidates (0)" in section


def test_a_failing_run_lists_its_failing_cases_escaped_inside_hx_disable(
    units: UnitOfWorkFactory,
) -> None:
    dashboard = gate_world(units)
    dashboard.llm.script.extend(people_answers(wrong_name=True))
    run_people(dashboard, units)

    html = dashboard.get("/ai", cookie=dashboard.open_session()).text

    section = route_section(html, "extract_people")
    assert "Ungated: this tuple serves without a passing test run." in section
    assert "Test run: Failed. Extraction fixtures, 1 of 2 cases passed" in section
    assert "Labelled people found 71% (5 of 7) at least 80% Fail" in section
    assert "Quotes that pass the substring check 100% (7 of 7) at least 100% Pass" in section
    match = re.search(r'<div class="ai-failing" hx-disable>(.*?)</div>', html, re.DOTALL)
    assert match is not None
    failing = texts(match.group(1))
    assert failing.startswith("Failing cases (1) Case id Expected Got law_people_team_pages ")
    assert "Expected Got law_people_team_pages People: Daniel Okafor, Margaret Ellison" in failing
    assert PEOPLE_ANSWER_NAME not in html  # escaped, never raw markup
    assert "&lt;b&gt;Priya&lt;/b&gt; &amp; Co" in html


def test_a_set_too_small_shows_its_error(units: UnitOfWorkFactory) -> None:
    dashboard = gate_world(units)
    with units.open():
        Playbook(FrozenClock()).sync_test_cases(
            [case for case in main.load_test_cases() if case.route != "extract_people"]
        )
    run_people(dashboard, units)

    html = dashboard.get("/ai", cookie=dashboard.open_session()).text
    section = route_section(html, "extract_people")

    assert "Test run: Failed (set too small). Extraction fixtures, 0 of 0 cases passed" in section
    assert dashboard.llm.calls == []


def newer_people_tuple(units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch) -> str:
    """A newer candidate of extract_people at effort high, which ``ScriptedLlm`` accepts."""
    [default] = routes.CANDIDATES["extract_people"]
    high = dataclasses.replace(default, effort=Effort.HIGH)
    changed = {**routes.CANDIDATES, "extract_people": (default, high)}
    monkeypatch.setattr(routes, "CANDIDATES", MappingProxyType(changed))
    files, defaults = extraction_prompts()
    high = tuple(
        dataclasses.replace(spec, effort=Effort.HIGH) if spec.route == "extract_people" else spec
        for spec in defaults
    )
    with units.open():
        Playbook(FrozenClock(DEFAULT_NOW.replace(hour=10))).sync_prompts(
            files, high, read_playbook(TEST_PLAYBOOK.encode("utf-8"))
        )
    return tuple_id_of(units, "extract_people", "high")


def test_a_passing_older_tuple_can_be_rolled_back_to(
    units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    dashboard = gate_world(units)
    old = tuple_id_of(units, "extract_people")
    dashboard.llm.script.extend(people_answers())
    run_people(dashboard, units, old)  # the bootstrap tuple passes: tested
    new = newer_people_tuple(units, monkeypatch)
    dashboard.llm.script.extend(people_answers())
    run_people(dashboard, units, new)  # the newest candidate passes: it serves
    html = dashboard.get("/ai", cookie=dashboard.open_session()).text
    section = route_section(html, "extract_people")
    assert "Effort high" in section.split("Candidates")[0]
    assert section.count("Roll back to this tuple") == 1
    assert f'<input type="hidden" name="tuple_id" value="{old}">' in html
    assert 'hx-post="/ai/rollback" hx-target="#ai-content-panel" hx-swap="outerHTML"' in html

    reply = rollback(dashboard, f"tuple_id={old}")

    assert reply.status == 200
    assert reply.text.startswith('<div id="ai-content-panel">')
    assert (
        f'<p class="notice" role="status">Rolled back extract_people to tuple {old}.</p>'
    ) in reply.text
    section = route_section(reply.text, "extract_people")
    assert "Effort medium" in section.split("Candidates")[0]
    assert "(tested, by operator)" in section
    with units.open(read_only=True):
        served = playbook_queries.active_tuple("extract_people")
    assert served is not None and served.effort is Effort.MEDIUM
    before = counts(units)

    again = rollback(dashboard, f"tuple_id={old}")  # it already serves: nothing changes

    assert (
        f'<p class="notice" role="status">extract_people already runs tuple {old}.</p>'
    ) in again.text
    assert "Rolled back" not in again.text
    assert counts(units) == before


def test_the_check_notice_counts_the_queued_test_runs() -> None:
    result = PromptSync((), (), (), (), (("extract_people", new_id()),), None)

    assert check_notice(result) == "Checked for changes: 1 new candidate tuple."
    assert check_notice(result, 0) == "Checked for changes: 1 new candidate tuple."
    assert check_notice(result, 1) == (
        "Checked for changes: 1 new candidate tuple. Queued 1 test run."
    )
    assert check_notice(result, 2).endswith(" Queued 2 test runs.")


def test_a_share_of_nothing_reads_none_and_passes_the_quote_bar(
    units: UnitOfWorkFactory,
) -> None:
    dashboard = gate_world(units)
    signals = [case for case in main.load_test_cases() if case.route == "extract_signals"]
    dashboard.llm.script.extend(
        LlmOk({"signals": []}, model="claude-haiku-5-5", usage=LlmUsage()) for _ in signals
    )
    tuple_id = tuple_id_of(units, "extract_signals")
    with units.open():
        dashboard.test_runs.run(tuple_id, "extraction")

    html = dashboard.get("/ai", cookie=dashboard.open_session()).text

    section = route_section(html, "extract_signals")
    assert "Quotes that pass the substring check none (0 of 0) at least 100% Pass" in section
    assert "Labelled Signals found 0% (0 of 9) at least 80% Fail" in section


def test_a_refused_rollback_answers_200_with_an_alert_and_changes_nothing(
    units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    dashboard = gate_world(units)
    untested = newer_people_tuple(units, monkeypatch)
    before = counts(units)

    reply = rollback(dashboard, f"tuple_id={untested}")

    assert reply.status == 200
    assert (
        '<p class="notice notice-refused" role="alert">No passing test run for this tuple</p>'
    ) in reply.text
    assert counts(units) == before
    reply = rollback(dashboard, f"tuple_id={new_id()}")
    assert '<p class="notice notice-refused" role="alert">No such tuple</p>' in reply.text


@pytest.mark.parametrize(
    "body",
    [
        "",
        "tuple_id=not-a-ulid",
        "tuple_id=01JD0000000000000000000099&tuple_id=01JD0000000000000000000098",
        "tuple_id=01JD0000000000000000000099&extra=1",
    ],
)
def test_a_malformed_rollback_is_refused_with_400(units: UnitOfWorkFactory, body: str) -> None:
    dashboard = gate_world(units)
    before = counts(units)

    reply = rollback(dashboard, body)

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert counts(units) == before


def test_check_for_changes_queues_a_run_of_each_tuple_without_one(
    units: UnitOfWorkFactory,
) -> None:
    dashboard = dashboard_on(units)

    check(dashboard)

    with units.open(read_only=True) as unit:
        keys = unit.session.execute(
            sql("SELECT idempotency_key FROM jobs WHERE type = 'run_ai_test_sets'")
        ).scalars().all()
    assert sorted(keys) == sorted(
        [f"run_ai_test_sets:{tuple_id_of(units, route)}:extraction" for route in ROUTES]
        + [  # Story 4.14: both grounding tuples run the Grounding set
            f"run_ai_test_sets:{tuple_id_of(units, route)}:grounding"
            for route in ("grounding_check", "grounding_check_fallback")
        ]
        # Story 4.15: the draft_sequence tuple runs the drafting smoke set
        + [f"run_ai_test_sets:{tuple_id_of(units, 'draft_sequence')}:drafting_smoke"]
    )
    check(dashboard)  # the live jobs absorb a second check
    with units.open(read_only=True) as unit:
        again = unit.session.execute(
            sql("SELECT COUNT(*) FROM jobs WHERE type = 'run_ai_test_sets'")
        ).scalar_one()
    assert again == 6


def test_the_health_panel_shows_a_failed_active_tuple(units: UnitOfWorkFactory) -> None:
    dashboard = gate_world(units)
    dashboard.llm.script.extend(people_answers(wrong_name=True))
    run_people(dashboard, units)

    panel = texts(dashboard.get("/health/panel", cookie=dashboard.open_session()).text)

    assert "extract_people: active tuple failed its test set" in panel


# Story 4.6: a run is bound to the case set it measured


def changed_people_cases() -> list[TestCase]:
    """The shipped cases with one extract_people fixture edited: a new extraction set."""
    cases = main.load_test_cases()
    [first] = [case for case in cases if case.case_key == "law_people_team_pages"]
    edited = dataclasses.replace(first, covers=(*first.covers, "edited"))
    return [edited if case is first else case for case in cases]


def test_a_run_on_an_earlier_case_set_is_marked_and_offers_no_rollback(
    units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    dashboard = gate_world(units)
    old = tuple_id_of(units, "extract_people")
    dashboard.llm.script.extend(people_answers())
    run_people(dashboard, units, old)
    new = newer_people_tuple(units, monkeypatch)
    dashboard.llm.script.extend(people_answers())
    run_people(dashboard, units, new)
    html = dashboard.get("/ai", cookie=dashboard.open_session()).text
    assert "measured an earlier version" not in html
    assert route_section(html, "extract_people").count("Roll back to this tuple") == 1

    with units.open():
        Playbook(FrozenClock(DEFAULT_NOW.replace(hour=11))).sync_test_cases(
            changed_people_cases()
        )
    html = dashboard.get("/ai", cookie=dashboard.open_session()).text
    section = route_section(html, "extract_people")

    stale = "This run measured an earlier version of the Extraction fixtures."
    assert section.count(stale) == 2  # the active tuple's run and the older candidate's
    assert f'<p class="ai-mark ai-run-stale">{stale}</p>' in html
    assert "Roll back to this tuple" not in section  # its pass was on the earlier set
    assert "measured an earlier version" not in route_section(html, "extract_signals")
    reply = rollback(dashboard, f"tuple_id={old}")
    assert '<p class="notice notice-refused" role="alert">No passing test run for this tuple</p>' in (
        reply.text
    )
