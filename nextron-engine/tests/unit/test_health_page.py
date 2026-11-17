"""The health panel (``GET /health/panel``, ``GET /health/panel/live`` and ``POST
/health/jobs/dismiss``, FR-58, UX-DR29, Story 1.15) through ``Dashboard(units=units)`` on a
temporary database at head: the page, the fragment and their exact ``hx-*`` attributes, each
section's text in order, the Dismiss form and its refusals, the notification area's error count
(singular, plural and absent at 0), ``no-store``, the navigation link, the guard's
``HX-Refresh`` on an HTMX GET only, and a hostile ``last_error`` rendered as escaped text."""

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest

from nextron.app.ops.api import DeadJobAlerts, Heartbeat
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.alert import Channel
from nextron.domain.budget import AiCall
from nextron.domain.job import Lane
from nextron.domain.readiness import CheckResult, Status
from nextron.web.guard import LOCKED_TEXT
from tests.asgi import DEFAULT_HOST, Reply, request
from tests.dashboard import FORM, READINESS, Dashboard, form
from tests.fakes.clock import FrozenClock

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
SUBJECTS = [f"01JD3QZ8X5V9K2M7N4P6R8T0{n:02d}" for n in range(10, 30)]
UNKNOWN_JOB = "01JD3QZ8X5V9K2M7N4P6R8T0ZZ"
PAGE = "/health/panel"
LIVE = "/health/panel/live"
DISMISS = "/health/jobs/dismiss"
MALFORMED = b"Request refused: malformed form"
PANEL_ATTRS = {
    "id": "health-panel",
    "hx-get": "/health/panel/live",
    "hx-trigger": "every 30s",
    "hx-swap": "outerHTML",
}
HOSTILE = (
    'ValueError: <script>alert(1)</script><img src=x onerror="alert(2)"> '
    'hx-post="/health/jobs/dismiss" for mac@example.com'
)


class Panel(HTMLParser):
    """The ``#health-panel`` div: its attributes, its texts in order, its tables (rows of
    cells), its forms (attributes and inputs) and its buttons' attributes."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.attrs: dict[str, str | None] | None = None
        self.panels = 0
        self.texts: list[str] = []
        self.tables: list[list[list[str]]] = []
        self.forms: list[tuple[dict[str, str | None], list[dict[str, str | None]]]] = []
        self.buttons: list[dict[str, str | None]] = []
        self.heading_ids: list[str | None] = []
        self._depth = 0  # div depth inside the panel; 0 outside it
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "div" and found.get("id") == "health-panel":
            self.panels += 1
            self.attrs = found
            self._depth = 1
            return
        if not self._depth:
            return
        if tag == "div":
            self._depth += 1
        elif tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "form":
            self.forms.append((found, []))
        elif tag == "input" and self.forms:
            self.forms[-1][1].append(found)
        elif tag == "button":
            self.buttons.append(found)
        elif tag == "h2":
            self.heading_ids.append(found.get("id"))

    def handle_endtag(self, tag: str) -> None:
        if not self._depth:
            return
        if tag == "div":
            self._depth -= 1
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if not self._depth:
            return
        if self._cell is not None:
            self._cell.append(data)
        elif data.strip():
            self.texts.append(" ".join(data.split()))


class Texts(HTMLParser):
    """Every non-blank text of a fragment, and its links as ``(href, text)``."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.texts: list[str] = []
        self.links: list[tuple[str | None, str]] = []
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self._href: str | None = None
        self._link: list[str] | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        if tag == "a":
            self._href, self._link = dict(attrs).get("href"), []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._link is not None:
            self.links.append((self._href, "".join(self._link).strip()))
            self._link = None

    def handle_data(self, data: str) -> None:
        if self._link is not None:
            self._link.append(data)
        if data.strip():
            self.texts.append(" ".join(data.split()))


@pytest.fixture
def dashboard(units: UnitOfWorkFactory) -> Dashboard:
    return Dashboard(units=units, clock=FrozenClock(T))


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


