"""The export of everything held on a Contact (``nextron.domain.contact_export``, FR-52, AD-17,
Story 3.15), pure: ``COVERAGE``, ``EXTRA_SECTIONS`` and ``SECTIONS``; the mention terms (the
3-character name floor, whole words, case, any whitespace, the whole address, the tombstone and
a hashed email); ``mentions``; ``excerpts`` (whole short lines, the 300-character window around
a long line's match, the 20 cap, a match over a line break, one excerpt per line); the Address
Class history; the suppression hashes (every gate key, Provenance addresses, stored hashes,
never a tombstone); ``assemble`` (every declared section, empty arrays, an undeclared owner or
section, the arguments); ``render`` (datetimes in UTC, dates, the refused types) and
``filename``."""

import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType

import pytest

from nextron.domain.contact_export import (
    COVERAGE,
    EXTRA_SECTIONS,
    MAX_EXCERPT_LENGTH,
    MAX_EXCERPTS,
    SCHEMA_VERSION,
    SECTIONS,
    ContactExport,
    Exported,
    NotAboutAContact,
    address_history,
    assemble,
    excerpts,
    export_hashes,
    filename,
    mention_terms,
    mentions,
    render,
)
from nextron.domain.contactability import ContactFacts, LeadFacts, key_hash, lookup_keys
from nextron.domain.erasure import REDACTED

CONTACT_ID = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
EMAIL = "jane.doe@firm.co.uk"
TERMS = mention_terms("Jane Doe", EMAIL)


# Coverage ---------------------------------------------------------------------------------------


def test_the_coverage_declares_every_section_once() -> None:
    assert COVERAGE["crawled_pages"] == Exported(
        "research", "pages", shown_as=MappingProxyType({"text": "text_excerpts"})
    )
    suppression = COVERAGE["suppression_entries"]
    assert isinstance(suppression, Exported) and set(suppression.withheld) == {"value_hash"}
    assert isinstance(COVERAGE["settings"], NotAboutAContact)
    assert isinstance(COVERAGE, MappingProxyType)
    assert EXTRA_SECTIONS == (
        ("research", "addresses"),
        ("research", "signals"),
        ("event_log", "contact_history"),
    )
    assert dict(SECTIONS) == {
        "research": (
            "contact",
            "provenance_records",
            "evidence",
            "pages",
            "contact_methods",
            "job_postings",  # Story 2.11
            "addresses",
            "signals",
        ),
        "compliance": ("suppression", "requests", "erasure_receipts"),  # Story 3.14
        "notify": ("alerts", "notifications"),
        "playbook": ("override_cases",),  # Story 4.6
        "sequencing": ("enrollments",),  # Story 4.8
        "drafting": ("draft_revisions", "grounding_results"),  # Stories 4.12 and 4.13
        "pipeline": ("call_bookings", "lead_notes", "channel_permissions"),  # Stories 9.1, 10.7
        "discovery": ("signal_inbox_items",),  # Story 2.16
        "sending": ("outbound_messages",),  # Story 5.2
        "event_log": ("contact_history",),
    }
    declared = [(owner, section) for owner, sections in SECTIONS.items() for section in sections]
    assert len(declared) == len(set(declared))


# Mention terms ----------------------------------------------------------------------------------


def labels(name: str | None, email: str | None) -> list[str]:
    return [term.label for term in mention_terms(name, email)]


def test_a_name_and_an_address_are_terms() -> None:
    assert labels("Jane Doe", EMAIL) == ["name", "email"]
    assert labels("Bob", None) == ["name"]
    assert labels(None, EMAIL) == ["email"]
    assert labels(None, None) == []


@pytest.mark.parametrize("name", ["Al", " J ", "", "   ", "\tJo\n", "A B", "J o"])
def test_a_name_under_three_characters_besides_whitespace_is_no_term(name: str) -> None:
    assert labels(name, None) == []


@pytest.mark.parametrize(
    "value",
    [REDACTED, f"  {REDACTED} ", key_hash("address", EMAIL), "no-at-sign.co.uk"],
)
def test_a_tombstone_a_hash_or_a_value_without_an_at_is_no_email_term(value: str) -> None:
    assert labels(None, value) == []


def test_a_tombstone_is_no_name_term_but_three_characters_are() -> None:
    assert labels(REDACTED, None) == []
    assert labels("A B C", None) == ["name"]
    assert labels("Bob", None) == ["name"]


