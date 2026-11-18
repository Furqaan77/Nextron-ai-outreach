"""The Quotas and cost page (``GET /quotas``, UX-DR28, Story 1.13) through ``Dashboard(units=
units)`` on a temporary database at head, its usage read from the dashboard's
``InMemoryUsageMeter``: the panel's lines in order, Hunter in credits with halves, the warning
and the stopped state, the empty state, limits of 0 shown as off, the Settings link, the
read-only unit of work, ``no-store``, and the locked page without a session."""

from contextlib import AbstractContextManager
from datetime import UTC, datetime
from html.parser import HTMLParser

import pytest

from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.budget import AiCall, ApiCall
from nextron.domain.settings_schema import Key
from tests.dashboard import Dashboard
from tests.fakes.clock import FrozenClock
from tests.fakes.units import FakeUnits

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
NEVER_BLOCKED = "Sending, reply handling and unsubscribe polling are never blocked."
QUOTA_HEADINGS = ["Quota", "Used", "Monthly quota", "State"]
USAGE_HEADINGS = ["Provider", "Operation", "SKU", "Calls", "Failed", "Units"]


class Page(HTMLParser):
    """The page's main text, in order, and each table's header and body rows."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.texts: list[str] = []
        self.tables: list[list[list[str]]] = []
        self.links: list[tuple[str | None, str]] = []
        self._in_main = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._href: str | None = None
        self._link_text: list[str] | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "main":
            self._in_main = True
        elif tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "main":
            self._in_main = False
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.tables[-1].append(self._row)
            self._row = None
        elif tag == "a" and self._link_text is not None:
            self.links.append((self._href, "".join(self._link_text).strip()))
            self._link_text = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        if self._link_text is not None:
            self._link_text.append(data)
        if self._in_main and data.strip():
            self.texts.append(" ".join(data.split()))


@pytest.fixture
def dashboard(units: UnitOfWorkFactory) -> Dashboard:
    return Dashboard(units=units, clock=FrozenClock(T))


@pytest.fixture
def cookie(dashboard: Dashboard) -> bytes:
    return dashboard.open_session()


def quotas(dashboard: Dashboard, cookie: bytes) -> Page:
    reply = dashboard.get("/quotas", cookie=cookie)
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    return Page(reply.text)


def spend(dashboard: Dashboard, micros: int) -> None:
    assert dashboard.meter is not None
    dashboard.meter.record(
        AiCall(
            "draft_email",
            "draft_email:v1:claude-opus-5-5",
            "claude-opus-5-5",
            input_tokens=900,
            output_tokens=120,
            cost_micros=micros,
        )
    )


def test_the_panel_shows_credits_with_halves_the_warning_and_the_stopped_state(
    dashboard: Dashboard, cookie: bytes
) -> None:
    meter = dashboard.meter
    assert meter is not None
    meter.record(ApiCall("hunter", "verify", ok=True, count=3))  # 3 units
    meter.record(ApiCall("hunter", "find", ok=False, error_code="hunter_unavailable"))  # 2
    meter.record(ApiCall("places", "text_search", ok=True, sku="enterprise", count=1_000))
    spend(dashboard, 16_050_000)

    page = quotas(dashboard, cookie)

    assert page.texts[:9] == [
        "Quotas and cost",
        NEVER_BLOCKED,
        "Non-essential work stopped: Places Enterprise quota reached",
        "Month (UTC): 2026-11",
        "AI spend",
        "$16.05 of $20",
        "AI calls this month: 1",
        "Warning: AI spend has reached 80% of the $20 budget.",
        "Quotas",
    ]
    quota_table, usage_table = page.tables
    assert quota_table == [
        QUOTA_HEADINGS,
        ["Hunter", "2.5 credits", "50 credits", "OK"],
        ["Prospeo", "0 credits", "100 credits", "OK"],
        ["Places Enterprise", "1,000 requests", "1,000 requests", "Reached: discovery stopped"],
        ["Places Pro", "0 requests", "0 requests", "Off (quota 0)"],
        ["Job aggregator", "0 requests", "0 requests", "Off (quota 0)"],
    ]
    assert usage_table == [
        USAGE_HEADINGS,
        ["hunter", "find", "-", "1", "1", "1 credits"],  # Hunter's units in credits, named
        ["hunter", "verify", "-", "1", "0", "1.5 credits"],
        ["places", "text_search", "enterprise", "1", "0", "1,000"],  # others: plain units
    ]


def test_with_nothing_recorded_the_panel_says_so_and_work_is_running(
    dashboard: Dashboard, cookie: bytes
) -> None:
    page = quotas(dashboard, cookie)

    assert NEVER_BLOCKED in page.texts
    assert "Non-essential work is running." in page.texts
    assert "$0 of $20" in page.texts
    assert "No external calls recorded this month." in page.texts
    assert not [text for text in page.texts if text.startswith("Warning")]
    [quota_table] = page.tables  # no usage table
    assert [row[3] for row in quota_table[1:]] == [
        "OK",
        "OK",
        "OK",
        "Off (quota 0)",
        "Off (quota 0)",
    ]


def test_a_reached_ai_budget_stops_work_and_a_zero_budget_shows_off(
    dashboard: Dashboard, cookie: bytes
) -> None:
    spend(dashboard, 20_000_000)

    page = quotas(dashboard, cookie)

    assert "Non-essential work stopped: AI budget reached" in page.texts
    assert "AI budget reached: discovery, enrichment and re-checks stopped until next month." in (
        page.texts
    )

    with dashboard.units.open():
        dashboard.setup.set(Key.AI_BUDGET_MONTHLY, 0)
    page = quotas(dashboard, cookie)

    assert "$20 of $0" in page.texts
    assert "Off (budget $0): discovery, enrichment and re-checks don't run." in page.texts
    assert "Non-essential work is running." in page.texts  # off is quiet, not "stopped"


def test_a_changed_quota_shows_on_the_panel(dashboard: Dashboard, cookie: bytes) -> None:
    with dashboard.units.open():
        dashboard.setup.set(Key.QUOTA_HUNTER_CREDITS, 1_500)

    [quota_table] = quotas(dashboard, cookie).tables

    assert quota_table[1] == ["Hunter", "0 credits", "1,500 credits", "OK"]


def test_the_page_links_back_to_settings_and_settings_links_to_it(
    dashboard: Dashboard, cookie: bytes
) -> None:
    page = quotas(dashboard, cookie)
    settings = dashboard.get("/settings", cookie=cookie)

    assert ("/settings", "Settings page") in page.links
    assert settings.status == 200
    assert (
        'Quotas and AI spend are on the <a href="/quotas">Quotas and cost page</a>.'
        in settings.text
    )
    nav = [href for href, _ in page.links if href in ("/quotas",)]
    assert nav == []  # the navigation's sections are fixed: no Quotas entry


def test_the_page_reads_in_one_read_only_unit_of_work(units: UnitOfWorkFactory) -> None:
    recording = RecordingUnits(units)
    dashboard = Dashboard(units=recording, clock=FrozenClock(T))
    cookie = dashboard.open_session()
    recording.opened.clear()

    assert dashboard.get("/quotas", cookie=cookie).status == 200

    assert recording.opened == [True]


class RecordingUnits:
    """Real units of work that record whether each one opened was read-only."""

    def __init__(self, inner: UnitOfWorkFactory) -> None:
        self.inner = inner
        self.opened: list[bool] = []

    def open(self, *, read_only: bool = False) -> AbstractContextManager[object]:
        self.opened.append(read_only)
        return self.inner.open(read_only=read_only)


def test_without_a_session_the_page_is_locked() -> None:
    dashboard = Dashboard(units=FakeUnits())

    reply = dashboard.get("/quotas")

    assert reply.status == 401
    assert "nextron open" in reply.text
    assert "Quotas" not in reply.text


# The unsubscribe Worker's KV list calls (Story 3.12) --------------------------------------------


def kv_calls(dashboard: Dashboard, count: int, at: datetime) -> None:
    assert dashboard.meter is not None
    clock = dashboard.meter.clock
    dashboard.meter.clock = FrozenClock(at)
    for _ in range(count):
        dashboard.meter.record(ApiCall("unsub_worker", "kv_list", ok=True))
    dashboard.meter.clock = clock


def kv_line(dashboard: Dashboard, cookie: bytes) -> tuple[str, bool]:
    reply = dashboard.get("/quotas", cookie=cookie)
    page = Page(reply.text)
    [line] = [text for text in page.texts if text.startswith("Unsubscribe Worker KV list")]
    return line, '<p class="kv-list quota-warning">' in reply.text


def test_the_page_counts_today_s_kv_list_calls_and_warns_at_80_percent(
    dashboard: Dashboard, cookie: bytes
) -> None:
    assert kv_line(dashboard, cookie) == (
        "Unsubscribe Worker KV list calls today: 0 of 1,000",
        False,
    )
    kv_calls(dashboard, 300, datetime(2026, 11, 16, 23, 59, tzinfo=UTC))  # yesterday (UTC)
    kv_calls(dashboard, 799, T)
    assert dashboard.meter is not None
    dashboard.meter.record(ApiCall("hunter", "find", ok=True))  # another provider

    assert kv_line(dashboard, cookie) == (
        "Unsubscribe Worker KV list calls today: 799 of 1,000",
        False,
    )
    kv_calls(dashboard, 1, T)
    assert kv_line(dashboard, cookie) == (
        "Unsubscribe Worker KV list calls today: 800 of 1,000",
        True,
    )