class Jobs:
    """Makes ``send_alert`` jobs through the dashboard's queue, one alert subject each."""

    def __init__(self, dashboard: Dashboard, units: UnitOfWorkFactory) -> None:
        self.dashboard = dashboard
        self.units = units
        self.used = 0

    def enqueue(self) -> str:
        subject = SUBJECTS[self.used]
        self.used += 1
        queue = self.dashboard.queue

        @use_case
        def caller() -> str:
            with current().transaction():
                return queue.enqueue(
                    "send_alert",
                    scope=subject,
                    discriminator="0",
                    payload={"v": 1, "alert_id": subject},
                ).job_id

        with self.units.open():
            return caller()

    def run(self, *, succeed: bool = True, error: Exception | None = None) -> str:
        job_id = self.enqueue()
        with self.units.open():
            claim = self.dashboard.queue.claim(Lane.REALTIME)
        assert claim is not None and claim.job.id == job_id
        with self.units.open():
            if succeed:
                self.dashboard.queue.complete(claim)
            else:
                self.dashboard.queue.fail(claim, error or ValueError("broken"))
        return job_id

    def last_error(self, job_id: str, text: str | None) -> None:
        """Store ``text`` as the job's ``last_error`` as it is, past the service's own
        redaction, as a stand-in for any free text that reached the column."""
        database = self.units.engine.url.database
        assert database
        connection = sqlite3.connect(database, isolation_level=None)
        try:
            connection.execute("UPDATE jobs SET last_error = ? WHERE id = ?", (text, job_id))
        finally:
            connection.close()

    def ids(self) -> list[str]:
        database = self.units.engine.url.database
        assert database
        connection = sqlite3.connect(database, isolation_level=None)
        try:
            return [row[0] for row in connection.execute("SELECT id FROM jobs ORDER BY id")]
        finally:
            connection.close()


@pytest.fixture
def jobs(dashboard: Dashboard, units: UnitOfWorkFactory) -> Jobs:
    return Jobs(dashboard, units)


def beat(dashboard: Dashboard, units: UnitOfWorkFactory, tmp_path: Path) -> None:
    with units.open():
        Heartbeat(dashboard.clock, tmp_path / "heartbeat").beat()


def get(dashboard: Dashboard, path: str, cookie: bytes) -> Reply:
    reply = dashboard.get(path, cookie=cookie)
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    assert reply.header(b"content-type") == b"text/html; charset=utf-8"
    return reply


def panel_of(dashboard: Dashboard, cookie: bytes, path: str = LIVE) -> Panel:
    found = Panel(get(dashboard, path, cookie).text)
    assert found.panels == 1
    return found


def dismiss(dashboard: Dashboard, session: tuple[bytes, str], body: bytes) -> Reply:
    cookie, token = session
    return dashboard.post(
        DISMISS, cookie=cookie, token=token, headers=[(b"content-type", FORM)], body=body
    )


READINESS_TABLE = [["Status", "Check", "Message"]] + [
    ["ok", result.id, result.message] for result in READINESS
]


# The page and the fragment ------------------------------------------------------------------


