"""The Campaigns page (FR-3, AD-13, UX-DR3, UX-DR23; Story 11.8): ``GET /campaigns``, ``GET
/campaigns/{id}`` and its five POSTs, through ``Dashboard`` on a temporary database at head. It
checks the list and the create form, a create and its refusal, the edit form with a save, a
stale form, the enrolled read-only Segment and Market, the Variant forms (add, edit, retire,
enrolled), the Settings link, the 404, and the CSRF and malformed refusals (nothing written)."""

import re
from html import unescape
from typing import Any
from urllib.parse import urlencode

import pytest
from sqlalchemy import Engine

from nextron.app.queries import setup as setup_queries
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.campaign import LAST_ACTIVE_VARIANT, MAX_STEP_ANGLES, VARIANT_NAME_TAKEN
from nextron.domain.enrollment import NewEnrollment
from nextron.web.csrf import SECURITY_CHECK_FAILED
from tests.app.test_campaign_editing import (  # noqa: F401 - the bare fixture
    bare,
    read,
    ref,
    sql_write,
)
from tests.app.test_enroll import US_CAMPAIGN, World, build_world
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard
from tests.fakes.clock import DEFAULT_NOW, FrozenClock


@pytest.fixture
def dashboard(bare: UnitOfWorkFactory) -> Dashboard:  # noqa: F811
    return Dashboard(units=bare, clock=FrozenClock(DEFAULT_NOW))


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


def post(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    path: str,
    fields: list[tuple[str, str]],
    *,
    token: bool = True,
) -> Reply:
    cookie, csrf = session
    return dashboard.post(
        path,
        cookie=cookie,
        token=csrf if token else None,
        headers=[(b"content-type", FORM)],
        body=urlencode(fields).encode("utf-8"),
    )


def texts(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def snapshot(units: UnitOfWorkFactory) -> list[Any]:
    with units.engine.connect() as connection:
        return [
            connection.exec_driver_sql("SELECT * FROM campaigns ORDER BY id").all(),
            connection.exec_driver_sql("SELECT * FROM variants ORDER BY id").all(),
            connection.exec_driver_sql("SELECT count(*) FROM events").all(),
        ]


def create_fields(units: UnitOfWorkFactory, key: str = "law_us") -> list[tuple[str, str]]:
    ids = ref(units)
    return [
        ("key", key),
        ("name", "Law US"),
        ("segment", ids["segments:law_private_ai"]),
        ("market", ids["markets:us"]),
        ("offer", ids["offers:default"]),
        ("sequence", ids["sequences:default"]),
    ]


def stored(units: UnitOfWorkFactory, key: str = "law_us") -> Any:
    with units.open(read_only=True):
        [found] = [c for c in setup_queries.campaigns() if c.key == key]
    return found


def created(dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory) -> Any:
    reply = post(dashboard, session, "/campaigns/create", create_fields(units))
    assert reply.status == 200, reply.text
    return stored(units)


def edit_fields(found: Any, **changes: str) -> list[tuple[str, str]]:
    fields = {
        "based_on": str(found.version),
        "name": found.name,
        "segment": found.segment_id,
        "market": found.market_id,
        "offer": found.offer_id,
        "sequence": found.sequence_id,
        **changes,
    }
    return list(fields.items())


def variant_fields(based_on: int, name: str, *notes: tuple[str, str]) -> list[tuple[str, str]]:
    fields = [("based_on", str(based_on)), ("name", name)]
    for index, (step, note) in enumerate(notes, start=1):
        fields += [(f"angles[{index}].step", step), (f"angles[{index}].note", note)]
    return fields


# The list and create ---------------------------------------------------------------------------------


def test_the_list_offers_the_create_form_and_a_create_shows_its_notice(
    dashboard: Dashboard, session: tuple[bytes, str], bare: UnitOfWorkFactory  # noqa: F811
) -> None:
    page = dashboard.get("/campaigns", cookie=session[0])

    assert page.status == 200
    assert "No Campaigns yet." in texts(page.text)
    assert 'hx-post="/campaigns/create"' in page.text and 'name="csrf_token"' in page.text

    reply = post(dashboard, session, "/campaigns/create", create_fields(bare))

    found = stored(bare)
    assert reply.status == 200
    assert "Created law_us as a draft." in texts(reply.text)
    assert f'href="/campaigns/{found.id}"' in reply.text
    rows = re.findall(r'<tr class="campaign-row-item">(.*?)</tr>', reply.text, re.DOTALL)
    assert len(rows) == 1
    assert texts(rows[0]).startswith("law_us Law US draft ")
    assert texts(rows[0]).endswith(" 0")  # no active Variant yet


def test_a_refused_create_shows_each_problem_and_writes_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], bare: UnitOfWorkFactory  # noqa: F811
) -> None:
    before = snapshot(bare)
    fields = [
        (name, "" if name in ("key", "segment") else value)
        for name, value in create_fields(bare)
    ]

    reply = post(dashboard, session, "/campaigns/create", fields)

    assert reply.status == 200
    assert snapshot(bare) == before
    shown = texts(reply.text)
    assert "Nothing was saved. Correct the fields marked below, then save again." in shown
    assert 'id="campaign-create-key" name="key" aria-describedby' in reply.text
    assert "Choose one" in shown