def test_the_terms_hide_their_patterns() -> None:
    assert "Jane" not in repr(TERMS)
    assert "firm" not in repr(TERMS)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Meet Jane Doe, our partner.", ("name",)),
        ("JANE DOE leads the team", ("name",)),
        ("Jane\n  Doe\tleads", ("name",)),
        ("Jane Doer leads", ()),
        ("MaryJane Doe leads", ()),
        ("Jane Doe_2 leads", ()),
        ("Jane Doe's team", ("name",)),
        ("(Jane Doe)", ("name",)),
        ("Write to JANE.DOE@FIRM.CO.UK today", ("email",)),
        ("Write to jane.doe@firm.co.uk.", ("email",)),
        ("<jane.doe@firm.co.uk>", ("email",)),
        ("Write to xjane.doe@firm.co.uk", ()),
        ("Write to jane.doe@firm.co.uk.au", ()),
        ("Write to jane.doe@firm.co.uk-mail.com", ()),
        ("Write to mary+jane.doe@firm.co.uk", ()),
        ("Jane Doe, jane.doe@firm.co.uk", ("name", "email")),
        ("Nobody here", ()),
    ],
)
def test_mentions_match_whole_words_and_whole_addresses(
    text: str, expected: tuple[str, ...]
) -> None:
    assert mentions(text, TERMS) == expected


def test_each_label_is_given_once_in_term_order() -> None:
    terms = (
        *mention_terms("Jane Doe", EMAIL),
        *mention_terms(None, "jane@oldfirm.co.uk"),
        *mention_terms(None, "j.doe@older.co.uk"),
    )

    assert [term.label for term in terms] == ["name", "email", "email", "email"]
    assert mentions("jane@oldfirm.co.uk and j.doe@older.co.uk", terms) == ("email",)
    assert mentions("j.doe@older.co.uk, Jane Doe", terms) == ("name", "email")


def test_mentions_of_no_text_or_no_terms_are_empty() -> None:
    assert mentions(None, TERMS) == ()
    assert mentions("Jane Doe", ()) == ()
    assert mentions("[erased] [erased]", mention_terms(REDACTED, REDACTED)) == ()


def test_a_name_is_matched_literally() -> None:
    terms = mention_terms("J.R. (Bob) Smith+", None)

    assert mentions("call J.R. (Bob) Smith+ now", terms) == ("name",)
    assert mentions("call JxRx (Bob) Smith+ now", terms) == ()


# Excerpts ---------------------------------------------------------------------------------------


def test_each_line_that_mentions_the_contact_is_an_excerpt() -> None:
    text = (
        "Our team\n"
        "  Jane Doe, Partner  \r\n"
        "Bob Smith, Associate\n"
        "Email jane.doe@firm.co.uk for advice\n"
        "Jane Doe and jane.doe@firm.co.uk again\n"
    )

    assert excerpts(text, TERMS) == (
        "Jane Doe, Partner",
        "Email jane.doe@firm.co.uk for advice",
        "Jane Doe and jane.doe@firm.co.uk again",
    )


def test_a_match_over_a_line_break_gives_the_lines_it_covers() -> None:
    assert excerpts("Intro\nteam: Jane\nDoe, partner\nOutro", TERMS) == (
        "team: Jane\nDoe, partner",
    )


def test_a_match_inside_lines_already_given_is_skipped() -> None:
    text = "Intro\nteam: Jane\nDoe, write to jane.doe@firm.co.uk\nJane Doe again\nOutro"

    assert excerpts(text, TERMS) == (
        "team: Jane\nDoe, write to jane.doe@firm.co.uk",
        "Jane Doe again",
    )


def test_a_long_line_gives_the_300_characters_around_its_first_match() -> None:
    before, after = "a" * 1000, "b" * 1000
    text = f"{before} Jane Doe {after} Jane Doe"

    [excerpt] = excerpts(text, TERMS)

    assert len(excerpt) == MAX_EXCERPT_LENGTH == 300
    assert "Jane Doe" in excerpt
    start = excerpt.index("Jane Doe")
    assert abs(start - (MAX_EXCERPT_LENGTH - len("Jane Doe") - start)) <= 1  # centred


