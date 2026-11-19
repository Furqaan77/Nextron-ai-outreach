"""The Sequences section of Settings (FR-4, AD-13, UX-DR3, UX-DR23; Story 11.7): ``GET
/sequences``, ``GET /sequences/{id}``, ``GET /sequences/{id}/versions`` and ``POST
/sequences/{id}/edit``, through ``Dashboard(units=units)`` on a temporary database at head,
seeded with the shipped files. It checks the list, the prefilled form with its read-only fixed
rules, a save and its notice, a refusal re-rendered beside its field (and beside the right row
after a removal), a stale form, the version history, and the CSRF and malformed refusals
(nothing written)."""

import re
from html import unescape
from typing import Any
from urllib.parse import urlencode

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.ops.api import Seeds
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.sequence_editing import SequenceEditor
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import seed_yaml
from nextron.domain.campaign import validate_campaigns
from nextron.domain.company_facts import validate_company_facts
from nextron.domain.markets import validate_markets
from nextron.domain.offers import validate_offers
from nextron.domain.segments import validate_segments
from nextron.domain.enrollment import NewEnrollment
from nextron.domain.sequences import validate_sequences
from nextron.web import sequences_page
from nextron.web.csrf import SECURITY_CHECK_FAILED
from tests.app.test_enroll import US_CAMPAIGN, build_world
from tests.app.test_sequence_editing import fields_of
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard
from tests.fakes.clock import DEFAULT_NOW, FrozenClock