def test_the_page_puts_the_polled_panel_in_the_layout(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = get(dashboard, PAGE, session[0])
    page = Texts(reply.text)

    assert "<title>Nextron: Health</title>" in reply.text
    assert [attrs for tag, attrs in page.tags if tag == "h1"] == [{}]
    assert "Engine health" in page.texts
    assert Panel(reply.text).attrs == PANEL_ATTRS
    assert reply.text.count('hx-trigger="every 30s"') == 1


def test_the_fragment_is_the_panel_alone(dashboard: Dashboard, session: tuple[bytes, str]) -> None:
    reply = get(dashboard, LIVE, session[0])

    assert reply.text.lstrip().startswith('<div id="health-panel" hx-get="/health/panel/live" ')
    assert reply.text.rstrip().endswith("</div>")
    assert "<html" not in reply.text and "<nav" not in reply.text and "<main" not in reply.text
    assert Panel(reply.text).attrs == PANEL_ATTRS


def test_a_fresh_engine_shows_each_section_in_order(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    panel = panel_of(dashboard, session[0])

    assert panel.texts == [
        "Checked at 2026-11-17 09:00:00 UTC; refreshes every 30 seconds.",
        "Worker",
        "Not running: no heartbeat yet",
        "Errors needing attention (0)",
        "No errors need attention.",
        "Last successful runs",
        "Other",
        "Readiness (nextron doctor)",
        "11 ok, 0 warn, 0 fail",
    ]
    assert panel.tables == [
        [["Job type", "Last success"], ["send_alert", "never"]],
        READINESS_TABLE,
    ]
    assert panel.forms == []
    assert panel.heading_ids == [
        "health-worker-heading",
        "health-errors-heading",
        "health-runs-heading",
        "health-readiness-heading",
    ]


def test_a_running_worker_its_last_success_and_a_dead_job_with_its_dismiss_form(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    jobs: Jobs,
    units: UnitOfWorkFactory,
    tmp_path: Path,
) -> None:
    jobs.run()
    dashboard.clock.advance(timedelta(minutes=5))
    jobs.run()  # the latest success
    dead = jobs.run(succeed=False)
    beat(dashboard, units, tmp_path)
    dashboard.clock.advance(timedelta(seconds=119))

    panel = panel_of(dashboard, session[0])

    assert panel.texts == [
        "Checked at 2026-11-17 09:06:59 UTC; refreshes every 30 seconds.",
        "Worker",
        "Running (last beat 2026-11-17 09:05:00 UTC)",
        "Errors needing attention (1)",
        f"Job dead: send_alert {dead}, attempts: 1",
        "Last error: ValueError: broken",
        "Dismiss",
        "Last successful runs",
        "Other",
        "Readiness (nextron doctor)",
        "11 ok, 0 warn, 0 fail",
    ]
    assert panel.tables[0] == [
        ["Job type", "Last success"],
        ["send_alert", "2026-11-17 09:05:00 UTC"],
    ]
    [(attrs, inputs)] = panel.forms
    assert attrs == {
        "method": "post",
        "hx-post": "/health/jobs/dismiss",
        "hx-target": "#health-panel",
        "hx-swap": "outerHTML",
    }
    assert inputs == [
        {"type": "hidden", "name": "csrf_token", "value": session[1]},
        {"type": "hidden", "name": "job", "value": dead},
    ]
    # an id, so HTMX keeps a keyboard user's focus across the 30 s swap, and a distinct label
    assert panel.buttons == [
        {
            "type": "submit",
            "class": "secondary",
            "id": f"dismiss-{dead}",
            "aria-label": f"Dismiss dead job {dead}",
        }
    ]


def test_a_stale_heartbeat_is_not_running(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    beat(dashboard, units, tmp_path)
    dashboard.clock.advance(timedelta(seconds=120))

    panel = panel_of(dashboard, session[0])

    assert panel.texts[2] == "Not running: last beat 2026-11-17 09:00:00 UTC"


def test_budget_issues_are_listed_without_a_dismiss_form(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    assert dashboard.meter is not None
    dashboard.meter.record(
        AiCall("draft_email", "draft:v1:m", "m", input_tokens=1, output_tokens=1,
               cost_micros=16_000_000)
    )

    panel = panel_of(dashboard, session[0])

    assert panel.texts[3:5] == [
        "Errors needing attention (1)",
        "AI spend at 80% of $20: $16 spent in 2026-11",
    ]
    assert panel.forms == []


def test_the_readiness_list_counts_warnings_and_failures(
    units: UnitOfWorkFactory,
) -> None:
    results: Sequence[CheckResult] = (
        *READINESS[:9],
        CheckResult("required_settings", Status.FAIL, "required settings not set: <x>"),
        CheckResult("database_revision", Status.WARN, "will migrate on next start"),
    )
    dashboard = Dashboard(units=units, clock=FrozenClock(T), readiness=lambda: results)
    cookie = dashboard.open_session()

    panel = panel_of(dashboard, cookie)

    assert panel.tables[-1][-2:] == [
        ["fail", "required_settings", "required settings not set: <x>"],
        ["warn", "database_revision", "will migrate on next start"],
    ]
    assert panel.texts[-1] == "9 ok, 1 warn, 1 fail"


def test_a_hostile_last_error_renders_as_escaped_text(
    dashboard: Dashboard, session: tuple[bytes, str], jobs: Jobs
) -> None:
    dead = jobs.run(succeed=False)
    jobs.last_error(dead, HOSTILE)

    reply = get(dashboard, LIVE, session[0])
    panel = Panel(reply.text)

    assert "<script>" not in reply.text and "<img" not in reply.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in reply.text
    assert reply.text.count('hx-post="') == 1  # only the Dismiss form's own attribute
    assert "mac@example.com" not in reply.text  # redacted when read
    assert panel.texts[5] == (
        'Last error: ValueError: <script>alert(1)</script><img src=x onerror="alert(2)"> '
        'hx-post="/health/jobs/dismiss" for <email>'
    )


# Dismiss ----------------------------------------------------------------------------------------


def test_dismiss_deletes_the_dead_job_and_answers_with_the_panel(
    dashboard: Dashboard, session: tuple[bytes, str], jobs: Jobs
) -> None:
    done = jobs.run()
    dead = jobs.run(succeed=False)

    reply = dismiss(dashboard, session, form(job=dead))

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    assert dead not in jobs.ids() and done in jobs.ids()
    panel = Panel(reply.text)
    assert panel.attrs == PANEL_ATTRS
    assert panel.texts[3:5] == ["Errors needing attention (0)", "No errors need attention."]


@pytest.mark.parametrize("which", ["queued", "done", "unknown"])
def test_dismissing_anything_but_a_dead_job_changes_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], jobs: Jobs, which: str
) -> None:
    done = jobs.run()
    dead = jobs.run(succeed=False)
    queued = jobs.enqueue()
    target = {"queued": queued, "done": done, "unknown": UNKNOWN_JOB}[which]
    before = jobs.ids()

    reply = dismiss(dashboard, session, form(job=target))

    assert reply.status == 200
    assert jobs.ids() == before
    assert f"Job dead: send_alert {dead}, attempts: 1" in Panel(reply.text).texts


def test_dismissing_twice_is_a_no_op_the_second_time(
    dashboard: Dashboard, session: tuple[bytes, str], jobs: Jobs
) -> None:
    dead = jobs.run(succeed=False)

    assert dismiss(dashboard, session, form(job=dead)).status == 200
    reply = dismiss(dashboard, session, form(job=dead))

    assert reply.status == 200
    assert Panel(reply.text).texts[3] == "Errors needing attention (0)"


@pytest.mark.parametrize(
    "body",
    [
        b"",
        form(job="not-a-ulid"),
        form(job=SUBJECTS[0].lower()),
        form(job=""),
        form(job=SUBJECTS[0], extra="1"),
        form(notification=SUBJECTS[0]),
        b"job=" + SUBJECTS[0].encode() + b"&job=" + SUBJECTS[1].encode(),
        b"job=%FF",
    ],
    ids=["no field", "not a ulid", "lowercase", "blank", "extra field", "wrong name",
         "repeated", "bad body"],
)
def test_a_malformed_dismiss_is_refused_with_400_and_changes_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], jobs: Jobs, body: bytes
) -> None:
    jobs.run(succeed=False)
    before = jobs.ids()

    reply = dismiss(dashboard, session, body)

    assert (reply.status, reply.body) == (400, MALFORMED)
    assert jobs.ids() == before