# One Campaign -----------------------------------------------------------------------------------------


def test_the_campaign_page_edits_adds_edits_and_retires_variants(
    dashboard: Dashboard, session: tuple[bytes, str], bare: UnitOfWorkFactory  # noqa: F811
) -> None:
    found = created(dashboard, session, bare)
    base = f"/campaigns/{found.id}"

    page = dashboard.get(base, cookie=session[0])
    assert page.status == 200
    assert "No Variants yet." in texts(page.text)

    saved = post(dashboard, session, f"{base}/edit", edit_fields(found, name="Law in the US"))
    assert "Saved law_us as version 2." in texts(saved.text)

    added = post(
        dashboard, session, f"{base}/variants/add", variant_fields(2, "A", ("2", "Intake"))
    )
    assert "Added Variant A." in texts(added.text)
    variant = stored(bare).variants[0]
    assert "Step 2: Intake" in texts(added.text)

    edited = post(
        dashboard, session, f"{base}/variants/{variant.id}/edit", variant_fields(3, "Anchor")
    )
    assert "Saved Variant Anchor." in texts(edited.text)

    retired = post(
        dashboard, session, f"{base}/variants/{variant.id}/retire", [("based_on", "4")]
    )
    assert "Retired Variant Anchor." in texts(retired.text)
    assert "Anchor (inactive)" in texts(retired.text)
    assert stored(bare).version == 5


def test_a_refused_variant_shows_its_problem_beside_the_notes(
    dashboard: Dashboard, session: tuple[bytes, str], bare: UnitOfWorkFactory  # noqa: F811
) -> None:
    found = created(dashboard, session, bare)
    before = snapshot(bare)

    reply = post(
        dashboard,
        session,
        f"/campaigns/{found.id}/variants/add",
        variant_fields(1, "A", ("5", "Too far")),
    )

    assert reply.status == 200
    assert snapshot(bare) == before
    assert "Step 5 is beyond the Sequence's 4 Steps" in texts(reply.text)
    assert 'value="Too far"' in reply.text  # the submission is shown again


def angle_indices(html: str) -> list[int]:
    return [int(n) for n in re.findall(r'name="angles\[([0-9]+)\]\.step"', html)]