def read(name: str) -> object:
    return seed_yaml.parse((main.SEED_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def seeded(units: UnitOfWorkFactory) -> UnitOfWorkFactory:
    with units.open():
        Seeds(FrozenClock()).sync_seed(
            validate_segments(read(main.SEGMENTS_FILE_NAME)),
            validate_markets(read(main.MARKETS_FILE_NAME)),
            validate_sequences(read(main.SEQUENCES_FILE_NAME)),
            validate_company_facts(read(main.COMPANY_FACTS_FILE_NAME)),
            validate_offers(read(main.OFFERS_FILE_NAME)),
            validate_campaigns(read(main.CAMPAIGNS_FILE_NAME)),
        )
    return units


@pytest.fixture
def dashboard(seeded: UnitOfWorkFactory) -> Dashboard:
    return Dashboard(units=seeded, clock=FrozenClock(DEFAULT_NOW))


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


def sequence(units: UnitOfWorkFactory) -> Any:
    with units.open(read_only=True):
        found = setup_queries.sequence("default")
    assert found is not None
    return found


def snapshot(units: UnitOfWorkFactory) -> list[Any]:
    with units.engine.connect() as connection:
        return [
            connection.exec_driver_sql("SELECT * FROM sequences ORDER BY key").all(),
            connection.exec_driver_sql("SELECT * FROM step_briefs ORDER BY id").all(),
            connection.exec_driver_sql("SELECT * FROM sequence_versions ORDER BY id").all(),
            connection.exec_driver_sql("SELECT count(*) FROM events").all(),
        ]


def edit_fields(units: UnitOfWorkFactory, **changes: str) -> list[tuple[str, str]]:
    """The fields a browser sends for the stored Sequence as the edit form shows it (no blank
    rows), with ``changes`` (``steps__3__ask`` for ``steps[3].ask``)."""
    found = sequence(units)
    submitted = sequences_page.submitted_from(found.definition)
    fields: dict[str, str] = {"based_on": str(found.version), "name": submitted.name}
    for row in submitted.steps:
        base = f"steps[{row.index}]."
        fields[base + "stored_step"] = str(row.stored_step)
        for name, value in row.values.items():
            if row.index == 1 and name == "delay_days":
                continue  # Step 1's delay is fixed: the form has no field for it
            fields[base + name] = value
    for name, value in changes.items():
        parts = name.split("__")
        key = parts[0] if len(parts) == 1 else f"{parts[0]}[{parts[1]}].{parts[2]}"
        fields[key] = value
    return list(fields.items())


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


# Pages ---------------------------------------------------------------------------------------------


def test_the_list_shows_each_sequence_with_version_steps_and_campaigns(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    reply = dashboard.get("/sequences", cookie=session[0])

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    rows = re.findall(r'<tr class="sequence-row-item">(.*?)</tr>', reply.text, re.DOTALL)
    assert [texts(row) for row in rows] == ["default Default four-Step Sequence 1 4 4 Versions"]
    assert f'href="/sequences/{sequence(seeded).id}/versions"' in reply.text


def test_the_settings_page_links_to_the_sequences_page(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    settings = dashboard.get("/settings", cookie=session[0]).text

    assert '<a href="/sequences">Sequences page</a>' in settings


def test_the_edit_page_is_prefilled_with_fixed_rules_read_only(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    found = sequence(seeded)

    reply = dashboard.get(f"/sequences/{found.id}", cookie=session[0])

    assert reply.status == 200
    html = reply.text
    assert '<input type="hidden" name="based_on" value="1">' in html
    assert 'name="name" value="Default four-Step Sequence"' in html
    assert 'name="steps[2].delay_days" value="3"' in html
    assert 'name="steps[1].delay_days"' not in html  # fixed
    assert 'name="steps[1].remove"' not in html  # Step 1 can't be removed
    assert 'name="steps[4].remove"' in html
    assert 'name="steps[5].stored_step" value=""' in html  # 2 blank rows up to 6 Steps
    assert 'name="steps[6].stored_step" value=""' in html
    assert 'name="steps[7].' not in html
    for fixed in ("link_rule", "no_price", "no_audit", "subject_rule", "segment_angles"):
        assert f'name="steps[1].{fixed}"' not in html
    page = texts(html)
    assert "Set in code (FR-4)" in page
    assert "Subject: under 7 words: About the prospect's world; Never misleading" in page
    assert "law_private_ai in us: What ABA Formal Opinion 512 means" in page
    assert 'hx-sync="this:drop"' in html


def test_the_form_says_which_versions_running_enrollments_keep(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    found = sequence(seeded)
    context = sequences_page.edit_context(found, (), {1: 2, 2: 1})

    assert context["enrollments"] == (
        "2 active Enrollments keep version 1",
        "1 active Enrollment keeps version 2",
    )
    reply = dashboard.get(f"/sequences/{found.id}", cookie=session[0])
    assert "active Enrollment" not in reply.text  # nobody is enrolled yet


def test_the_page_says_which_versions_live_enrollments_keep(db_engine: Engine) -> None:
    """Two Contacts enrolled at version 1, one after a save at version 2, through the route."""
    world = build_world(db_engine)
    campaign = world.campaign(US_CAMPAIGN)
    for _ in range(2):
        _, contact_id = world.contact()
        assert isinstance(world.enroll(contact_id, campaign), NewEnrollment)
    found = sequence(world.units)
    with world.units.open():
        fields = fields_of(found.definition)
        fields["name"] = "Renamed"
        assert SequenceEditor(world.clock).edit(found.id, found.version, fields).changed
    _, contact_id = world.contact()
    assert isinstance(world.enroll(contact_id, campaign), NewEnrollment)
    dashboard = Dashboard(units=world.units, clock=FrozenClock(DEFAULT_NOW))
    cookie = dashboard.open_session()

    page = texts(dashboard.get(f"/sequences/{found.id}", cookie=cookie).text)

    assert "2 active Enrollments keep version 1" in page
    assert "1 active Enrollment keeps version 2" in page


def test_an_unreadable_stored_sequence_shows_the_unavailable_notice(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    found = sequence(seeded)
    with seeded.engine.begin() as connection:
        connection.exec_driver_sql("UPDATE step_briefs SET allowed_content = 'x' WHERE step = 2")
    before = snapshot(seeded)

    for path in (f"/sequences/{found.id}", f"/sequences/{found.id}/versions"):
        reply = dashboard.get(path, cookie=session[0])
        assert reply.status == 200, path
        assert '<p class="notice notice-refused" role="alert">' in reply.text, path
    fields = [("based_on", "1"), ("name", "Renamed"), ("steps[1].stored_step", "1")]
    edited = post(dashboard, session, f"/sequences/{found.id}/edit", fields)
    assert edited.status == 200
    assert '<p class="notice notice-refused" role="alert">' in edited.text
    assert snapshot(seeded) == before


def test_an_unknown_sequence_is_404_and_never_echoed(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    for path in ("/sequences/01ARZ3NDEKTSV4RRFFQ69G5FAV", "/sequences/not-a-ulid/versions"):
        reply = dashboard.get(path, cookie=session[0])
        assert (reply.status, reply.text) == (404, "That Sequence doesn't exist.")


# Saving --------------------------------------------------------------------------------------------


def test_a_save_stores_version_2_and_says_so(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    found = sequence(seeded)

    reply = post(
        dashboard,
        session,
        f"/sequences/{found.id}/edit",
        edit_fields(seeded, steps__3__ask="Offer a short case study by reply"),
    )

    assert reply.status == 200
    assert "Saved default as version 2." in texts(reply.text)
    assert '<input type="hidden" name="based_on" value="2">' in reply.text
    after = sequence(seeded)
    assert (after.version, after.definition.steps[2].ask) == (
        2,
        "Offer a short case study by reply",
    )


def test_a_removed_step_and_an_appended_one_are_saved(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    found = sequence(seeded)
    fields = edit_fields(seeded) + [
        ("steps[3].remove", "on"),
        ("steps[5].stored_step", ""),
        ("steps[5].delay_days", "21"),
        ("steps[5].purpose", "A last note"),
        ("steps[5].word_limit", "40"),
        ("steps[5].word_comparison", "at_most"),
        ("steps[5].allowed_content", "One new fact\n\nfrom Approved Company Facts\n"),
        ("steps[5].ask", "Whether to check back next quarter"),
        ("steps[6].stored_step", ""),  # a blank appended row is left out
        ("steps[6].purpose", ""),
    ]

    reply = post(dashboard, session, f"/sequences/{found.id}/edit", fields)

    assert "Saved default as version 2." in texts(reply.text)
    steps = sequence(seeded).definition.steps
    assert [brief.delay_days for brief in steps] == [0, 3, 14, 21]
    assert steps[3].allowed_content == ("One new fact", "from Approved Company Facts")


def test_an_unchanged_form_writes_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    found = sequence(seeded)
    before = snapshot(seeded)

    reply = post(dashboard, session, f"/sequences/{found.id}/edit", edit_fields(seeded))

    assert "Nothing changed, so no new version was saved." in texts(reply.text)
    assert snapshot(seeded) == before


def test_a_refusal_is_re_rendered_beside_its_field(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    found = sequence(seeded)
    before = snapshot(seeded)

    reply = post(
        dashboard, session, f"/sequences/{found.id}/edit", edit_fields(seeded, steps__3__delay_days="3")
    )

    assert reply.status == 200
    assert snapshot(seeded) == before
    html = reply.text
    assert 'role="alert"' in html and "Nothing was saved." in texts(html)
    assert 'id="sequence-edit-steps-3-delay_days" name="steps[3].delay_days" aria-describedby' in html
    assert "must be more than Step 2's delay_days (3)" in texts(html)
    assert 'aria-invalid="true" value="3"' in html  # re-shown as typed


def test_a_refusal_after_a_removal_is_shown_beside_the_row_it_came_from(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    found = sequence(seeded)
    fields = edit_fields(seeded, steps__4__ask="") + [("steps[2].remove", "on")]

    reply = post(dashboard, session, f"/sequences/{found.id}/edit", fields)

    assert 'id="sequence-edit-steps-4-ask-error"' in reply.text  # proposed Step 3, form row 4
    assert 'name="steps[2].remove" value="on" checked' in reply.text


def test_a_stale_form_is_refused_in_the_notice(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    found = sequence(seeded)
    stale = edit_fields(seeded, name="Changed twice")
    post(dashboard, session, f"/sequences/{found.id}/edit", edit_fields(seeded, name="Changed"))
    before = snapshot(seeded)

    reply = post(dashboard, session, f"/sequences/{found.id}/edit", stale)

    page = texts(reply.text)
    assert (
        "Nothing was saved. This Sequence changed since you opened it. The form now shows the "
        "current version. Make your change again." in page
    )
    assert snapshot(seeded) == before
    # the current version's form, so saving it again isn't refused as stale
    assert '<input type="hidden" name="based_on" value="2">' in reply.text
    assert 'name="name" value="Changed"' in reply.text
    assert "Changed twice" not in reply.text
    retried = edit_fields(seeded, name="Changed twice")
    again = post(dashboard, session, f"/sequences/{found.id}/edit", retried)
    assert "Saved default as version 3." in texts(again.text)


def test_the_versions_page_lists_every_version_with_its_changes(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    found = sequence(seeded)
    post(dashboard, session, f"/sequences/{found.id}/edit", edit_fields(seeded, name="Renamed"))
    post(
        dashboard,
        session,
        f"/sequences/{found.id}/edit",
        edit_fields(seeded, steps__2__word_limit="60"),
    )

    reply = dashboard.get(f"/sequences/{found.id}/versions", cookie=session[0])

    assert reply.status == 200
    page = texts(reply.text)
    assert "Version 1 Saved by seed" in page and "First stored version." in page
    assert "Version 2 Saved by operator" in page
    assert 'name "Default four-Step Sequence" "Renamed"' in page
    assert "Version 3 (current)" in page
    assert "steps[2].word_limit 70 60" in page
    assert "were not kept" not in page


# Refusals ------------------------------------------------------------------------------------------


def test_a_post_without_the_csrf_token_writes_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    found = sequence(seeded)
    before = snapshot(seeded)

    reply = post(
        dashboard,
        session,
        f"/sequences/{found.id}/edit",
        edit_fields(seeded, name="Changed"),
        token=False,
    )

    assert (reply.status, reply.text) == (403, SECURITY_CHECK_FAILED)
    assert snapshot(seeded) == before


def test_a_forged_csrf_token_writes_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    found = sequence(seeded)
    before = snapshot(seeded)

    reply = post(
        dashboard,
        (session[0], "forged-token"),
        f"/sequences/{found.id}/edit",
        edit_fields(seeded, name="Changed"),
    )

    assert reply.status == 403
    assert snapshot(seeded) == before


@pytest.mark.parametrize(
    "extra",
    [
        [("unknown", "x")],
        [("steps[100].ask", "x")],
        [("steps[0].ask", "x")],
        [("steps[01].ask", "x")],
        [("steps[2].bogus", "x")],
        [("steps[2].link_rule", "in_body")],  # a fixed field is not a form field
        [("steps[2].word_comparison", "about")],
        [("steps[2].stored_step", "x")],
        [("name", "again")],
    ],
)
def test_a_malformed_form_is_refused_with_400(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    seeded: UnitOfWorkFactory,
    extra: list[tuple[str, str]],
) -> None:
    found = sequence(seeded)
    fields = [field for field in edit_fields(seeded) if field[0] not in dict(extra)] + extra
    if extra == [("name", "again")]:
        fields = edit_fields(seeded) + extra
    before = snapshot(seeded)

    reply = post(dashboard, session, f"/sequences/{found.id}/edit", fields)

    assert reply.status == 400
    assert snapshot(seeded) == before


@pytest.mark.parametrize("based_on", ["0", "-1", "x", "01", ""])
def test_a_bad_based_on_is_malformed(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory, based_on: str
) -> None:
    found = sequence(seeded)
    fields = [(n, based_on if n == "based_on" else v) for n, v in edit_fields(seeded)]

    reply = post(dashboard, session, f"/sequences/{found.id}/edit", fields)

    assert reply.status == 400


def test_the_readme_documents_sequence_editing() -> None:
    readme = (main.SEED_DIR.parent / "README.md").read_text(encoding="utf-8")

    assert "### Editing Sequences" in readme
    assert "Sequences are managed in the dashboard; seed/sequences.yaml ignored" in readme