def test_a_dismiss_without_the_csrf_token_is_refused_with_403(
    dashboard: Dashboard, session: tuple[bytes, str], jobs: Jobs
) -> None:
    dead = jobs.run(succeed=False)

    reply = dashboard.post(
        DISMISS, cookie=session[0], headers=[(b"content-type", FORM)], body=form(job=dead)
    )

    assert reply.status == 403
    assert dead in jobs.ids()


# The notification area's error count ------------------------------------------------------------


def area(dashboard: Dashboard, cookie: bytes) -> Reply:
    return get(dashboard, "/notifications", cookie)


def test_the_area_shows_no_count_when_nothing_needs_attention(
    dashboard: Dashboard, session: tuple[bytes, str], jobs: Jobs
) -> None:
    jobs.run()  # a success is no error

    assert area(dashboard, session[0]).body == b""


def test_the_area_shows_one_error_in_the_singular(
    dashboard: Dashboard, session: tuple[bytes, str], jobs: Jobs
) -> None:
    jobs.run(succeed=False)

    reply = area(dashboard, session[0])

    assert reply.text.startswith('<p class="health-count"><a href="/health/panel">')
    assert Texts(reply.text).links == [("/health/panel", "Engine health: 1 error needs attention")]


def test_the_area_shows_several_errors_in_the_plural_before_the_notifications(
    dashboard: Dashboard, session: tuple[bytes, str], jobs: Jobs, units: UnitOfWorkFactory
) -> None:
    jobs.run(succeed=False)
    jobs.run(succeed=False)

    @use_case
    def raise_one() -> None:
        with current().transaction():
            dashboard.notify.alert("job_dead", SUBJECTS[-1], "A notice", [Channel.DASHBOARD])

    with units.open():
        raise_one()

    texts = Texts(area(dashboard, session[0]).text)

    assert texts.texts[:2] == ["Engine health: 2 errors need attention", "Notifications (1)"]
    assert texts.links == [("/health/panel", "Engine health: 2 errors need attention")]