def test_a_match_near_a_long_lines_start_or_end_keeps_300_characters() -> None:
    [head] = excerpts("Jane Doe " + "x" * 600, TERMS)
    [tail] = excerpts("y" * 600 + " Jane Doe", TERMS)

    assert head.startswith("Jane Doe") and len(head) == 300
    assert tail.endswith("Jane Doe") and len(tail) == 300


def test_a_line_of_exactly_300_characters_is_kept_whole() -> None:
    line = "Jane Doe " + "z" * (300 - len("Jane Doe "))

    assert excerpts(line, TERMS) == (line,)


def test_at_most_20_excerpts_are_kept() -> None:
    text = "\n".join(f"line {n}: Jane Doe" for n in range(30))

    found = excerpts(text, TERMS)

    assert len(found) == MAX_EXCERPTS == 20
    assert found[0] == "line 0: Jane Doe" and found[-1] == "line 19: Jane Doe"


def test_no_text_no_terms_or_no_mention_gives_no_excerpt() -> None:
    assert excerpts(None, TERMS) == ()
    assert excerpts("Jane Doe", ()) == ()
    assert excerpts("Bob Smith only", TERMS) == ()
    assert excerpts("[erased]", mention_terms(REDACTED, key_hash("address", EMAIL))) == ()


# The Address Class history ----------------------------------------------------------------------


def record(subject: str, address: str | None, address_class: str | None, at: datetime) -> dict:
    return {
        "id": "x",
        "subject": subject,
        "address": address,
        "address_class": address_class,
        "source_kind": "csv",
        "retrieved_at": at,
        "notes": "kept out",
    }


def test_each_address_lists_its_classes_in_order() -> None:
    records = [
        record("contact", None, None, T0),
        record("address", "old@firm.co.uk", "guessed", T0),
        record("address", EMAIL, "published", T0 + timedelta(days=1)),
        record("address", "old@firm.co.uk", "invalid", T0 + timedelta(days=2)),
    ]

    assert address_history(records, EMAIL) == (
        {
            "address": "old@firm.co.uk",
            "current": False,
            "history": [
                {"address_class": "guessed", "source_kind": "csv", "retrieved_at": T0},
                {
                    "address_class": "invalid",
                    "source_kind": "csv",
                    "retrieved_at": T0 + timedelta(days=2),
                },
            ],
        },
        {
            "address": EMAIL,
            "current": True,
            "history": [
                {
                    "address_class": "published",
                    "source_kind": "csv",
                    "retrieved_at": T0 + timedelta(days=1),
                }
            ],
        },
    )


def test_no_address_record_gives_no_address() -> None:
    assert address_history([record("contact", None, None, T0)], None) == ()
    assert address_history([], EMAIL) == ()


# Suppression hashes -----------------------------------------------------------------------------


def test_the_hashes_cover_every_gate_key_and_each_provenance_address() -> None:
    gate = lookup_keys(
        ContactFacts(
            LeadFacts("firm.co.uk"),
            email=EMAIL,
            name="Jane Doe",
            linkedin_url="https://www.linkedin.com/in/jane-doe",
        )
    )

    found = export_hashes(
        email=EMAIL,
        name="Jane Doe",
        linkedin_url="https://www.linkedin.com/in/jane-doe",
        web_domain="firm.co.uk",
        addresses=["old@firm.co.uk", EMAIL, None],
    )

    assert found == {key.value_hash for key in gate} | {key_hash("address", "old@firm.co.uk")}
    assert key_hash("domain", "firm.co.uk") in found  # the firm's domain blocks the person too
    assert key_hash("person", ("Jane Doe", "firm.co.uk")) in found
    assert key_hash("profile", "https://www.linkedin.com/in/jane-doe") in found


def test_an_erased_contacts_hashes_are_its_stored_hashes_and_never_a_tombstone_key() -> None:
    address = key_hash("address", EMAIL)
    old = key_hash("address", "old@firm.co.uk")

    found = export_hashes(
        email=address,
        name=REDACTED,
        linkedin_url=None,
        web_domain="firm.co.uk",
        addresses=[old, address],
    )

    assert found == {address, old, key_hash("domain", "firm.co.uk")}
    assert key_hash("person", (REDACTED, "firm.co.uk")) not in found


def test_values_that_cant_be_normalised_give_no_key() -> None:
    assert export_hashes(
        email="not an address", name=None, linkedin_url="not a url", web_domain=None
    ) == frozenset()


# Assemble ---------------------------------------------------------------------------------------


