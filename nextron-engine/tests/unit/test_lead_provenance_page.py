"""The Lead page's Provenance and Eligibility basis (Story 3.7; FR-51, AD-7, AD-13, NFR-3, UX-DR4,
UX-DR17) through ``Dashboard(units=...)`` on a temporary database at head with the real
subscriber registry and the shipped seeds, the HTML parsed into a tree (``tests/unit/
test_leads_page.py``'s ``Tree``): every page row of the I/O matrix.

- **Bases.** A US basis, a UK basis with its LIA, a later LIA (both bases, oldest first), and a
  Contact never enrolled ("Basis not yet recorded (recorded at enrollment)").
- **Provenance missing.** The records removed by raw SQL, or an email without its ``address``
  record: a red "Provenance missing" and the ``provenance_missing`` reason.
- **Facts.** A resolved US Contact (from the Lead's country, and from the office location), no
  Market (``cctld_outside_markets``), an unresolved Contact, unreadable Markets ("Unknown
  Market"), the notes line by line in record order.
- **Records.** Each Detail through ``safe_link`` (a ``javascript:`` detail plain, an ``https``
  one a link with ``rel="noopener noreferrer"``, escaped), an address's records as its Address
  Class history, the basis and Manual labels.
- **The page** keeps it all inside the ``hx-disable`` table, has no HTMX action and sends
  ``Cache-Control: no-store`` (``World.page`` asserts it). The builders' edge cases (an unknown
  stored basis, a NULL rule, a missing ``basis_recorded_at``) and the label tables.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from nextron import main
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.research import StoredContact, StoredProvenance
from nextron.app.queries.setup import SeededMarket
from nextron.app.setup.api import Campaigns, Setup, check_seed
from nextron.domain import seed_yaml
from nextron.domain.campaign import validate_campaigns
from nextron.domain.contact_market import MarketBasis, TzBasis
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.eligibility import LEGAL_FORM_LABELS, UK_BASIS, US_BASIS, Decision
from nextron.domain.geo import Location
from nextron.domain.markets import validate_markets
from nextron.domain.offers import validate_offers
from nextron.domain.segments import validate_segments
from nextron.domain.sequences import validate_sequences
from nextron.web import leads_page
from nextron.web.leads_page import MARKET_BASIS_LABELS, SOURCE_LABELS, TZ_BASIS_LABELS
from tests.unit.test_leads_page import (
    ORIGIN,
    RECORD_HEADER,
    T,
    Node,
    Tree,
    World,
    table_rows,
    world,  # noqa: F401 - the fixture, reused
)

US_WEB, UK_WEB = "acmelaw.com", "acmelaw.co.uk"
US_EMAIL, UK_EMAIL = "zelda.quill@acmelaw.com", "zelda.quill@acmelaw.co.uk"
NAME = "Zelda Quill"
LTD_BASIS = UK_BASIS.format(label=LEGAL_FORM_LABELS["ltd"])
US_CAMPAIGN, UK_CAMPAIGN = "law_private_ai_us", "law_private_ai_uk"
BASIS_NOT_RECORDED = "Basis not yet recorded (recorded at enrollment)"
US_LEAD_FACTS = [
    "Market",
    "United States (from the Lead's country; office location unknown)",
    "Timezone",
    "America/New_York (US state unknown: Eastern fallback with a narrowed Send Window)",
]


def shipped[R](name: str, validate: Callable[[object], R]) -> R:
    return validate(seed_yaml.parse((main.SEED_DIR / name).read_text(encoding="utf-8")))


def seed_all(world: World) -> None:
    """The shipped Segments, Markets, Sequences, Offers and Campaigns."""
    checked = check_seed(
        shipped(main.SEGMENTS_FILE_NAME, validate_segments),
        shipped(main.MARKETS_FILE_NAME, validate_markets),
        shipped(main.SEQUENCES_FILE_NAME, validate_sequences),
        shipped(main.OFFERS_FILE_NAME, validate_offers),
        shipped(main.CAMPAIGNS_FILE_NAME, validate_campaigns),
    )
    world.transact(lambda: Setup(world.clock).seed_sync(checked, world.clock.now()))


def campaign(world: World, key: str) -> str:
    with world.units.open(read_only=True):
        [found] = [c.id for c in setup_queries.campaigns() if c.key == key]
    return found


def record_lia(world: World, campaign_id: str, ref: str, day: date) -> None:
    with world.units.open():
        Campaigns(world.clock).record_lia(campaign_id, ref, day.isoformat())


def record_basis(
    world: World,
    contact_id: str,
    decision: Decision,
    campaign_id: str,
    lia: tuple[str, date] | None = None,
    at: datetime = T,
) -> str:
    ref, day = lia if lia is not None else (None, None)
    return world.transact(
        lambda: world.research.record_eligibility_basis(
            contact_id, decision, campaign_id, ref, day, at
        )
    )


def us_contact(world: World, lead: str) -> str:
    """A Contact found on the website, so its address is Published."""
    candidate = ContactCandidate(name=NAME, email=US_EMAIL)
    return world.add(lead, candidate, source=ProvenanceSource.WEBSITE)


def uk_contact(world: World, lead: str) -> str:
    candidate = ContactCandidate(name=NAME, email=UK_EMAIL)
    return world.add(lead, candidate, source=ProvenanceSource.WEBSITE)


def row_of(page: Tree, kind: str, index: int = 0) -> Node:
    return page.find_all("tr", class_=kind)[index]


def bases_of(page: Tree, index: int = 0) -> list[list[str]]:
    """Each recorded basis of a Contact as its lines (the basis, then any LIA line), or its "not
    yet recorded" line alone."""
    row = row_of(page, "eligibility-row", index)
    found = row.find_all("ul", class_="eligibility-bases")
    if not found:
        return [[row.find("p", class_="eligibility-basis").text]]
    return [
        [child for child in item.children if isinstance(child, str)]
        for item in found[0].find_all("li")
    ]