def test_dismissing_a_notification_shows_the_count_again(
    dashboard: Dashboard, session: tuple[bytes, str], jobs: Jobs, units: UnitOfWorkFactory
) -> None:
    dead = jobs.run(succeed=False)
    with units.open():
        DeadJobAlerts(dashboard.notify).alert_dead_jobs()  # its job_dead notice
    [notification] = [
        attrs["value"]
        for tag, attrs in Texts(area(dashboard, session[0]).text).tags
        if tag == "input" and attrs.get("name") == "notification"
    ]
    assert notification is not None
    cookie, token = session

    reply = dashboard.post(
        "/notifications/dismiss",
        cookie=cookie,
        token=token,
        headers=[(b"content-type", FORM)],
        body=form(notification=notification),
    )

    assert reply.status == 200
    assert Texts(reply.text).texts == ["Engine health: 1 error needs attention"]
    assert dead in jobs.ids()  # the notification's Dismiss leaves the dead job alone


# The navigation, the session and the guard -----------------------------------------------------


def test_the_nav_links_the_health_panel(dashboard: Dashboard, session: tuple[bytes, str]) -> None:
    links = Texts(get(dashboard, "/", session[0]).text).links

    assert ("/health/panel", "Health") in links


@pytest.mark.parametrize("path", [PAGE, LIVE])
def test_the_panel_needs_a_session(dashboard: Dashboard, path: str) -> None:
    reply = dashboard.get(path)

    assert reply.status == 401
    assert b"<h1>Dashboard locked</h1>" in reply.body
    assert reply.header(b"hx-refresh") is None
    assert b"health-panel" not in reply.body


@pytest.mark.parametrize("path", [LIVE, PAGE, "/notifications"])
def test_an_htmx_get_without_a_session_reloads_the_page(dashboard: Dashboard, path: str) -> None:
    reply = request(
        dashboard.app, path, headers=[(b"host", DEFAULT_HOST), (b"hx-request", b"true")]
    )

    assert reply.status == 401
    assert reply.header(b"content-type") == b"text/plain; charset=utf-8"
    assert reply.body == LOCKED_TEXT.encode()
    assert reply.header(b"hx-refresh") == b"true"
    assert reply.header(b"cache-control") == b"no-store"


def test_an_htmx_post_without_a_session_keeps_the_page(dashboard: Dashboard) -> None:
    reply = dashboard.post(
        DISMISS,
        headers=[(b"hx-request", b"true"), (b"content-type", FORM)],
        body=form(job=SUBJECTS[0]),
    )

    assert (reply.status, reply.body) == (401, LOCKED_TEXT.encode())
    assert reply.header(b"hx-refresh") is None


def test_the_panel_reads_in_one_read_only_unit_of_work(
    dashboard: Dashboard, session: tuple[bytes, str], jobs: Jobs
) -> None:
    dead = jobs.run(succeed=False)
    before = jobs.ids()

    for path in (PAGE, LIVE):
        get(dashboard, path, session[0])

    assert jobs.ids() == before and dead in before