def test_every_declared_section_is_an_array_and_none_is_omitted() -> None:
    document = assemble(CONTACT_ID, T0, {"research": {"contact": ({"id": CONTACT_ID},)}})

    assert document == {
        "schema_version": SCHEMA_VERSION,
        "generated_at": T0,
        "contact_id": CONTACT_ID,
        "research": {
            "contact": [{"id": CONTACT_ID}],
            "provenance_records": [],
            "evidence": [],
            "pages": [],
            "contact_methods": [],
            "job_postings": [],
            "addresses": [],
            "signals": [],
        },
        "compliance": {"suppression": [], "requests": [], "erasure_receipts": []},
        "notify": {"alerts": [], "notifications": []},
        "playbook": {"override_cases": []},
        "sequencing": {"enrollments": []},
        "drafting": {"draft_revisions": [], "grounding_results": []},
        "pipeline": {"call_bookings": [], "lead_notes": [], "channel_permissions": []},
        "discovery": {"signal_inbox_items": []},
        "sending": {"outbound_messages": []},
        "event_log": {"contact_history": []},
    }
    assert SCHEMA_VERSION == 1
    assert list(document) == [
        "schema_version",
        "generated_at",
        "contact_id",
        "research",
        "compliance",
        "notify",
        "playbook",
        "sequencing",
        "drafting",
        "pipeline",
        "discovery",
        "sending",
        "event_log",
    ]


def test_assemble_copies_each_row() -> None:
    row = {"id": CONTACT_ID}

    document = assemble(CONTACT_ID, T0, {"research": {"contact": [row]}})
    row["id"] = "changed"

    assert document["research"]["contact"] == [{"id": CONTACT_ID}]  # type: ignore[index]


@pytest.mark.parametrize(
    "parts",
    [
        {"research": {"page_text": ()}},
        {"jobqueue": {"jobs": ()}},
        {"compliance": {"contact": ()}},
        {"event_log": {"events": ()}},
    ],
)
def test_an_undeclared_owner_or_section_is_refused(parts: dict) -> None:
    with pytest.raises(ValueError, match="declare"):
        assemble(CONTACT_ID, T0, parts)


def test_assemble_checks_its_arguments() -> None:
    with pytest.raises(ValueError):
        assemble("not-a-ulid", T0, {})
    with pytest.raises(TypeError):
        assemble(CONTACT_ID, T0.date(), {})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        assemble(CONTACT_ID, T0.replace(tzinfo=None), {})


# Render and the file name -----------------------------------------------------------------------


def test_render_writes_indented_utf8_json_with_utc_times_and_iso_dates() -> None:
    plus_two = timezone(timedelta(hours=2))
    document = {
        "generated_at": datetime(2026, 11, 18, 11, 0, 0, 5, tzinfo=plus_two),
        "day": date(2026, 11, 18),
        "name": "Zoë Ångström",
        "rows": [{"n": 1, "flag": True, "none": None, "items": ("a", "b")}],
    }

    body = render(document)

    assert isinstance(body, bytes)
    text = body.decode("utf-8")
    assert "Zoë Ångström" in text  # not \\u-escaped
    assert '\n  "day": "2026-11-18"' in text  # indented by 2
    assert json.loads(text) == {
        "generated_at": "2026-11-18T09:00:00.000005Z",
        "day": "2026-11-18",
        "name": "Zoë Ångström",
        "rows": [{"n": 1, "flag": True, "none": None, "items": ["a", "b"]}],
    }


@pytest.mark.parametrize(
    "value", [b"bytes", {1, 2}, Decimal("1.5"), object(), MappingProxyType({})]
)
def test_render_refuses_any_other_type(value: object) -> None:
    with pytest.raises(TypeError):
        render({"value": value})


def test_render_refuses_a_naive_datetime_and_a_nan() -> None:
    with pytest.raises(ValueError):
        render({"at": datetime(2026, 11, 18, 9, 0)})
    with pytest.raises(ValueError):
        render({"n": float("nan")})


def test_the_file_name_is_the_contacts_ulid() -> None:
    assert filename(CONTACT_ID) == f"contact-{CONTACT_ID}-export.json"
    with pytest.raises(ValueError):
        filename("../../etc/passwd")


def test_an_export_hides_its_body() -> None:
    export = ContactExport("contact-x-export.json", b'{"name": "Jane Doe"}')

    assert "Jane" not in repr(export)
