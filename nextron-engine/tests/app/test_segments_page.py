"""The Segments section of Settings (FR-1, AD-13, UX-DR3, UX-DR23; Story 11.5): ``GET
/segments``, ``GET /segments/{id}``, ``GET /segments/{id}/versions`` and the three POSTs, and
the Lead page's "Re-score with the current definition", through ``Dashboard(units=units)`` on a
temporary database at head, seeded with the shipped files. It checks the list and its inert
marker, the prefilled edit form, a save and its notice, a refusal re-rendered with each reason
beside its field, a stale form, the version history with each version's changes, and the CSRF
and malformed refusals (nothing written)."""

import json
import re
from html import unescape
from typing import Any
from urllib.parse import urlencode

import pytest

from nextron import main
from nextron.app.discovery.api import Discovery
from nextron.app.ops.api import Seeds
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import setup as setup_queries
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.campaign import validate_campaigns
from nextron.domain.company_facts import validate_company_facts
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.markets import validate_markets
from nextron.domain.offers import validate_offers
from nextron.domain.segments import EDITING_RULE, SegmentDef, validate_segments
from nextron.domain.sequences import validate_sequences
from nextron.web import segments_page
from nextron.web.csrf import SECURITY_CHECK_FAILED
from tests.app.test_leads import queue_on
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard
from tests.fakes.clock import DEFAULT_NOW, FrozenClock

LAW = "law_private_ai"
OPS = "ops_automation"


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


def segment(units: UnitOfWorkFactory, key: str = LAW) -> Any:
    with units.open(read_only=True):
        found = setup_queries.segment(key)
    assert found is not None
    return found


def snapshot(units: UnitOfWorkFactory) -> list[Any]:
    with units.engine.connect() as connection:
        return [
            connection.exec_driver_sql("SELECT * FROM segments ORDER BY key").all(),
            connection.exec_driver_sql("SELECT * FROM segment_versions ORDER BY id").all(),
            connection.exec_driver_sql("SELECT count(*) FROM events").all(),
            connection.exec_driver_sql("SELECT count(*) FROM jobs").all(),
        ]


def fields_of(definition: SegmentDef, *, key: bool = False) -> dict[str, list[str]]:
    """The form fields a browser sends for ``definition`` as the edit form shows it."""
    submitted = segments_page.submitted_from(definition)
    fields: dict[str, list[str]] = {
        name: [value] for name, value in submitted.values.items() if name != "key" or key
    }
    for row in submitted.disqualifiers:
        for name, value in row.values.items():
            fields[f"disqualifiers[{row.index}].{name}"] = [value]
    for row in submitted.signal_types:
        base = f"signal_types[{row.index}]."
        for name, value in row.values.items():
            fields[base + name] = [value]
        fields[base + "evidence"] = list(row.evidence)
        fields[base + "sources"] = list(row.sources)
        if row.score_only:
            fields[base + "score_only"] = ["on"]
    return fields


def encoded(fields: dict[str, list[str]]) -> list[tuple[str, str]]:
    return [(name, value) for name, values in fields.items() for value in values]


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


def edit_fields(units: UnitOfWorkFactory, **changes: str) -> list[tuple[str, str]]:
    found = segment(units)
    fields = fields_of(found.definition)
    fields["based_on"] = [str(found.version)]
    for name, value in changes.items():
        fields[name.replace("__", ".")] = [value]
    return encoded(fields)


