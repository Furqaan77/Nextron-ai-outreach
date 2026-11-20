"""Story 4.10's "Ready to draft" page (``GET /leads/ready``, ``POST /leads/ready/pick``) and the
notification area's signal shortfall line (and Story 4.16's "not drafted" lines), through
``Dashboard(units=...)`` on the world of ``tests.app.test_enroll``: the header texts, the rows
and badges, the pick form, the result table, the 400s of a malformed form, the Leads list's link
and the area line."""

from html import escape
from urllib.parse import urlencode

import pytest
from sqlalchemy import Engine

from nextron.db.models.base import new_id
from nextron.domain.scoring import Tier
from nextron.web.ready_page import NO_CAPACITY
from tests.app.test_enroll import T0, World, build_world, stamp
from tests.app.test_selection import scored, warm
from tests.app.test_signal_inbox_drafting import inbox_item
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard

UNKNOWN = "01JD3QZ8X5V9K2M7N4P6R8T0AA"
HOSTILE_NAME = "<script>alert(1)</script> & Co"


class Page:
    def __init__(self, world: World) -> None:
        self.world = world
        self.dashboard = Dashboard(units=world.units, clock=world.clock)
        self.cookie = self.dashboard.open_session()

    def get(self, path: str) -> Reply:
        return self.dashboard.get(path, cookie=self.cookie)

    def pick(self, body: bytes) -> Reply:
        return self.dashboard.post(
            "/leads/ready/pick",
            cookie=self.cookie,
            token=self.dashboard.csrf_token(self.cookie),
            headers=[(b"content-type", FORM)],
            body=body,
        )


def picks(*lead_ids: str, **extra: str) -> bytes:
    return urlencode([*(("lead", lead_id) for lead_id in lead_ids), *extra.items()]).encode()


@pytest.fixture
def page(db_engine: Engine) -> Page:
    return Page(build_world(db_engine))


def test_without_warm_inboxes_the_header_says_no_capacity(page: Page) -> None:
    lead_id, _ = scored(page.world, Tier.C, 40)

    reply = page.get("/leads/ready")

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    assert NO_CAPACITY in reply.text
    assert f'name="lead" value="{lead_id}"' in reply.text
    assert "generic copy" in reply.text and "below minimum score" in reply.text
    assert 'hx-post="/leads/ready/pick"' in reply.text
    assert 'name="csrf_token"' in reply.text


def test_the_header_shows_what_remains_today(page: Page) -> None:
    warm(page.world, 30)
    scored(page.world, Tier.B, 60)

    reply = page.get("/leads/ready")

    assert "Remaining today: 7 of 7" in reply.text
    assert "generic copy" not in reply.text
    assert "No Leads are ready" not in reply.text


def test_an_empty_list_says_so(page: Page) -> None:
    assert "No Leads are ready to draft." in page.get("/leads/ready").text


def test_a_pick_answers_the_list_with_a_result_per_lead(page: Page) -> None:
    warm(page.world, 4)  # 4 // 4 = 1 new Sequence today
    first, _ = scored(page.world, Tier.B, 60)
    second, _ = scored(page.world, Tier.B, 60)

    reply = page.pick(picks(first, second, UNKNOWN))

    assert reply.status == 200
    assert reply.text.lstrip().startswith('<section class="ready-list" id="ready-list"')
    assert "<td>Enrolled</td>" in reply.text
    assert "Refused: no capacity left today" in reply.text
    assert "Refused: the Lead is unknown or merged" in reply.text
    assert "Unknown Lead" in reply.text
    assert "Remaining today: 0 of 1" in reply.text
    assert f'value="{first}"' not in reply.text  # enrolled: off the list
    assert f'value="{second}"' in reply.text
    assert page.world.sql("SELECT origin FROM enrollments") == [("operator_pick",)]


@pytest.mark.parametrize(
    "body",
    [
        b"",
        picks("not-a-ulid"),
        picks(UNKNOWN, UNKNOWN),
        picks(*[f"01JD3QZ8X5V9K2M7N4P6R8T{n:03d}" for n in range(201)]),
        picks(UNKNOWN, other="x"),
    ],
)
def test_a_malformed_pick_form_is_400(page: Page, body: bytes) -> None:
    reply = page.pick(body)

    assert reply.status == 400
    assert page.world.sql("SELECT COUNT(*) FROM daily_capacity") == [(0,)]


def test_two_hundred_picks_are_accepted(page: Page) -> None:
    reply = page.pick(picks(*[f"01JD3QZ8X5V9K2M7N4P6R8T{n:03d}" for n in range(200)]))

    assert reply.status == 200
    assert reply.text.count("Refused: the Lead is unknown or merged") == 200


def test_the_leads_list_links_to_the_page(page: Page) -> None:
    assert '<a href="/leads/ready">Ready to draft</a>' in page.get("/leads").text


def test_the_area_shows_the_signal_shortfall_while_capacity_remains(page: Page) -> None:
    assert "signal shortfall" not in page.get("/notifications").text

    warm(page.world, 30)
    area = page.get("/notifications").text

    assert '<a href="/leads/ready">signal shortfall: 7</a>' in area


def test_the_area_tells_mac_a_signal_inbox_lead_was_not_drafted(page: Page) -> None:
    """Story 4.16: a Signal Inbox Lead refused at scoring shows "not drafted" (its name
    escaped, inside hx-disable), and is first on the list with its badge, until it is picked."""
    world = page.world
    lead_id, _ = scored(world, Tier.B, 60)
    world.sql("UPDATE leads SET name = ? WHERE id = ?", (HOSTILE_NAME, lead_id))
    inbox_item(world, lead_id)
    with world.units.open():
        page.dashboard.lead_scores.score_lead(lead_id)  # no warm inbox: over capacity

    area = page.get("/notifications").text

    assert '<ul class="signal-not-drafted" hx-disable>' in area
    line = f"Signal Inbox Lead {escape(HOSTILE_NAME)} not drafted: no capacity left today"
    assert f'<li><a href="/leads/{lead_id}">{line}</a></li>' in area
    assert "<script>" not in area
    assert "Signal Inbox: not drafted" in page.get("/leads/ready").text

    warm(world, 30)
    assert page.pick(picks(lead_id)).status == 200

    assert "not drafted" not in page.get("/notifications").text
    assert "Signal Inbox: not drafted" not in page.get("/leads/ready").text


def test_the_area_shows_at_most_ten_not_drafted_lines(page: Page) -> None:
    world = page.world
    for _ in range(11):
        lead_id, _contact = scored(world, Tier.B, 60)
        world.sql(
            "INSERT INTO signal_inbox_items (id, url, text, status, submitted_at, processed_at, "
            "lead_id, draft_when_ready, draft_outcome, draft_decided_at) VALUES (?, "
            "'https://post.example/1', 'Text', 'processed', ?, ?, ?, 1, 'disqualified', ?)",
            (new_id(), stamp(T0), stamp(T0), lead_id, stamp(T0)),
        )

    area = page.get("/notifications").text

    assert area.count("not drafted: Lead disqualified") == 10