def test_sparse_refused_note_rows_come_back_as_rows_1_to_6(
    dashboard: Dashboard, session: tuple[bytes, str], bare: UnitOfWorkFactory  # noqa: F811
) -> None:
    found = created(dashboard, session, bare)
    before = snapshot(bare)
    fields = [
        ("based_on", "1"),
        ("name", "A"),
        ("angles[3].step", "2"),
        ("angles[3].note", "Intake"),
        ("angles[5].step", "5"),
        ("angles[5].note", "Too far"),
    ]

    reply = post(dashboard, session, f"/campaigns/{found.id}/variants/add", fields)

    assert reply.status == 200
    assert snapshot(bare) == before
    assert "Step 5 is beyond the Sequence's 4 Steps" in texts(reply.text)
    indices = angle_indices(reply.text)  # only the add form: the Campaign has no Variant
    assert indices[:2] == [3, 5]  # the submitted rows first, as submitted
    assert sorted(indices) == list(range(1, MAX_STEP_ANGLES + 1))


def test_a_refused_variant_edit_shows_its_submission_inside_its_own_form(
    dashboard: Dashboard, session: tuple[bytes, str], bare: UnitOfWorkFactory  # noqa: F811
) -> None:
    found = created(dashboard, session, bare)
    base = f"/campaigns/{found.id}"
    post(dashboard, session, f"{base}/variants/add", variant_fields(1, "A"))
    post(dashboard, session, f"{base}/variants/add", variant_fields(2, "B"))
    a, b = stored(bare).variants
    before = snapshot(bare)

    reply = post(dashboard, session, f"{base}/variants/{b.id}/edit", variant_fields(3, "a"))

    assert reply.status == 200
    assert snapshot(bare) == before
    [own] = re.findall(
        re.escape(f'action="{base}/variants/{b.id}/edit"') + r".*?</form>", reply.text, re.DOTALL
    )
    assert 'value="a"' in own  # the submitted name, shown again
    assert VARIANT_NAME_TAKEN in texts(own)
    [other] = re.findall(
        re.escape(f'action="{base}/variants/{a.id}/edit"') + r".*?</form>", reply.text, re.DOTALL
    )
    assert VARIANT_NAME_TAKEN not in texts(other) and 'value="A"' in other


@pytest.mark.parametrize(
    "extra",
    [
        [("angles[7].step", "1"), ("angles[7].note", "x")],
        [("name", "Again")],
    ],
    ids=["index-7", "repeated-name"],
)
def test_a_malformed_variant_form_is_400_and_writes_nothing(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    bare: UnitOfWorkFactory,  # noqa: F811
    extra: list[tuple[str, str]],
) -> None:
    found = created(dashboard, session, bare)
    post(dashboard, session, f"/campaigns/{found.id}/variants/add", variant_fields(1, "A"))
    variant = stored(bare).variants[0]
    before = snapshot(bare)

    for path in (
        f"/campaigns/{found.id}/variants/add",
        f"/campaigns/{found.id}/variants/{variant.id}/edit",
    ):
        reply = post(dashboard, session, path, variant_fields(2, "B") + extra)
        assert reply.status == 400, path
    assert snapshot(bare) == before


def corrupt_a_variant(units: UnitOfWorkFactory, campaign_id: str) -> None:
    sql_write(units, "UPDATE variants SET step_angles = 'x' WHERE campaign_id = ?", campaign_id)


def test_an_undecodable_row_shows_the_full_list_page_with_the_notice(
    dashboard: Dashboard, session: tuple[bytes, str], bare: UnitOfWorkFactory  # noqa: F811
) -> None:
    found = created(dashboard, session, bare)
    post(dashboard, session, f"/campaigns/{found.id}/variants/add", variant_fields(1, "A"))
    corrupt_a_variant(bare, found.id)

    for path in (f"/campaigns/{found.id}", "/campaigns"):
        reply = dashboard.get(path, cookie=session[0])
        assert reply.status == 200, path
        assert "<h1>Campaigns</h1>" in reply.text  # the full page, in the base layout
        assert "<html" in reply.text
        assert 'id="campaign-list-panel"' in reply.text
        assert "The Campaigns can't be shown:" in texts(reply.text)