def texts(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def weight_field(definition: SegmentDef, key: str) -> str:
    index = [each.key for each in definition.signal_types].index(key)
    return f"signal_types[{index}].weight"


# The pages ---------------------------------------------------------------------------------------


def test_the_list_shows_each_segment_with_its_version_and_campaigns(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    reply = dashboard.get("/segments", cookie=session[0])

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    rows = re.findall(r'<tr class="segment-row-item">(.*?)</tr>', reply.text, re.DOTALL)
    assert [texts(row) for row in rows] == [
        "law_private_ai Law-PrivateAI 1 2 Versions",
        "ops_automation Ops-Automation 1 2 Versions",
    ]
    assert "Create a Segment" in reply.text
    law = segment(seeded)
    assert f'href="/segments/{law.id}/versions"' in reply.text


def test_the_settings_page_links_to_the_segments_page(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    settings = dashboard.get("/settings", cookie=session[0]).text

    assert '<a href="/segments">Segments page</a>' in settings


def test_the_edit_page_is_prefilled_with_the_current_version(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    law = segment(seeded)

    reply = dashboard.get(f"/segments/{law.id}", cookie=session[0])

    assert reply.status == 200
    html = reply.text
    assert '<input type="hidden" name="based_on" value="1">' in html
    assert 'name="name" value="Law-PrivateAI"' in html
    assert 'name="signal_types[0].key" value="law_hiring_legal_ops"' in html
    assert 'name="disqualifiers[0].key" value="law_fewer_than_5_lawyers"' in html
    blank = len(law.definition.signal_types)
    assert f'name="signal_types[{blank}].key" value=""' in html  # 2 blank rows
    assert f'name="signal_types[{blank + 1}].key" value=""' in html
    assert f'name="signal_types[{blank + 2}].key"' not in html
    assert 'action="/segments/' in html and "rescore-current" in html


def test_an_unknown_segment_is_404_and_never_echoed(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    for path in ("/segments/01ARZ3NDEKTSV4RRFFQ69G5FAV", "/segments/not-a-ulid/versions"):
        reply = dashboard.get(path, cookie=session[0])
        assert (reply.status, reply.text) == (404, "That Segment doesn't exist.")


# Saving --------------------------------------------------------------------------------------------


def test_a_save_stores_version_2_and_says_so(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    law = segment(seeded)
    field = weight_field(law.definition, "law_contact_form_only")

    reply = post(
        dashboard, session, f"/segments/{law.id}/edit", edit_fields(seeded, **{field: "20"})
    )

    assert reply.status == 200
    assert "Saved law_private_ai as version 2." in texts(reply.text)
    assert '<input type="hidden" name="based_on" value="2">' in reply.text
    assert segment(seeded).version == 2


def test_an_unchanged_form_writes_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    law = segment(seeded)
    before = snapshot(seeded)

    reply = post(dashboard, session, f"/segments/{law.id}/edit", edit_fields(seeded))

    assert "Nothing changed, so no new version was saved." in texts(reply.text)
    assert snapshot(seeded) == before


def test_a_refusal_is_re_rendered_beside_its_field(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    law = segment(seeded)
    field = weight_field(law.definition, "law_expansion")  # Tier 2
    before = snapshot(seeded)

    reply = post(
        dashboard, session, f"/segments/{law.id}/edit", edit_fields(seeded, **{field: "60"})
    )

    assert reply.status == 200
    assert snapshot(seeded) == before
    html = reply.text
    assert "Nothing was saved. Correct the fields marked below, then save again." in texts(html)
    assert f'name="{field}" aria-describedby=' in html
    assert 'aria-invalid="true" value="60"' in html  # the submitted value is kept
    assert "must be below every Tier 1 weight" in html


def test_the_editing_rule_refuses_on_the_signal_types(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    law = segment(seeded)
    fields = edit_fields(seeded)
    for index, each in enumerate(law.definition.signal_types):
        if each.signal_class == "event":
            fields.append((f"signal_types[{index}].remove", "on"))

    reply = post(dashboard, session, f"/segments/{law.id}/edit", fields)

    assert EDITING_RULE in reply.text
    assert segment(seeded).version == 1


def test_a_stale_form_is_refused_in_the_notice(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    law = segment(seeded)
    field = weight_field(law.definition, "law_contact_form_only")
    post(dashboard, session, f"/segments/{law.id}/edit", edit_fields(seeded, **{field: "20"}))
    stale = edit_fields(seeded, **{field: "25"})
    stale = [(name, "1" if name == "based_on" else value) for name, value in stale]

    reply = post(dashboard, session, f"/segments/{law.id}/edit", stale)

    assert "Nothing was saved. This Segment changed since you opened it." in texts(reply.text)
    assert segment(seeded).version == 2


def test_create_stores_an_inert_segment(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    law = segment(seeded)
    fields = fields_of(law.definition, key=True)
    fields["key"] = ["accountants"]
    fields["name"] = ["Accountants"]
    for name in list(fields):
        if name.endswith("].key"):
            fields[name] = [f"acc_{fields[name][0]}"[:31]]

    reply = post(dashboard, session, "/segments/create", encoded(fields))

    assert reply.status == 200
    assert "Created accountants at version 1. It is inert until a Campaign uses it." in texts(
        reply.text
    )
    rows = re.findall(r'<tr class="segment-row-item">(.*?)</tr>', reply.text, re.DOTALL)
    assert texts(rows[0]) == "accountants Accountants 1 0 (inert) Versions"


def test_create_refuses_a_taken_key_beside_its_field(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    fields = fields_of(segment(seeded).definition, key=True)
    fields["key"] = [OPS]
    before = snapshot(seeded)

    reply = post(dashboard, session, "/segments/create", encoded(fields))

    assert snapshot(seeded) == before
    assert 'name="key" aria-describedby="segment-new-key-error" aria-invalid="true"' in reply.text
    assert "repeats an earlier segment key (ops_automation)" in reply.text


# The history --------------------------------------------------------------------------------------


def test_the_versions_page_lists_v1_to_v4_with_their_changes(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    law = segment(seeded)
    field = weight_field(law.definition, "law_contact_form_only")
    for value in ("20", "25"):
        post(dashboard, session, f"/segments/{law.id}/edit", edit_fields(seeded, **{field: value}))
    post(dashboard, session, f"/segments/{law.id}/edit", edit_fields(seeded, name="Law firms"))

    html = dashboard.get(f"/segments/{law.id}/versions", cookie=session[0]).text

    headings = re.findall(r'<h2 id="segment-version-(\d+)">', html)
    assert headings == ["1", "2", "3", "4"]
    sections = re.findall(r'<section class="segment-version"(.*?)</section>', html, re.DOTALL)
    assert "First stored version." in texts(sections[0])
    assert "Saved by seed" in texts(sections[0])
    assert texts(sections[1]).endswith(
        "Field Before After signal_types[law_contact_form_only].weight 15 20"
    )
    assert texts(sections[2]).endswith(
        "Field Before After signal_types[law_contact_form_only].weight 20 25"
    )
    assert 'name "Law-PrivateAI" "Law firms"' in texts(sections[3])
    assert "Version 4 (current)" in texts(sections[3])
    assert "Saved by operator" in texts(sections[3])


def test_a_segment_whose_first_stored_version_is_above_1_notes_it(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    law = segment(seeded)
    found_id, row_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV", "01ARZ3NDEKTSV4RRFFQ69G5FAW"
    with seeded.engine.begin() as connection:  # as the backfill stores a pre-11.5 Segment at v3
        [(text, at)] = connection.exec_driver_sql(
            "SELECT definition, updated_at FROM segments WHERE id = ?", (law.id,)
        ).all()
        connection.exec_driver_sql(
            "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
            "VALUES (?, 'accountants', ?, 3, ?, ?)",
            (found_id, text, at, at),
        )
        connection.exec_driver_sql(
            "INSERT INTO segment_versions (id, segment_id, version, definition, created_at, "
            "actor) VALUES (?, ?, 3, ?, ?, 'seed')",
            (row_id, found_id, text, at),
        )

    backfilled = dashboard.get(f"/segments/{found_id}/versions", cookie=session[0]).text
    fresh = dashboard.get(f"/segments/{law.id}/versions", cookie=session[0]).text

    assert "Versions before v3 were not kept." in texts(backfilled)
    assert re.findall(r'<h2 id="segment-version-(\d+)">', backfilled) == ["3"]
    assert "were not kept" not in fresh


# Re-score with the current definition -------------------------------------------------------------


def test_the_segment_rescore_answers_with_how_many_leads_it_queued(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    law = segment(seeded)

    reply = post(dashboard, session, f"/segments/{law.id}/rescore-current", [])

    assert reply.status == 200
    assert "No open Lead uses this Segment, so nothing was queued." in texts(reply.text)


def add_lead(units: UnitOfWorkFactory, segment_id: str, domain: str) -> str:
    """A new open Lead of the Segment ``segment_id``; its id."""
    clock = FrozenClock(DEFAULT_NOW)
    discovery = Discovery(clock, Pipeline(clock), queue_on(clock))
    identity = LeadIdentity("Smith Law LLP", segment_id, web_domain=domain, country_code="GB")

    @use_case
    def caller() -> str:
        with current().transaction():
            return discovery.upsert_lead(identity, LeadSource.CSV).lead_id

    with units.open():
        return caller()


def score_jobs(units: UnitOfWorkFactory) -> list[Any]:
    with units.engine.connect() as connection:
        return connection.exec_driver_sql(
            "SELECT idempotency_key, lane FROM jobs WHERE type = 'score_lead' ORDER BY rowid"
        ).all()


def test_the_segment_rescore_queues_each_open_lead_on_the_bulk_lane(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    law = segment(seeded)
    first = add_lead(seeded, law.id, "smith-law.co.uk")
    second = add_lead(seeded, law.id, "jones-law.co.uk")
    add_lead(seeded, segment(seeded, OPS).id, "ops.co.uk")  # another Segment's Lead

    reply = post(dashboard, session, f"/segments/{law.id}/rescore-current", [])

    assert reply.status == 200
    assert "Re-score with the current definition queued for 2 Leads." in texts(reply.text)
    assert sorted(score_jobs(seeded)) == sorted(
        [(f"score_lead:{first}:current", "bulk"), (f"score_lead:{second}:current", "bulk")]
    )


@pytest.mark.parametrize("segment_id", ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "not-a-ulid"])
def test_the_segment_rescore_of_an_unknown_segment_is_404_and_queues_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory, segment_id: str
) -> None:
    add_lead(seeded, segment(seeded).id, "smith-law.co.uk")

    reply = post(dashboard, session, f"/segments/{segment_id}/rescore-current", [])

    assert (reply.status, reply.text) == (404, "That Segment doesn't exist.")
    assert score_jobs(seeded) == []


# Refusals ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["create", "edit", "rescore"])
def test_a_post_without_the_csrf_token_writes_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory, which: str
) -> None:
    law = segment(seeded)
    path, fields = {
        "create": ("/segments/create", encoded(fields_of(law.definition, key=True))),
        "edit": (f"/segments/{law.id}/edit", edit_fields(seeded, name="Changed")),
        "rescore": (f"/segments/{law.id}/rescore-current", []),
    }[which]
    before = snapshot(seeded)

    reply = post(dashboard, session, path, fields, token=False)

    assert (reply.status, reply.text) == (403, SECURITY_CHECK_FAILED)
    assert snapshot(seeded) == before


def test_a_forged_csrf_token_writes_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    law = segment(seeded)
    before = snapshot(seeded)

    reply = post(
        dashboard,
        (session[0], "forged-token"),
        f"/segments/{law.id}/edit",
        edit_fields(seeded, name="Changed"),
    )

    assert reply.status == 403
    assert snapshot(seeded) == before


@pytest.mark.parametrize(
    "extra",
    [
        [("unknown", "x")],
        [("signal_types[100].key", "x")],
        [("signal_types[01].key", "x")],  # a leading zero: not the name of row 1
        [("disqualifiers[00].key", "x")],
        [("signal_types[0].bogus", "x")],
        [("signal_types[0].class", "maybe")],
        [("size_range.unit", "parsecs")],
        [("name", "again")],
    ],
)
def test_a_malformed_form_is_refused_with_400(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    seeded: UnitOfWorkFactory,
    extra: list[tuple[str, str]],
) -> None:
    law = segment(seeded)
    fields = edit_fields(seeded)
    names = {name for name, _ in extra}
    if names & {"signal_types[0].class", "size_range.unit"}:
        fields = [pair for pair in fields if pair[0] not in names]
    before = snapshot(seeded)

    reply = post(dashboard, session, f"/segments/{law.id}/edit", fields + extra)

    assert reply.status == 400
    assert snapshot(seeded) == before


@pytest.mark.parametrize("based_on", ["0", "01", "x", ""])
def test_a_bad_based_on_is_malformed(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory, based_on: str
) -> None:
    law = segment(seeded)
    fields = [
        (name, based_on if name == "based_on" else value) for name, value in edit_fields(seeded)
    ]

    assert post(dashboard, session, f"/segments/{law.id}/edit", fields).status == 400


def test_the_proposed_form_keeps_typed_numbers_and_raw_text() -> None:
    submitted = segments_page.SubmittedSegment(
        {
            "key": "x",
            "name": "X",
            "industry_keywords": "a\n\n b \n",
            "size_range.minimum": "5",
            "size_range.maximum": "lots",
            "size_range.unit": "staff",
            "decision_maker_roles": "CEO | Owner\nCFO",
            "discovery_queries.places": "accountants",
            "discovery_queries.registry.keywords": "",
            "discovery_queries.registry.sic_codes": "69201",
            "discovery_queries.job_titles": "",
        },
        (),
        (
            segments_page.SignalRow(0, {"key": "a", "weight": "60", "tier": "1"}, ("quote",)),
            segments_page.SignalRow(1, {"key": "gone"}, removed=True),
            segments_page.SignalRow(2, {}),
        ),
    )

    proposed = segments_page.proposed_form(submitted)

    assert proposed.form["industry_keywords"] == ["a", "b"]
    assert proposed.form["size_range"] == {"minimum": 5, "maximum": "lots", "unit": "staff"}
    assert proposed.form["decision_maker_roles"] == [["CEO", "Owner"], ["CFO"]]
    assert proposed.form["discovery_queries"] == {
        "places": ["accountants"],
        "registry": {"sic_codes": ["69201"]},
    }
    assert [each["key"] for each in proposed.form["signal_types"]] == ["a"]  # type: ignore[index]
    assert proposed.signal_rows == (0,)
    assert json.dumps(proposed.form)  # plain JSON-able data


def test_the_readme_documents_segment_editing() -> None:
    readme = (main.SEED_DIR.parent / "README.md").read_text(encoding="utf-8")

    assert "### Editing Segments" in readme
    assert "Segments are managed in the dashboard; seed/segments.yaml ignored" in readme
    assert "Re-score with the current definition" in readme.replace("\n", " ")