def reason_codes(page: Tree, index: int = 0) -> list[str | None]:
    items = row_of(page, "eligibility-row", index).find_all("li")
    return [item.attrs.get("data-reason") for item in items if "data-reason" in item.attrs]


def facts_of(page: Tree, index: int = 0) -> list[str]:
    facts = row_of(page, "provenance-row", index).find("dl", class_="provenance-facts")
    return [node.text for node in facts.walk() if node.tag in ("dt", "dd")]


def missing_of(page: Tree, index: int = 0) -> list[str]:
    row = row_of(page, "provenance-row", index)
    return [node.text for node in row.find_all("p", class_="provenance-missing")]


@pytest.fixture
def seeded(world: World) -> World:
    seed_all(world)
    return world


# The bases ----------------------------------------------------------------------------------------


def test_a_us_basis_shows_its_rule_and_time(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    contact = us_contact(seeded, lead)
    record_basis(seeded, contact, Decision(True, US_BASIS, ()), campaign(seeded, US_CAMPAIGN))

    page = seeded.page(f"/leads/{lead}")

    row = row_of(page, "eligibility-row")
    assert row.find("p", class_="eligibility-summary").text == (
        "Eligible: US: cold email allowed (CAN-SPAM)"
    )
    assert bases_of(page) == [
        ["Basis: US: cold email allowed (CAN-SPAM), recorded 2026-11-17 09:00 UTC"]
    ]
    assert not row.find_all("p", class_="eligibility-basis")
    records = table_rows(row_of(page, "provenance-row").find("table", class_="provenance-table"))
    assert records[0] == RECORD_HEADER
    # the basis is in the bases list, never a records-table row: only the sources are
    assert [record[:2] for record in records[1:]] == [
        ["Contact", "Website"],
        [f"Address {US_EMAIL}", "Website"],
    ]
    assert "Eligibility basis" not in [record[1] for record in records[1:]]


def test_a_uk_basis_shows_its_legitimate_interests_line(seeded: World) -> None:
    lead = seeded.lead("Acme Law", UK_WEB)
    contact = uk_contact(seeded, lead)
    uk = campaign(seeded, UK_CAMPAIGN)
    record_lia(seeded, uk, "R1", date(2026, 10, 1))
    at = datetime(2026, 11, 17, 14, 5, 59, tzinfo=UTC)
    record_basis(seeded, contact, Decision(True, LTD_BASIS, ()), uk, ("R1", date(2026, 10, 1)), at)

    page = seeded.page(f"/leads/{lead}")

    assert bases_of(page) == [
        [
            f"Basis: {LTD_BASIS}, recorded 2026-11-17 14:05 UTC",
            "Legitimate interests: LIA R1 dated 2026-10-01, recorded 2026-11-17 14:05 UTC",
        ]
    ]
    [item] = row_of(page, "eligibility-row").find("ul", class_="eligibility-bases").find_all("li")
    assert [node.tag for node in item.children if isinstance(node, Node)] == ["br"]
    # the live Decision still says why it isn't eligible today, next to the recorded basis
    assert row_of(page, "eligibility-row").find("p", class_="eligibility-summary").text == (
        "Not eligible"
    )


def test_a_later_lia_shows_both_bases_oldest_first(seeded: World) -> None:
    lead = seeded.lead("Acme Law", UK_WEB)
    contact = uk_contact(seeded, lead)
    uk = campaign(seeded, UK_CAMPAIGN)
    decision = Decision(True, LTD_BASIS, ())
    record_lia(seeded, uk, "R1", date(2026, 10, 1))
    record_basis(seeded, contact, decision, uk, ("R1", date(2026, 10, 1)), T)
    record_lia(seeded, uk, "R2", date(2026, 11, 2))
    record_basis(seeded, contact, decision, uk, ("R2", date(2026, 11, 2)), T + timedelta(hours=2))

    bases = bases_of(seeded.page(f"/leads/{lead}"))

    assert bases == [
        [
            f"Basis: {LTD_BASIS}, recorded 2026-11-17 09:00 UTC",
            "Legitimate interests: LIA R1 dated 2026-10-01, recorded 2026-11-17 09:00 UTC",
        ],
        [
            f"Basis: {LTD_BASIS}, recorded 2026-11-17 11:00 UTC",
            "Legitimate interests: LIA R2 dated 2026-11-02, recorded 2026-11-17 11:00 UTC",
        ],
    ]


def test_a_contact_never_enrolled_says_its_basis_isnt_recorded(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    us_contact(seeded, lead)

    page = seeded.page(f"/leads/{lead}")

    row = row_of(page, "eligibility-row")
    assert row.find("p", class_="eligibility-basis").text == BASIS_NOT_RECORDED
    assert not row.find_all("ul", class_="eligibility-bases")
    children = [node for node in row.find("td").children if isinstance(node, Node)]
    # next to the live Decision: the summary first, the basis line last
    assert children[0].attrs["class"] == "eligibility-summary eligible"
    assert children[-1].attrs["class"] == "eligibility-basis"


# Provenance missing -------------------------------------------------------------------------------


def test_a_contact_with_no_record_shows_provenance_missing_and_its_reason(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    contact = us_contact(seeded, lead)
    seeded.sql("DELETE FROM provenance_records WHERE contact_id = ?", (contact,))

    page = seeded.page(f"/leads/{lead}")

    assert missing_of(page) == ["Provenance missing"]
    assert not row_of(page, "provenance-row").find_all("table", class_="provenance-table")
    assert "No Provenance Record." not in page.text
    reasons = row_of(page, "eligibility-row").find_all("li")
    assert [(item.attrs.get("data-reason"), item.text) for item in reasons] == [
        (
            "provenance_missing",
            "Provenance missing: no Provenance Record shows where this Contact or its email "
            "address came from",
        )
    ]


def test_an_email_without_its_address_record_shows_provenance_missing(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    contact = us_contact(seeded, lead)
    seeded.sql(
        "DELETE FROM provenance_records WHERE contact_id = ? AND subject = 'address'", (contact,)
    )

    page = seeded.page(f"/leads/{lead}")

    assert missing_of(page) == ["Provenance missing"]
    records = table_rows(row_of(page, "provenance-row").find("table", class_="provenance-table"))
    assert [record[0] for record in records[1:]] == ["Contact"]
    assert reason_codes(page) == ["provenance_missing"]


def test_a_basis_only_contact_shows_provenance_missing_and_no_record_row(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    contact = us_contact(seeded, lead)
    record_basis(seeded, contact, Decision(True, US_BASIS, ()), campaign(seeded, US_CAMPAIGN))
    seeded.sql(
        "DELETE FROM provenance_records WHERE contact_id = ? AND source_kind != "
        "'eligibility_basis'",
        (contact,),
    )

    page = seeded.page(f"/leads/{lead}")

    assert missing_of(page) == ["Provenance missing"]
    assert not row_of(page, "provenance-row").find_all("table", class_="provenance-table")
    assert bases_of(page) == [
        ["Basis: US: cold email allowed (CAN-SPAM), recorded 2026-11-17 09:00 UTC"]
    ]


def test_a_contact_with_its_sources_shows_no_provenance_missing(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    us_contact(seeded, lead)
    seeded.add(lead, ContactCandidate(name="Ann Jones"), seconds=1)

    page = seeded.page(f"/leads/{lead}")

    assert missing_of(page, 0) == [] and missing_of(page, 1) == []
    assert not page.find_all("p", class_="provenance-missing")


# The facts ----------------------------------------------------------------------------------------


def test_a_resolved_us_contact_shows_its_market_timezone_and_notes(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    us_contact(seeded, lead)

    facts = facts_of(seeded.page(f"/leads/{lead}"))

    assert facts[:4] == US_LEAD_FACTS
    assert facts[4] == "Notes"
    assert facts[5:] == [
        "Market taken from the Lead's country; office location unknown",
        "Timezone falls back to America/New_York: the US state is unknown, so the Send Window "
        "narrows to 11:00-17:00",
    ]


def test_an_office_location_is_the_basis_of_both(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    office = Location("US", "CA")
    seeded.add(lead, ContactCandidate(name=NAME, email=US_EMAIL, office=office))

    facts = facts_of(seeded.page(f"/leads/{lead}"))

    assert facts == [
        "Market",
        "United States (from the office location)",
        "Timezone",
        "America/Los_Angeles (from the office location)",
        "Notes",
        "—",
    ]


def test_a_contact_without_a_market_says_why(seeded: World) -> None:
    lead = seeded.lead("Acme Law", UK_WEB)
    seeded.add(lead, ContactCandidate(name=NAME, email="zelda@acmelaw.de"))

    facts = facts_of(seeded.page(f"/leads/{lead}"))

    assert facts[:4] == [
        "Market",
        "No Market (the email is on a country-code domain outside the enabled Markets)",
        "Timezone",
        "None (no Market)",
    ]
    assert facts[5] == (
        "No Market: the email address is on the country-code domain .de, outside the enabled "
        "Markets"
    )


def test_an_unresolved_contact_says_so(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    contact = seeded.add(lead, ContactCandidate(name=NAME))
    seeded.sql(
        "UPDATE contacts SET market_id = NULL, tz = NULL, market_basis = NULL, tz_basis = NULL "
        "WHERE id = ?",
        (contact,),
    )

    facts = facts_of(seeded.page(f"/leads/{lead}"))

    assert facts[:4] == ["Market", "Not resolved yet", "Timezone", "Not resolved yet"]


def test_unreadable_markets_show_an_unknown_market(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    us_contact(seeded, lead)
    seeded.sql("UPDATE markets SET cctlds = 'not json' WHERE code = 'uk'")

    page = seeded.page(f"/leads/{lead}")

    assert facts_of(page)[:4] == [
        "Market",
        "Unknown Market (from the Lead's country; office location unknown)",
        "Timezone",
        "America/New_York (US state unknown: Eastern fallback with a narrowed Send Window)",
    ]
    assert reason_codes(page) == ["no_enabled_market"]


def test_the_notes_show_line_by_line_in_record_order(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    contact = us_contact(seeded, lead)
    [(contact_record,), (address_record,)] = seeded.sql(
        "SELECT id FROM provenance_records WHERE contact_id = ? ORDER BY subject DESC",
        (contact,),
    )
    notes = "UPDATE provenance_records SET notes = ? WHERE id = ?"
    seeded.sql(notes, ("first\nsecond", contact_record))
    seeded.sql(notes, ("<b>third</b>", address_record))

    page = seeded.page(f"/leads/{lead}")

    facts = facts_of(page)
    assert facts[facts.index("Notes") + 1 :] == ["first", "second", "<b>third</b>"]
    assert not page.find_all("b")


# The records --------------------------------------------------------------------------------------


def test_a_detail_renders_through_safe_link_inside_hx_disable(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    seeded.add(lead, ContactCandidate(name="Ann Jones"), detail="javascript:alert(1)")
    seeded.add(
        lead,
        ContactCandidate(name="Bo Smith"),
        source=ProvenanceSource.WEBSITE,
        detail="https://x.test/<b>",
        seconds=1,
    )

    reply = seeded.get(f"/leads/{lead}")
    page = Tree(reply.text)

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    first = row_of(page, "provenance-row", 0).find("table", class_="provenance-table")
    second = row_of(page, "provenance-row", 1).find("table", class_="provenance-table")
    assert table_rows(first)[1][2] == "javascript:alert(1)"
    assert not first.find_all("a")
    [link] = second.find_all("a")
    assert link.attrs == {"href": "https://x.test/<b>", "rel": "noopener noreferrer"}
    assert link.text == "https://x.test/<b>"
    assert "<b>" not in reply.text and "&lt;b&gt;" in reply.text
    assert not page.find_all("b")
    assert page.disabled("javascript:alert(1)") == [True]
    assert page.disabled("https://x.test/") == [True]
    assert not [a for a in page.find_all("a") if (a.attrs.get("href") or "").startswith("javas")]


def test_an_addresss_records_are_its_address_class_history(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    contact = seeded.add(lead, ContactCandidate(name=NAME, email=US_EMAIL))  # CSV: guessed
    seeded.sql(
        "INSERT INTO provenance_records (id, contact_id, subject, source_kind, source_detail, "
        "address, retrieved_at, address_class) VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0C1', ?, "
        "'address', 'finder', 'Hunter Email Verifier', ?, '2026-11-18T10:00:00.000000Z', "
        "'verified')",
        (contact, US_EMAIL),
    )

    page = seeded.page(f"/leads/{lead}")

    records = table_rows(row_of(page, "provenance-row").find("table", class_="provenance-table"))
    addresses = [record for record in records[1:] if record[0].startswith("Address")]
    assert addresses == [
        [f"Address {US_EMAIL}", "CSV import", ORIGIN, "2026-11-17", "guessed"],
        [f"Address {US_EMAIL}", "Email finder", "Hunter Email Verifier", "2026-11-18", "verified"],
    ]


def test_the_manual_source_reads_manual(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    seeded.add(lead, ContactCandidate(name=NAME), source=ProvenanceSource.OPERATOR, detail="call")

    records = table_rows(seeded.page(f"/leads/{lead}").find("table", class_="provenance-table"))

    assert records[1] == ["Contact", "Manual (entered by Mac)", "call", "2026-11-17", "—"]


def test_everything_sits_inside_hx_disable_with_no_htmx_action(seeded: World) -> None:
    lead = seeded.lead("Acme US", US_WEB, country="US")
    contact = us_contact(seeded, lead)
    record_basis(seeded, contact, Decision(True, US_BASIS, ()), campaign(seeded, US_CAMPAIGN))
    seeded.add(lead, ContactCandidate(name="Ann Jones"), seconds=1)
    seeded.sql(
        "DELETE FROM provenance_records WHERE contact_id = (SELECT id FROM contacts WHERE name = "
        "'Ann Jones')"
    )

    page = seeded.page(f"/leads/{lead}")

    table = page.find("table", class_="contact-table")
    found: set[str] = set()
    for tag, kind in (
        ("dl", "provenance-facts"),
        ("p", "provenance-missing"),
        ("ul", "eligibility-bases"),
        ("p", "eligibility-basis"),
    ):
        for node in page.find_all(tag, class_=kind):
            ancestors = list(node.ancestors())
            assert table in ancestors, kind
            # Story 2.10's panel puts hx-disable on each untrusted cell, not on the table
            assert any("hx-disable" in ancestor.attrs for ancestor in ancestors), kind
            found.add(kind)
    assert len(found) == 4  # not vacuous: each kind shows
    forms = page.find("main").find_all("form")
    # the only HTMX actions are Story 2.10's "Make primary" forms, Story 2.13's "Find email"
    # forms, Story 2.15's "Re-score" form and Story 3.15's "Export data (JSON)" form for each
    # Contact, outside every hx-disable
    exports = page.find("main").find_all("form", class_="contact-export")
    contacts = [row[0] for row in seeded.sql("SELECT id FROM contacts WHERE lead_id = ?", (lead,))]
    assert sorted(form.attrs["hx-post"] for form in exports) == sorted(
        f"/contacts/{contact_id}/export" for contact_id in contacts
    )
    rescore_paths = {f"/leads/{lead}/rescore", f"/leads/{lead}/rescore-current"}
    rescore = [form for form in forms if form.attrs.get("action") in rescore_paths]
    assert len(rescore) == 2  # Story 2.15's "Re-score" and Story 11.5's current-definition one
    find_email = [
        form for form in forms if form.attrs.get("action") == f"/leads/{lead}/find-email"
    ]
    # Story 9.1's Pipeline panel posts only to the Lead's stage, call and deal routes
    panel = page.find("main").find("section", id="lead-pipeline")
    staged = [form for form in forms if panel in list(form.ancestors())]
    assert staged and all(
        form.attrs["hx-post"] in {f"/leads/{lead}/{route}" for route in ("stage", "call", "deal")}
        for form in staged
    )
    primary = [form for form in forms if form not in exports + rescore + find_email + staged]
    assert primary and all(form.attrs["action"] == f"/leads/{lead}/primary" for form in primary)
    actions = [  # base.html's own hx- attributes sit outside main
        node
        for node in page.find("main").walk()
        if any(name.startswith("hx-") and name != "hx-disable" for name in node.attrs)
    ]
    assert actions == forms
    for form in forms:
        assert not any("hx-disable" in ancestor.attrs for ancestor in form.ancestors())


# The builders and the labels ----------------------------------------------------------------------


def stored_contact(**changes: object) -> StoredContact:
    fields: dict[str, object] = {
        "id": "01JD3QZ8X5V9K2M7N4P6R8T0D1",
        "lead_id": "01JD3QZ8X5V9K2M7N4P6R8T0D2",
        "name": NAME,
        "role": None,
        "email": None,
        "address_class": None,
        "linkedin_url": None,
        "is_primary": False,
        "primary_set_by": None,  # Story 2.10
        "tz": "Europe/London",
        "market_id": "m_uk",
        "market_basis": "office_location",
        "tz_basis": "office_location",
        "office_country": "GB",
        "office_region": None,
        "created_at": T,
        "updated_at": T,
    }
    fields.update(changes)
    return StoredContact(**fields)  # type: ignore[arg-type]


def stored_record(**changes: object) -> StoredProvenance:
    fields: dict[str, object] = {
        "id": "01JD3QZ8X5V9K2M7N4P6R8T0D3",
        "contact_id": "01JD3QZ8X5V9K2M7N4P6R8T0D1",
        "subject": "contact",
        "source_kind": "eligibility_basis",
        "source_detail": US_BASIS,
        "address": None,
        "retrieved_at": T,
        "address_class": None,
        "notes": None,
        "basis_rule": US_BASIS,
        "basis_campaign_id": "01JD3QZ8X5V9K2M7N4P6R8T0D4",
        "lia_ref": None,
        "lia_date": None,
        "basis_recorded_at": T + timedelta(minutes=1),
    }
    fields.update(changes)
    return StoredProvenance(**fields)  # type: ignore[arg-type]


def test_an_unknown_stored_basis_renders_as_its_own_text() -> None:
    names = {"m_uk": "United Kingdom"}

    facts = leads_page.market_facts(
        stored_contact(market_basis="by_carrier_pigeon", tz_basis="sundial"), (), names
    )

    assert facts == leads_page.MarketFacts(
        "United Kingdom (by_carrier_pigeon)", "Europe/London (sundial)", ()
    )


def test_a_market_no_stored_market_names_is_unknown() -> None:
    facts = leads_page.market_facts(stored_contact(market_id="m_gone"), (), {})

    assert facts.market == "Unknown Market (from the office location)"


def test_a_basis_row_falls_back_to_the_retrieval_time_and_a_dash() -> None:
    old = stored_record(basis_rule=None, basis_recorded_at=None)
    half = stored_record(lia_ref="R1", lia_date=None)
    utc_plus_one = datetime(2026, 11, 17, 11, 0, tzinfo=timezone(timedelta(hours=1)))

    assert leads_page.basis_row(old) == leads_page.BasisRow(
        "Basis: —, recorded 2026-11-17 09:00 UTC", None
    )
    assert leads_page.basis_row(half).lia is None  # an LIA needs both its reference and date
    assert leads_page.moment(utc_plus_one) == "2026-11-17 10:00 UTC"


def test_basis_rows_are_oldest_first_and_only_basis_records() -> None:
    later = stored_record(
        id="01JD3QZ8X5V9K2M7N4P6R8T0D9", basis_recorded_at=T + timedelta(days=1)
    )
    earlier = stored_record(id="01JD3QZ8X5V9K2M7N4P6R8T0D8", basis_recorded_at=T)
    source = stored_record(source_kind="csv", basis_rule=None, basis_recorded_at=None)

    rows = leads_page.basis_rows([later, source, earlier])

    assert [row.basis for row in rows] == [
        f"Basis: {US_BASIS}, recorded 2026-11-17 09:00 UTC",
        f"Basis: {US_BASIS}, recorded 2026-11-18 09:00 UTC",
    ]


def test_every_basis_and_source_has_its_label() -> None:
    assert list(MARKET_BASIS_LABELS) == list(MarketBasis)
    assert list(TZ_BASIS_LABELS) == list(TzBasis)
    assert MARKET_BASIS_LABELS[MarketBasis.LEAD_COUNTRY] == (
        "from the Lead's country; office location unknown"
    )
    assert TZ_BASIS_LABELS[TzBasis.US_UNKNOWN_FALLBACK] == (
        "US state unknown: Eastern fallback with a narrowed Send Window"
    )
    assert SOURCE_LABELS[ProvenanceSource.ELIGIBILITY_BASIS] == "Eligibility basis"
    assert SOURCE_LABELS[ProvenanceSource.OPERATOR] == "Manual (entered by Mac)"
    assert leads_page.market_basis_label("x") == "x" and leads_page.tz_basis_label("y") == "y"


def test_market_names_are_by_id() -> None:
    [us, *_] = [m for m in shipped(main.MARKETS_FILE_NAME, validate_markets) if m.code == "us"]

    names = leads_page.market_names([SeededMarket("m_us", "us", 1, us)])

    assert names == {"m_us": "United States"}