def test_a_post_that_meets_an_undecodable_row_shows_the_notice_and_writes_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], bare: UnitOfWorkFactory  # noqa: F811
) -> None:
    found = created(dashboard, session, bare)
    post(dashboard, session, f"/campaigns/{found.id}/variants/add", variant_fields(1, "A"))
    variant = stored(bare).variants[0]
    corrupt_a_variant(bare, found.id)
    before = snapshot(bare)
    base = f"/campaigns/{found.id}"

    for path, fields in (
        (f"{base}/edit", edit_fields(found, based_on="2", name="X")),
        (f"{base}/variants/add", variant_fields(2, "B")),
        (f"{base}/variants/{variant.id}/edit", variant_fields(2, "Z")),
        (f"{base}/variants/{variant.id}/retire", [("based_on", "2")]),
    ):
        reply = post(dashboard, session, path, fields)
        assert reply.status == 200, path
        assert "The Campaigns can't be shown:" in texts(reply.text), path
        assert "<h1>" not in reply.text  # the panel only
    assert snapshot(bare) == before


def test_a_create_that_meets_an_undecodable_row_shows_the_notice_and_writes_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], bare: UnitOfWorkFactory  # noqa: F811
) -> None:
    fields = create_fields(bare)
    sql_write(bare, "UPDATE offers SET ladder = 'x'")  # create decodes the Offers, not Variants
    before = snapshot(bare)

    reply = post(dashboard, session, "/campaigns/create", fields)

    assert reply.status == 200
    assert "The Campaigns can't be shown:" in texts(reply.text)
    assert snapshot(bare) == before


def test_a_stale_form_shows_the_current_campaign(
    dashboard: Dashboard, session: tuple[bytes, str], bare: UnitOfWorkFactory  # noqa: F811
) -> None:
    found = created(dashboard, session, bare)
    post(dashboard, session, f"/campaigns/{found.id}/edit", edit_fields(found, name="First"))
    before = snapshot(bare)

    late = edit_fields(found, name="Late")

    reply = post(dashboard, session, f"/campaigns/{found.id}/edit", late)

    assert reply.status == 200
    assert snapshot(bare) == before
    shown = texts(reply.text)
    assert "This Campaign changed since you opened it." in shown
    assert "The page now shows the current Campaign." in shown
    assert 'value="First"' in reply.text and 'name="based_on" value="2"' in reply.text


