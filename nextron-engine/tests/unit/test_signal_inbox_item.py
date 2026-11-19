"""The SignalInboxItem machine and the Signal Inbox's pure rules (Story 2.16)."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from nextron.domain import seed_yaml
from nextron.domain.machines import IllegalTransition, Transition
from nextron.domain.segments import SegmentDef, validate_segments
from nextron.domain.signal_inbox_item import (
    AUTHOR_INVALID,
    COMPANY_INVALID,
    COMPANY_TOO_LONG,
    CREATION,
    FLAG_LABELS,
    NO_ITEMS,
    NO_ITEMS_FIELD,
    TEXT_REQUIRED,
    TEXT_TOO_LONG,
    TRANSITIONS,
    URL_INVALID,
    URL_REQUIRED,
    URL_TOO_LONG,
    AcceptedParse,
    FlagReason,
    ItemCause,
    ItemEvent,
    ItemFacts,
    ItemsRefused,
    ItemStatus,
    SubmittedItem,
    accept_parse,
    check_items,
    lead_name,
    transition,
    domain_occurs,
    url_never_fetched,
    website_domain,
)

SEGMENTS = Path(__file__).resolve().parents[2] / "seed" / "segments.yaml"
SUBMITTED_AT = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
TEXT = (
    "Dana Cole, COO at Northgate Supply (northgate-supply.co.uk)\n"
    "On 3 November 2026 we hit the limit: two of my team re-key forty PDF purchase orders "
    "into our ERP by hand every morning."
)
QUOTE = "two of my team re-key forty PDF purchase orders into our ERP by hand every morning."


@pytest.fixture(scope="module")
def segments() -> tuple[SegmentDef, ...]:
    return validate_segments(seed_yaml.parse(SEGMENTS.read_text(encoding="utf-8")))


def answer(**overrides: object) -> dict[str, object]:
    found: dict[str, object] = {
        "segment": "ops_automation",
        "signal_type": "ops_rekeying_post",
        "website": "northgate-supply.co.uk",
        "author_name": "Dana Cole",
        "author_role": "COO",
        "event_date": "2026-11-03",
        "quote": QUOTE,
    }
    found.update(overrides)
    return found


def accept(segments: tuple[SegmentDef, ...], item: ItemFacts | None = None, **overrides: object):
    return accept_parse(answer(**overrides), item or ItemFacts(TEXT), segments, SUBMITTED_AT)


# The machine ------------------------------------------------------------------------------------


def test_the_transition_table_is_the_specs() -> None:
    assert dict(TRANSITIONS) == {
        (ItemStatus.PENDING, ItemEvent.PROCESS): Transition(
            ItemStatus.PROCESSED, ItemCause.PARSED
        ),
        (ItemStatus.PENDING, ItemEvent.FLAG): Transition(ItemStatus.FLAGGED, ItemCause.FLAGGED),
        (ItemStatus.FLAGGED, ItemEvent.REPROCESS): Transition(
            ItemStatus.PROCESSED, ItemCause.REPROCESSED_BY_OPERATOR
        ),
    }
    assert CREATION == Transition(ItemStatus.PENDING, ItemCause.SUBMITTED)


@pytest.mark.parametrize(
    ("status", "event"),
    [
        (ItemStatus.PROCESSED, ItemEvent.PROCESS),
        (ItemStatus.PROCESSED, ItemEvent.FLAG),
        (ItemStatus.PROCESSED, ItemEvent.REPROCESS),
        (ItemStatus.FLAGGED, ItemEvent.PROCESS),
        (ItemStatus.FLAGGED, ItemEvent.FLAG),
        (ItemStatus.PENDING, ItemEvent.REPROCESS),
    ],
)
def test_every_other_move_is_illegal(status: ItemStatus, event: ItemEvent) -> None:
    with pytest.raises(IllegalTransition, match="signal_inbox_item"):
        transition(status, event)


def test_an_unhashable_argument_is_illegal() -> None:
    with pytest.raises(IllegalTransition):
        transition([], ItemEvent.FLAG)  # type: ignore[arg-type]


def test_every_flag_reason_says_what_is_missing() -> None:
    assert set(FLAG_LABELS) == set(FlagReason)
    assert FLAG_LABELS[FlagReason.ORGANIZATION_SUPPRESSED] == (
        "Organization is on the Suppression List"
    )


# The form ---------------------------------------------------------------------------------------


def row(**fields: str) -> dict[str, str]:
    return {"url": "", "text": "", "author": "", "company": "", **fields}


def test_blank_fieldsets_are_ignored_and_fields_are_stripped() -> None:
    items = check_items(
        [
            row(),
            row(url=" https://www.linkedin.com/posts/x ", text=TEXT, author=" Dana ", company=" "),
            row(),
        ]
    )

    assert items == (SubmittedItem("https://www.linkedin.com/posts/x", TEXT, "Dana", None),)


def test_no_item_at_all_is_refused() -> None:
    with pytest.raises(ItemsRefused) as refused:
        check_items([row(), row(text="   ", author="Dana")])

    assert dict(refused.value.errors) == {"text_2": TEXT_REQUIRED, "url_2": URL_REQUIRED}
    with pytest.raises(ItemsRefused) as empty:
        check_items([row()])
    assert dict(empty.value.errors) == {NO_ITEMS_FIELD: NO_ITEMS}


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("", URL_REQUIRED),
        ("example.com/post", URL_INVALID),
        ("ftp://example.com/x", URL_INVALID),
        ("https://", URL_INVALID),
        ("https://exa mple.com", URL_INVALID),
        ("https://example.com/​x", URL_INVALID),
        ("https://example.com/" + "a" * 2049, URL_TOO_LONG),
    ],
)
def test_a_bad_url_refuses_the_whole_submission(url: str, reason: str) -> None:
    with pytest.raises(ItemsRefused) as refused:
        check_items([row(url="https://ok.example.com/1", text="fine"), row(url=url, text="x")])

    assert dict(refused.value.errors) == {"url_2": reason}


def test_the_text_and_the_optional_fields_have_limits() -> None:
    assert check_items([row(url="https://a.example.com", text="x" * 10_000)])
    with pytest.raises(ItemsRefused) as refused:
        check_items(
            [
                row(
                    url="https://a.example.com",
                    text="x" * 10_001,
                    author="a" * 201,
                    company="c" * 201,
                )
            ]
        )
    assert refused.value.errors["text_1"] == TEXT_TOO_LONG
    assert refused.value.errors["company_1"] == COMPANY_TOO_LONG
    assert "author_1" in refused.value.errors


@pytest.mark.parametrize("character", ["\x07", "\u200b", "\u2028", "\u2029", "\u202e"])
def test_a_control_or_format_character_in_the_author_or_company_is_refused(
    character: str,
) -> None:
    with pytest.raises(ItemsRefused) as refused:
        check_items(
            [
                row(
                    url="https://a.example.com",
                    text="A post",
                    author=f"Dana{character}Cole",
                    company=f"Acme{character}Ltd",
                )
            ]
        )

    assert dict(refused.value.errors) == {
        "author_1": AUTHOR_INVALID,
        "company_1": COMPANY_INVALID,
    }


def test_more_than_five_fieldsets_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="at most 5"):
        check_items([row()] * 6)


@pytest.mark.parametrize(
    ("url", "denied"),
    [
        ("https://www.linkedin.com/posts/dana-cole-123", True),
        ("https://www.instagram.com/p/abc", True),
        ("https://uk.indeed.com/viewjob?jk=1", True),
        ("https://northgate-supply.co.uk/news/erp", False),
    ],
)
def test_a_denylisted_url_is_marked_never_fetched(url: str, denied: bool) -> None:
    assert url_never_fetched(url) is denied


# The parse --------------------------------------------------------------------------------------


def test_a_full_answer_is_accepted(segments: tuple[SegmentDef, ...]) -> None:
    accepted = accept(segments)

    assert accepted == AcceptedParse(
        segment="ops_automation",
        signal_type="ops_rekeying_post",
        website="northgate-supply.co.uk",
        author_name="Dana Cole",
        author_role="COO",
        event_date=date(2026, 11, 3),
        quote=QUOTE,
    )
    assert accepted.flag is None


def test_an_unseeded_segment_flags_segment_unknown(segments: tuple[SegmentDef, ...]) -> None:
    accepted = accept(segments, segment="ops_invented")

    assert (accepted.segment, accepted.signal_type) == (None, None)
    assert accepted.flag is FlagReason.SEGMENT_UNKNOWN
    assert accepted.quote == QUOTE  # the rest still applies


def test_a_signal_type_must_be_the_segments_and_an_inbox_source(
    segments: tuple[SegmentDef, ...],
) -> None:
    other_segment = accept(segments, signal_type="law_ai_confidentiality_post")
    not_inbox = [
        each.key
        for segment in segments
        if segment.key == "ops_automation"
        for each in segment.signal_types
        if "signal_inbox" not in each.sources
    ]

    assert other_segment.signal_type is None
    assert not_inbox, "the shipped Ops-Automation Segment has a type the inbox can't give"
    assert accept(segments, signal_type=not_inbox[0]).signal_type is None


@pytest.mark.parametrize(
    "website",
    [
        "otherfirm.co.uk",  # not in the text or company
        "linkedin.com",  # denied
        "https://www.linkedin.com/company/northgate",
        "not a domain",
        "co.uk",  # a public suffix
    ],
)
def test_a_website_must_appear_and_be_allowed(
    segments: tuple[SegmentDef, ...], website: str
) -> None:
    accepted = accept(segments, website=website)

    assert accepted.website is None
    assert accepted.flag is FlagReason.ORGANIZATION_UNKNOWN


def test_a_website_in_the_company_field_or_as_a_url_is_its_registrable_domain(
    segments: tuple[SegmentDef, ...],
) -> None:
    item = ItemFacts("We re-key orders by hand.", company="Acme Ltd, ACME-Widgets.com")

    assert website_domain("https://www.acme-widgets.com/about", item) == "acme-widgets.com"


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Visit acme.com today", True),
        ("Visit www.acme.com today", True),
        ("See https://acme.com/x for more.", True),
        ("Email us at hello@acme.com.", True),
        ("ACME.COM", True),
        ("Visit acme.co today", False),
        ("Visit acme.com-shop.net", False),
        ("Visit myacme.com", False),
        ("Visit my-acme.com", False),
        ("Visit acme.company.io", False),
        ("Visit acme.com.au", False),
        ("Visit shop.acme.com.", True),
    ],
)
def test_a_domain_occurs_only_as_whole_labels(text: str, found: bool) -> None:
    assert domain_occurs("acme.com", text) is found


def test_a_website_must_occur_with_label_boundaries() -> None:
    assert website_domain("acme.co", ItemFacts("We are acme.com, re-keying orders.")) is None
    assert website_domain("me.com", ItemFacts("We are acme.com, re-keying orders.")) is None
    assert website_domain("acme.com", ItemFacts("Our site: www.acme.com")) == "acme.com"
    assert website_domain("acme.com", ItemFacts("See https://acme.com/x")) == "acme.com"
    assert website_domain("acme.com", ItemFacts("x", company="https://acme.com/x")) == "acme.com"


def test_a_shared_platform_is_never_the_organization(segments: tuple[SegmentDef, ...]) -> None:
    item = ItemFacts("Read my post at yelp.com about re-keying.")

    assert website_domain("yelp.com", item) is None


def test_the_author_fields_must_appear(segments: tuple[SegmentDef, ...]) -> None:
    item = ItemFacts(TEXT, author="Dana  Cole")
    named_in_author = accept(segments, item, author_name="dana cole", author_role="CFO")

    assert named_in_author.author_name == "dana cole"
    assert named_in_author.author_role is None  # CFO appears nowhere


def test_an_event_date_must_be_stated_and_not_in_the_future(
    segments: tuple[SegmentDef, ...],
) -> None:
    assert accept(segments, event_date="2026-11-04").event_date is None  # not stated
    future = ItemFacts(TEXT.replace("3 November 2026", "3 December 2026"))
    assert accept(segments, future, event_date="2026-12-03").event_date is None
    assert accept(segments, event_date=None).event_date is None


def test_a_quote_must_be_an_exact_substring_of_the_text(
    segments: tuple[SegmentDef, ...],
) -> None:
    assert accept(segments, quote="two of my team rekey").quote is None
    assert accept(segments, quote="x" * 301).quote is None
    assert accept(segments, quote="   ").quote is None
    spaced = accept(segments, quote="two  of my\nteam re-key")
    assert spaced.quote == "two of my team re-key"


@pytest.mark.parametrize(
    "field",
    ["segment", "signal_type", "website", "author_name", "author_role", "quote", "event_date"],
)
def test_a_malformed_answer_raises(segments: tuple[SegmentDef, ...], field: str) -> None:
    with pytest.raises(ValueError, match="signal_inbox_parse"):
        accept(segments, **{field: 12})


def test_a_bad_date_text_raises(segments: tuple[SegmentDef, ...]) -> None:
    with pytest.raises(ValueError, match="event_date"):
        accept(segments, event_date="next week")


def test_the_lead_name_is_the_company_unless_it_is_a_website() -> None:
    assert lead_name("Northgate Supply", "northgate-supply.co.uk") == "Northgate Supply"
    assert lead_name("northgate-supply.co.uk", "northgate-supply.co.uk") == (
        "northgate-supply.co.uk"
    )
    assert lead_name("https://www.northgate-supply.co.uk", "northgate-supply.co.uk") == (
        "northgate-supply.co.uk"
    )
    assert lead_name(None, "northgate-supply.co.uk") == "northgate-supply.co.uk"
