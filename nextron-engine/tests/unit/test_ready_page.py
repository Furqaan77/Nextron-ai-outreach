"""Story 4.10's "Ready to draft" page (``GET /leads/ready``, ``POST /leads/ready/pick``) and the
notification area's signal shortfall line, through ``Dashboard(units=...)`` on the world of
``tests.app.test_enroll``: the header texts, the rows and badges, the pick form, the result
table, the 400s of a malformed form, the Leads list's link and the area line."""

from urllib.parse import urlencode

import pytest
from sqlalchemy import Engine

from nextron.domain.scoring import Tier
from nextron.web.ready_page import NO_CAPACITY
from tests.app.test_enroll import World, build_world
from tests.app.test_selection import scored, warm
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard

UNKNOWN = "01JD3QZ8X5V9K2M7N4P6R8T0AA"


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