def test_an_unknown_campaign_is_404_and_the_settings_page_links_here(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    for path in ("/campaigns/01ARZ3NDEKTSV4RRFFQ69G5FAV", "/campaigns/not-an-id"):
        reply = dashboard.get(path, cookie=session[0])
        assert (reply.status, reply.text) == (404, "That Campaign doesn't exist.")
    settings = dashboard.get("/settings", cookie=session[0])
    assert 'href="/campaigns"' in settings.text


@pytest.mark.parametrize(
    "path",
    [
        "/campaigns/create",
        "/campaigns/{id}/edit",
        "/campaigns/{id}/variants/add",
        "/campaigns/{id}/variants/{vid}/edit",
        "/campaigns/{id}/variants/{vid}/retire",
    ],
)
def test_a_missing_csrf_token_writes_nothing(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    bare: UnitOfWorkFactory,  # noqa: F811
    path: str,
) -> None:
    found = created(dashboard, session, bare)
    post(dashboard, session, f"/campaigns/{found.id}/variants/add", variant_fields(1, "A"))
    found = stored(bare)
    before = snapshot(bare)
    target = path.format(id=found.id, vid=found.variants[0].id)
    fields = {
        "/campaigns/create": create_fields(bare, "other"),
        f"/campaigns/{found.id}/edit": edit_fields(found, name="X"),
    }.get(target, variant_fields(found.version, "B"))
    if target.endswith("/retire"):
        fields = [("based_on", str(found.version))]

    reply = post(dashboard, session, target, fields, token=False)

    assert (reply.status, reply.text) == (403, SECURITY_CHECK_FAILED)
    assert snapshot(bare) == before


@pytest.mark.parametrize(
    "fields",
    [
        [("based_on", "1"), ("name", "X")],  # missing references
        [("based_on", "0"), ("name", "X"), ("segment", ""), ("market", ""), ("offer", ""),
         ("sequence", "")],
        [("based_on", "1"), ("name", "X"), ("name", "Y"), ("segment", ""), ("market", ""),
         ("offer", ""), ("sequence", "")],
        [("based_on", "1"), ("name", "X"), ("key", "x"), ("segment", ""), ("market", ""),
         ("offer", ""), ("sequence", "")],
    ],
    ids=["missing", "zero-based-on", "repeated", "key-not-editable"],
)
def test_a_malformed_edit_is_400_and_writes_nothing(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    bare: UnitOfWorkFactory,  # noqa: F811
    fields: list[tuple[str, str]],
) -> None:
    found = created(dashboard, session, bare)
    before = snapshot(bare)

    reply = post(dashboard, session, f"/campaigns/{found.id}/edit", fields)

    assert reply.status == 400
    assert snapshot(bare) == before


# Enrolled ---------------------------------------------------------------------------------------------


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def test_an_enrolled_campaign_shows_its_segment_and_market_read_only(world: World) -> None:
    dashboard = Dashboard(units=world.units, clock=FrozenClock(DEFAULT_NOW))
    cookie = dashboard.open_session()
    campaign_id = world.campaign(US_CAMPAIGN)
    _, contact_id = world.contact()
    assert isinstance(world.enroll(contact_id, campaign_id), NewEnrollment)
    world.add_variant(US_CAMPAIGN, "B")  # so A isn't the running Campaign's last active one

    page = dashboard.get(f"/campaigns/{campaign_id}", cookie=cookie)

    shown = texts(page.text)
    assert page.status == 200
    assert "Segment and Market can't change once Contacts are enrolled." in shown
    assert 'id="campaign-edit-segment"' not in page.text  # no select
    assert '<input type="hidden" name="segment"' in page.text
    assert "1 Enrollment" in shown
    assert "Retire this Variant and add a new one, so its results stay comparable." in shown
    assert "/retire" in page.text  # it may still be retired
    a = world.variants(US_CAMPAIGN)["A"]
    assert f"/variants/{a}/retire" in page.text
    assert f"/variants/{a}/edit" not in page.text  # no edit form for it
    assert 'hx-confirm="Retire Variant A? Retiring is final."' in page.text
    # 2 active Variants: no add form, the reason instead
    assert "/variants/add" not in page.text
    assert "A Campaign can have at most 2 Variants." in shown


def test_a_running_campaign_s_last_active_variant_offers_no_retire(world: World) -> None:
    dashboard = Dashboard(units=world.units, clock=FrozenClock(DEFAULT_NOW))
    cookie = dashboard.open_session()
    session = (cookie, dashboard.csrf_token(cookie))
    campaign_id = world.campaign(US_CAMPAIGN)
    [a] = world.variants(US_CAMPAIGN).values()

    page = dashboard.get(f"/campaigns/{campaign_id}", cookie=cookie)

    assert "/retire" not in page.text
    assert f"{LAST_ACTIVE_VARIANT}." in texts(page.text)
    assert "/variants/add" in page.text  # 1 active Variant: one more may be added
    before = snapshot(world.units)
    version = world.read(lambda: setup_queries.campaign(campaign_id)).version

    reply = post(
        dashboard,
        session,
        f"/campaigns/{campaign_id}/variants/{a}/retire",
        [("based_on", str(version))],
    )

    assert reply.status == 200
    assert f"Nothing was saved. {LAST_ACTIVE_VARIANT}." in texts(reply.text)
    assert snapshot(world.units) == before
