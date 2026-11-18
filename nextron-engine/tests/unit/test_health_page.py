"""The health panel (``GET /health/panel``, ``GET /health/panel/live``, ``POST
/health/jobs/dismiss``, ``POST /health/backup`` and ``POST /health/restore-test``, FR-58,
UX-DR29, Stories 1.15 to 1.18)
through ``Dashboard(units=units)`` on a temporary database at head: the page, the fragment and
their exact ``hx-*`` attributes, each section's text in order, the Dismiss form and its
refusals, the Backups section (each last-backup line, each off-PC label and the failed
class, the "Back up now" form and its POST: created, absorbed, an extra field, no CSRF token),
the Restore test section (each last-test line, the failed class, the "Run restore test now" form
and its POST, with its notice), the Recovery section (Story 1.19: none before a restore; on, with
its steps table and the on class; ended, with "Last restore: ..."; after the Restore test
section and before the errors),
the notification area's error count
(singular, plural and absent at 0), ``no-store``, the navigation link, the guard's
``HX-Refresh`` on an HTMX GET only, and a hostile ``last_error`` rendered as escaped text."""

import json
import re
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest

from nextron.app.ops.api import DeadJobAlerts, Heartbeat, OffpcOutcome, Recovery, TakenBackup
from nextron.db.repos.ops.snapshot import Snapshot
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.alert import Channel
from nextron.domain.backup import BackupKind, LedgerStatus, OffpcStatus, copy_name
from nextron.domain.budget import AiCall
from nextron.domain.job import Lane
from nextron.domain.readiness import CheckResult, Status
from nextron.domain.recovery import RecoveryStep
from nextron.domain.restore_test import RestoreTestKind
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
BACKUP = "/health/backup"
RESTORE = "/health/restore-test"
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


def csrf_input(token: str) -> dict[str, str | None]:
    return {"type": "hidden", "name": "csrf_token", "value": token}


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
BACKUP_FORM = {
    "method": "post",
    "hx-post": "/health/backup",
    "hx-target": "#health-panel",
    "hx-swap": "outerHTML",
}
BACKUP_BUTTON = {"type": "submit", "class": "secondary", "id": "backup-now"}
RESTORE_FORM = {
    "method": "post",
    "hx-post": "/health/restore-test",
    "hx-target": "#health-panel",
    "hx-swap": "outerHTML",
}
RESTORE_BUTTON = {"type": "submit", "class": "secondary", "id": "restore-test-now"}
NEVER_RUN = [
    [
        ["Job type", "Last success"],
        ["enrich_lead", "never"],
        ["canonicalize_lead", "never"],
        ["render_pages", "never"],
        ["extract_lead", "never"],
        ["select_primary_contact", "never"],
        ["find_contact_email", "never"],
    ],
    [
        ["Job type", "Last success"],
        ["backup_db", "never"],
        ["restore_test", "never"],
        ["check_recovery", "never"],
    ],
    [
        ["Job type", "Last success"],
        ["send_alert", "never"],
        ["resolve_contact_markets", "never"],  # Story 3.2's one-off, group Other
        ["purge_page_text", "never"],
    ],
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
        "Backups",
        "No backup yet.",
        "Back up now",
        "Restore test",
        "No restore test yet.",
        "Run restore test now",
        "Errors needing attention (0)",
        "No errors need attention.",
        "Last successful runs",
        "Enrichment",
        "Backup",
        "Other",
        "Readiness (nextron doctor)",
        "13 ok, 0 warn, 0 fail",
    ]
    assert panel.tables == [*NEVER_RUN, READINESS_TABLE]
    assert panel.forms == [
        (BACKUP_FORM, [csrf_input(session[1])]),
        (RESTORE_FORM, [csrf_input(session[1])]),
    ]
    assert panel.buttons == [BACKUP_BUTTON, RESTORE_BUTTON]
    assert panel.heading_ids == [
        "health-worker-heading",
        "health-backup-heading",
        "health-restore-heading",
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
        "Backups",
        "No backup yet.",
        "Back up now",
        "Restore test",
        "No restore test yet.",
        "Run restore test now",
        "Errors needing attention (1)",
        f"Job dead: send_alert {dead}, attempts: 1",
        "Last error: ValueError: broken",
        "Dismiss",
        "Last successful runs",
        "Enrichment",
        "Backup",
        "Other",
        "Readiness (nextron doctor)",
        "13 ok, 0 warn, 0 fail",
    ]
    assert panel.tables[:3] == [
        NEVER_RUN[0],
        [
            ["Job type", "Last success"],
            ["backup_db", "never"],
            ["restore_test", "never"],
            ["check_recovery", "never"],
        ],
        [
            ["Job type", "Last success"],
            ["send_alert", "2026-11-17 09:05:00 UTC"],
            ["resolve_contact_markets", "never"],
            ["purge_page_text", "never"],
        ],
    ]
    backup_form, restore_form, (attrs, inputs) = panel.forms
    assert backup_form == (BACKUP_FORM, [csrf_input(session[1])])
    assert restore_form == (RESTORE_FORM, [csrf_input(session[1])])
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
        BACKUP_BUTTON,
        RESTORE_BUTTON,
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

    assert panel.texts[9:11] == [
        "Errors needing attention (1)",
        "AI spend at 80% of $20: $16 spent in 2026-11",
    ]
    assert [attrs for attrs, _ in panel.forms] == [BACKUP_FORM, RESTORE_FORM]  # no Dismiss


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
    assert reply.text.count('hx-post="') == 3  # only the three forms' own
    assert "mac@example.com" not in reply.text  # redacted when read
    assert panel.texts[11] == (
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
    assert panel.texts[9:11] == ["Errors needing attention (0)", "No errors need attention."]


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
    assert Panel(reply.text).texts[9] == "Errors needing attention (0)"


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


# Backups (Story 1.16) ---------------------------------------------------------------------------


def record_backup(
    dashboard: Dashboard,
    units: UnitOfWorkFactory,
    kind: BackupKind,
    *,
    verified: bool,
    started_at: datetime = T,
) -> str:
    """Record a backup row through the dashboard's ``LocalBackups``, without copying."""
    path = dashboard.local_dir / copy_name("test", started_at)
    made = Snapshot(path, 4096, "a" * 64, "0007_1_16_backups", None, None) if verified else None
    error = None if verified else "OSError: disk full"
    taken = TakenBackup(kind, path, started_at, started_at, made, error, None if verified else "x")

    @use_case
    def record() -> str:
        with current().transaction():
            return dashboard.local.insert(taken)

    with units.open():
        return record()


def backup_line(reply: Reply) -> tuple[str, list[str]]:
    """The Backups line's text and its classes."""
    found = re.search(r'<p class="(health-backup[^"]*)">([^<]*)</p>', reply.text)
    assert found is not None
    return found.group(2), found.group(1).split()


@pytest.mark.parametrize(
    ("kind", "verified", "line", "failed"),
    [
        (BackupKind.DAILY, True, "verified (daily)", False),
        (BackupKind.MANUAL, False, "failed (manual)", True),
        (BackupKind.PRE_MIGRATE, True, "verified (before a migration)", False),
    ],
)
def test_the_backups_section_shows_the_last_backup(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    units: UnitOfWorkFactory,
    kind: BackupKind,
    verified: bool,
    line: str,
    failed: bool,
) -> None:
    record_backup(dashboard, units, BackupKind.DAILY, verified=True, started_at=T - timedelta(1))
    record_backup(dashboard, units, kind, verified=verified, started_at=T + timedelta(seconds=7))

    reply = get(dashboard, LIVE, session[0])

    text, classes = backup_line(reply)
    assert text == f"Last backup: 2026-11-17 09:00:07 UTC, {line}"
    assert classes == (["health-backup", "health-backup-failed"] if failed else ["health-backup"])
    assert Panel(reply.text).texts[3:6] == ["Backups", text, "Back up now"]


def test_no_backup_yet(dashboard: Dashboard, session: tuple[bytes, str]) -> None:
    reply = get(dashboard, LIVE, session[0])

    assert backup_line(reply) == ("No backup yet.", ["health-backup"])
    assert 'class="health-backup-notice"' not in reply.text


def test_the_back_up_now_form_posts_only_its_csrf_token_through_htmx(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = get(dashboard, PAGE, session[0])

    assert (
        '<form method="post" hx-post="/health/backup" hx-target="#health-panel" '
        'hx-swap="outerHTML">'
    ) in reply.text
    assert '<button type="submit" class="secondary" id="backup-now">Back up now</button>' in (
        reply.text
    )
    panel = Panel(reply.text)
    assert panel.forms[0] == (BACKUP_FORM, [csrf_input(session[1])])
    assert panel.buttons[0] == BACKUP_BUTTON


def a_failed_backup_shows_in_the_error_list(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    backup_id = record_backup(dashboard, units, BackupKind.MANUAL, verified=False)

    texts = panel_of(dashboard, session[0]).texts

    assert texts[9:12] == [
        "Errors needing attention (1)",
        f"Backup failed: manual {backup_id}",
        "Error: OSError: disk full",
    ]


def test_a_failed_backup_shows_in_the_error_list_until_a_later_one_verifies(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    a_failed_backup_shows_in_the_error_list(dashboard, session, units)

    record_backup(
        dashboard, units, BackupKind.DAILY, verified=True, started_at=T + timedelta(minutes=1)
    )

    assert panel_of(dashboard, session[0]).texts[9:11] == [
        "Errors needing attention (0)",
        "No errors need attention.",
    ]


# The off-PC copy on the Backups line (Story 1.17) ---------------------------------------------


def record_offpc(
    dashboard: Dashboard, units: UnitOfWorkFactory, backup_id: str, outcome: OffpcOutcome
) -> None:
    """Record ``outcome`` on the backup's row through the dashboard's ``OffpcBackups``."""

    @use_case
    def record() -> int:
        with current().transaction():
            return dashboard.offpc.record(backup_id, outcome)

    with units.open():
        assert record() == 1


@pytest.mark.parametrize(
    ("outcome", "suffix", "failed"),
    [
        (
            OffpcOutcome(OffpcStatus.COPIED, name="x.db.enc", ledger=LedgerStatus.ABSENT),
            "copied off the PC",
            False,
        ),
        (
            OffpcOutcome(OffpcStatus.FAILED, error="backup_target_unavailable: unreachable"),
            "off-PC copy failed",
            True,
        ),
        (
            OffpcOutcome(OffpcStatus.NOT_CONFIGURED, reason="folder_not_set"),
            "off-PC not configured",
            False,
        ),
    ],
    ids=["copied", "failed", "not configured"],
)
def test_the_backups_line_shows_the_off_pc_outcome(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    units: UnitOfWorkFactory,
    outcome: OffpcOutcome,
    suffix: str,
    failed: bool,
) -> None:
    backup_id = record_backup(dashboard, units, BackupKind.DAILY, verified=True)
    record_offpc(dashboard, units, backup_id, outcome)

    reply = get(dashboard, LIVE, session[0])

    text, classes = backup_line(reply)
    assert text == f"Last backup: 2026-11-17 09:00:00 UTC, verified (daily), {suffix}"
    assert classes == (["health-backup", "health-backup-failed"] if failed else ["health-backup"])
    issues = Panel(reply.text).texts[9:12]
    if failed:
        assert issues == [
            "Errors needing attention (1)",
            f"Off-PC backup copy failed: daily {backup_id}",
            "Error: backup_target_unavailable: unreachable",
        ]
    else:
        assert issues[:2] == ["Errors needing attention (0)", "No errors need attention."]


def test_a_backup_with_no_off_pc_outcome_shows_no_label(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    record_backup(dashboard, units, BackupKind.PRE_MIGRATE, verified=True)

    text, classes = backup_line(get(dashboard, LIVE, session[0]))

    assert text == "Last backup: 2026-11-17 09:00:00 UTC, verified (before a migration)"
    assert classes == ["health-backup"]


def back_up_now(dashboard: Dashboard, session: tuple[bytes, str], body: bytes) -> Reply:
    cookie, token = session
    return dashboard.post(
        BACKUP, cookie=cookie, token=token, headers=[(b"content-type", FORM)], body=body
    )


def backup_jobs(units: UnitOfWorkFactory) -> list[tuple[str, str, str]]:
    database = units.engine.url.database
    assert database
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        return connection.execute(
            "SELECT idempotency_key, payload, status FROM jobs WHERE type = 'backup_db'"
        ).fetchall()
    finally:
        connection.close()


def notice(reply: Reply) -> list[str]:
    return re.findall(r'<p class="health-backup-notice" role="status">([^<]*)</p>', reply.text)


def test_back_up_now_queues_a_manual_backup_and_says_so(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    reply = back_up_now(dashboard, session, form(csrf_token=session[1]))

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    assert Panel(reply.text).attrs == PANEL_ATTRS
    assert notice(reply) == ["Backup queued; its result shows here when it finishes."]
    [(key, payload, status)] = backup_jobs(units)
    assert (key, json.loads(payload), status) == (
        "backup_db:global:2026-11-17",
        {"v": 1, "kind": "manual"},
        "queued",
    )
    assert list(dashboard.local_dir.parent.parent.parent.glob("**/*.db")) == []  # no copy yet


def test_a_second_click_while_one_is_queued_is_absorbed(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    back_up_now(dashboard, session, b"")

    reply = back_up_now(dashboard, session, b"")

    assert reply.status == 200
    assert notice(reply) == ["A backup is already queued or running."]
    assert len(backup_jobs(units)) == 1
    assert notice(get(dashboard, LIVE, session[0])) == []  # the notice is the POST's alone


@pytest.mark.parametrize(
    "body",
    [form(extra="1"), form(job=SUBJECTS[0]), b"kind=daily", b"x=%FF"],
    ids=["extra field", "a job", "a kind", "bad body"],
)
def test_a_malformed_back_up_now_is_refused_with_400_and_queues_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory, body: bytes
) -> None:
    reply = back_up_now(dashboard, session, body)

    assert (reply.status, reply.body) == (400, MALFORMED)
    assert backup_jobs(units) == []


def test_back_up_now_without_the_csrf_token_is_refused_with_403(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    reply = dashboard.post(BACKUP, cookie=session[0], headers=[(b"content-type", FORM)], body=b"")

    assert reply.status == 403
    assert backup_jobs(units) == []


def test_back_up_now_needs_a_session(dashboard: Dashboard, units: UnitOfWorkFactory) -> None:
    reply = dashboard.post(BACKUP, headers=[(b"content-type", FORM)], body=b"")

    assert reply.status == 401
    assert backup_jobs(units) == []


# The Restore test section (Story 1.18) -----------------------------------------------------------

RESTORE_PASS = ("pass", "pass", "pass", "skipped", "pass")


def record_restore_test(
    dashboard: Dashboard,
    units: UnitOfWorkFactory,
    test_id: str,
    kind: RestoreTestKind,
    *,
    started_at: datetime = T,
    checks: tuple[str, ...] = RESTORE_PASS,
    reason: str | None = None,
    detail: str | None = None,
) -> None:
    """A ``restore_tests`` row, as the ``ops`` service writes one."""
    database = units.engine.url.database
    assert database
    result = "pass" if reason is None else "fail"
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute(
            "INSERT INTO restore_tests (id, kind, result, started_at, finished_at, backup_id, "
            "check_fetch, check_integrity, check_revision, check_suppression, check_provenance, "
            "reason, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                test_id,
                str(kind),
                result,
                f"{started_at:%Y-%m-%dT%H:%M:%S}.000000Z",
                f"{started_at:%Y-%m-%dT%H:%M:%S}.000000Z",
                SUBJECTS[19] if result == "pass" else None,
                *checks,
                reason,
                detail,
            ),
        )
    finally:
        connection.close()


def restore_line(reply: Reply) -> tuple[str, list[str]]:
    """The Restore test line's text and its classes."""
    found = re.search(r'<p class="(health-restore(?: [^"]*)?)">([^<]*)</p>', reply.text)
    assert found is not None
    return found.group(2), found.group(1).split()


def test_no_restore_test_yet(dashboard: Dashboard, session: tuple[bytes, str]) -> None:
    reply = get(dashboard, LIVE, session[0])

    assert restore_line(reply) == ("No restore test yet.", ["health-restore"])
    assert 'class="health-restore-notice"' not in reply.text


@pytest.mark.parametrize(
    ("kind", "checks", "reason", "line", "failed"),
    [
        (RestoreTestKind.MONTHLY, RESTORE_PASS, None, "pass (monthly)", False),
        (RestoreTestKind.MANUAL, ("pass",) * 5, None, "pass (manual)", False),
        (
            RestoreTestKind.MANUAL,
            ("pass", "pass", "fail", "pass", "pass"),
            "revision_behind",
            "fail (manual): revision_behind; failing checks: Alembic revision",
            True,
        ),
        (
            RestoreTestKind.MONTHLY,
            ("fail", "skipped", "skipped", "skipped", "skipped"),
            "no_offpc_copy",
            "fail (monthly): no_offpc_copy; failing checks: fetch and decrypt",
            True,
        ),
        (
            RestoreTestKind.MONTHLY,
            ("pass", "pass", "fail", "fail", "fail"),
            "revision_unknown",
            "fail (monthly): revision_unknown; failing checks: Alembic revision, "
            "Suppression List count, Provenance Records count",
            True,
        ),
        (
            RestoreTestKind.MANUAL,
            ("pass", "fail", "skipped", "skipped", "skipped"),
            "integrity_failed",
            "fail (manual): integrity_failed; failing checks: integrity check",
            True,
        ),
    ],
    ids=["monthly pass", "manual pass", "revision", "no copy", "three checks", "integrity"],
)
def test_the_restore_test_section_shows_the_last_test(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    units: UnitOfWorkFactory,
    kind: RestoreTestKind,
    checks: tuple[str, ...],
    reason: str | None,
    line: str,
    failed: bool,
) -> None:
    record_restore_test(dashboard, units, SUBJECTS[0], RestoreTestKind.MONTHLY,
                        started_at=T - timedelta(days=1))
    record_restore_test(dashboard, units, SUBJECTS[1], kind, started_at=T + timedelta(seconds=7),
                        checks=checks, reason=reason)

    reply = get(dashboard, LIVE, session[0])

    text, classes = restore_line(reply)
    assert text == f"Last restore test: 2026-11-17 09:00:07 UTC, {line}"
    assert classes == (
        ["health-restore", "health-restore-failed"] if failed else ["health-restore"]
    )
    texts = Panel(reply.text).texts
    assert texts[6:9] == ["Restore test", text, "Run restore test now"]
    if failed:
        assert texts[9:12] == [
            "Errors needing attention (1)",
            f"Restore test failed: {kind} {SUBJECTS[1]}, {reason}",
            f"Error: {reason}",
        ]


def test_a_failed_test_lists_its_redacted_detail_until_a_later_one_passes(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    record_restore_test(
        dashboard,
        units,
        SUBJECTS[0],
        RestoreTestKind.MONTHLY,
        checks=("pass", "pass", "pass", "fail", "skipped"),
        reason="suppression_fewer",
        detail="suppression_entries: copy 1, backup 5 <b>mac@example.com</b>",
    )

    reply = get(dashboard, LIVE, session[0])

    assert Panel(reply.text).texts[9:12] == [
        "Errors needing attention (1)",
        f"Restore test failed: monthly {SUBJECTS[0]}, suppression_fewer",
        "Error: suppression_entries: copy 1, backup 5 <b><email></b>",
    ]
    assert "<b>" not in reply.text and "mac@example.com" not in reply.text

    record_restore_test(dashboard, units, SUBJECTS[1], RestoreTestKind.MANUAL,
                        started_at=T + timedelta(minutes=1))

    assert panel_of(dashboard, session[0]).texts[9:11] == [
        "Errors needing attention (0)",
        "No errors need attention.",
    ]


def test_the_run_restore_test_now_form_posts_only_its_csrf_token_through_htmx(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = get(dashboard, PAGE, session[0])

    assert (
        '<form method="post" hx-post="/health/restore-test" hx-target="#health-panel" '
        'hx-swap="outerHTML">'
    ) in reply.text
    assert (
        '<button type="submit" class="secondary" id="restore-test-now">Run restore test now'
        "</button>"
    ) in reply.text
    panel = Panel(reply.text)
    assert panel.forms[1] == (RESTORE_FORM, [csrf_input(session[1])])
    assert panel.buttons[1] == RESTORE_BUTTON


def run_restore_test_now(
    dashboard: Dashboard, session: tuple[bytes, str], body: bytes
) -> Reply:
    cookie, token = session
    return dashboard.post(
        RESTORE, cookie=cookie, token=token, headers=[(b"content-type", FORM)], body=body
    )


def restore_jobs(units: UnitOfWorkFactory) -> list[tuple[str, str, str]]:
    database = units.engine.url.database
    assert database
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        return connection.execute(
            "SELECT idempotency_key, payload, status FROM jobs WHERE type = 'restore_test'"
        ).fetchall()
    finally:
        connection.close()


def restore_notice(reply: Reply) -> list[str]:
    return re.findall(r'<p class="health-restore-notice" role="status">([^<]*)</p>', reply.text)


def test_run_restore_test_now_queues_a_manual_test_and_says_so(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    reply = run_restore_test_now(dashboard, session, form(csrf_token=session[1]))

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    assert Panel(reply.text).attrs == PANEL_ATTRS
    assert restore_notice(reply) == [
        "Restore test queued; its result shows here when it finishes."
    ]
    assert notice(reply) == []  # the backup notice is "Back up now"'s alone
    [(key, payload, status)] = restore_jobs(units)
    assert (key, json.loads(payload), status) == (
        "restore_test:global:2026-11-17",
        {"v": 1, "kind": "manual"},
        "queued",
    )
    assert not (dashboard.local_dir.parent / "restore_test").exists()  # nothing tested yet


def test_a_second_restore_click_while_one_is_queued_is_absorbed(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    run_restore_test_now(dashboard, session, b"")

    reply = run_restore_test_now(dashboard, session, b"")

    assert reply.status == 200
    assert restore_notice(reply) == ["A restore test is already queued or running."]
    assert len(restore_jobs(units)) == 1
    assert restore_notice(get(dashboard, LIVE, session[0])) == []  # the POST's notice alone


@pytest.mark.parametrize(
    "body",
    [form(extra="1"), form(kind="monthly"), form(job=SUBJECTS[0]), b"x=%FF"],
    ids=["extra field", "a kind", "a job", "bad body"],
)
def test_a_malformed_run_restore_test_now_is_refused_with_400_and_queues_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory, body: bytes
) -> None:
    reply = run_restore_test_now(dashboard, session, body)

    assert (reply.status, reply.body) == (400, MALFORMED)
    assert restore_jobs(units) == []


def test_run_restore_test_now_without_the_csrf_token_is_refused_with_403(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    reply = dashboard.post(
        RESTORE, cookie=session[0], headers=[(b"content-type", FORM)], body=b""
    )

    assert reply.status == 403
    assert restore_jobs(units) == []


def test_run_restore_test_now_needs_a_session(
    dashboard: Dashboard, units: UnitOfWorkFactory
) -> None:
    reply = dashboard.post(RESTORE, headers=[(b"content-type", FORM)], body=b"")

    assert reply.status == 401
    assert restore_jobs(units) == []


# The Recovery section (Story 1.19) ---------------------------------------------------------------

RESTORED_NAME = "nextron-test-20261116T080000.000Z.db.enc"


def turn_recovery_on(units: UnitOfWorkFactory, clock: FrozenClock) -> str:
    with units.open():
        row = Recovery(clock).begin(
            backup_at=T - timedelta(days=1, hours=1),
            source_name=RESTORED_NAME,
            pre_restore_name="nextron-test-20261117T090000.000Z.db",
        )
    return row.id


def recovery_line(reply: Reply) -> tuple[str, list[str]]:
    """The Recovery line's text and its classes."""
    found = re.search(r'<p class="(health-recovery(?: [^"]*)?)">([^<]*)</p>', reply.text)
    assert found is not None
    return found.group(2), found.group(1).split()


def test_no_recovery_section_before_a_restore(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = get(dashboard, LIVE, session[0])

    assert "health-recovery" not in reply.text
    assert "Recovery" not in Panel(reply.text).texts


def test_while_recovery_mode_is_on_the_section_lists_the_steps(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    turn_recovery_on(units, FrozenClock(T))
    with units.open():  # repoll registered, the other three not applicable
        Recovery(FrozenClock(T)).pending(frozenset({RecoveryStep.REPOLL_UNSUBSCRIBES}))

    reply = get(dashboard, LIVE, session[0])

    assert recovery_line(reply) == (
        "Recovery mode is on since 2026-11-17 09:00:00 UTC: sending is held. Restored from "
        f"{RESTORED_NAME}, backed up at 2026-11-16 08:00:00 UTC.",
        ["health-recovery", "health-recovery-on"],
    )
    panel = Panel(reply.text)
    assert panel.tables[0] == [
        ["Step", "Status"],
        ["Reapply the erasure ledger", "not applicable"],
        ["Mark unfinished sends unknown", "not applicable"],
        ["Re-ingest the mailboxes", "not applicable"],
        ["Re-poll the unsubscribe Worker", "pending"],
    ]
    texts = panel.texts
    assert texts.index("Run restore test now") < texts.index("Recovery")
    assert texts.index("Recovery") < texts.index("Errors needing attention (0)")
    assert panel.heading_ids == [
        "health-worker-heading",
        "health-backup-heading",
        "health-restore-heading",
        "health-recovery-heading",
        "health-errors-heading",
        "health-runs-heading",
        "health-readiness-heading",
    ]


def test_after_recovery_mode_ends_the_section_shows_the_last_restore(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    turn_recovery_on(units, FrozenClock(T))
    later = Recovery(FrozenClock(T + timedelta(minutes=3)))
    with units.open():
        later.pending(frozenset())
    with units.open():
        assert later.finish() is False

    reply = get(dashboard, PAGE, session[0])

    assert recovery_line(reply) == (
        f"Last restore: 2026-11-17 09:00:00 UTC, from {RESTORED_NAME}; recovery mode ended at "
        "2026-11-17 09:03:00 UTC.",
        ["health-recovery"],
    )
    cells = [cell for table in Panel(reply.text).tables for row in table for cell in row]
    assert "Step" not in cells


def test_a_hostile_source_name_renders_as_escaped_text(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    with units.open():
        Recovery(FrozenClock(T)).begin(
            backup_at=T, source_name="<img src=x onerror=alert(1)>.db", pre_restore_name=None
        )

    reply = get(dashboard, LIVE, session[0])

    assert "<img src=x" not in reply.text
    assert "&lt;img src=x onerror=alert(1)&gt;.db" in reply.text
