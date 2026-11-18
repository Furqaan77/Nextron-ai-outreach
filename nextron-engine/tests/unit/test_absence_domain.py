"""``nextron.domain.absence`` (Story 2.9): the "contact form only" rule, every reason and the
order they are checked in, its argument checks, and every branch of ``reconcile_absence``."""

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from nextron.domain import seed_yaml
from nextron.domain.absence import (
    CONTACT_FORM_ONLY,
    CONTACT_FORM_ONLY_SOUGHT,
    AbsencePlan,
    AbsenceReason,
    CheckedPage,
    LiveAbsence,
    contact_form_only,
    reconcile_absence,
)
from nextron.domain.segments import EvidenceType, SegmentDef, validate_segments
from nextron.domain.widgets import ContactMethods, summarize
from tests.ai_eval.extraction import SEGMENTS_FILE

RUN = "01JD0000000000000000000011"
AT = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
HOME = "https://smith-law.com/"
TEAM = "https://smith-law.com/team"
CONTACT = "https://smith-law.com/contact"
LONG = "x" * 500  # the shortest text that needs no render
SHORT = "x" * 499


@pytest.fixture(scope="module")
def segments() -> tuple[SegmentDef, ...]:
    return validate_segments(seed_yaml.parse(SEGMENTS_FILE.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def law(segments: tuple[SegmentDef, ...]) -> SegmentDef:
    [found] = [segment for segment in segments if segment.key == "law_private_ai"]
    return found


@pytest.fixture(scope="module")
def ops(segments: tuple[SegmentDef, ...]) -> SegmentDef:
    [found] = [segment for segment in segments if segment.key == "ops_automation"]
    return found


def methods(**found: list[str]) -> ContactMethods:
    """The contact methods of HOME, TEAM and CONTACT, with ``found`` (method name to URLs)."""
    return summarize(
        RUN,
        AT,
        [(url, [m for m, urls in found.items() if url in urls]) for url in (HOME, TEAM, CONTACT)],
    )


def pages(
    text: str | None = LONG, tags: tuple[str, ...] | None = (), **changed: object
) -> list[CheckedPage]:
    """The three pages, each with ``text`` and ``tags``; ``changed`` maps a URL's name
    (``home``, ``team``, ``contact``) to a ``(text, tags)`` pair for that page."""
    found = []
    for name, url in (("home", HOME), ("team", TEAM), ("contact", CONTACT)):
        page_text, page_tags = changed.get(name, (text, tags))  # type: ignore[misc]
        found.append(CheckedPage(url, page_text, page_tags))  # type: ignore[arg-type]
    return found


FORM = {"form": [CONTACT]}


# The rule -------------------------------------------------------------------------------------


def test_the_constants() -> None:
    assert CONTACT_FORM_ONLY == "law_contact_form_only"
    assert CONTACT_FORM_ONLY_SOUGHT == (
        "a chat widget, an online booking link or an after-hours line"
    )
    assert [str(reason) for reason in AbsenceReason] == [
        "not_in_segment",
        "no_pages",
        "chat_found",
        "booking_found",
        "after_hours_found",
        "no_form",
        "page_needs_render",
        "tag_manager_unknown",
        "tag_manager",
        "holds",
    ]


def test_a_form_and_nothing_else_on_long_pages_with_no_tag_manager_holds(law: SegmentDef) -> None:
    assert contact_form_only(law, methods(**FORM), pages()) is AbsenceReason.HOLDS


def test_a_phone_number_doesnt_refute_it(law: SegmentDef) -> None:
    found = methods(form=[CONTACT], phone=[HOME, CONTACT])

    assert contact_form_only(law, found, pages()) is AbsenceReason.HOLDS


def test_a_segment_without_the_type_is_not_in_segment(ops: SegmentDef) -> None:
    assert contact_form_only(ops, methods(**FORM), pages()) is AbsenceReason.NOT_IN_SEGMENT


def test_the_type_without_absence_evidence_is_not_in_segment(law: SegmentDef) -> None:
    quoted = dataclasses.replace(
        law,
        signal_types=tuple(
            dataclasses.replace(t, evidence=(EvidenceType.QUOTE,))
            if t.key == CONTACT_FORM_ONLY
            else t
            for t in law.signal_types
        ),
    )

    assert contact_form_only(quoted, methods(**FORM), pages()) is AbsenceReason.NOT_IN_SEGMENT


def test_no_page_checked_is_no_pages(law: SegmentDef) -> None:
    assert contact_form_only(law, summarize(RUN, AT, []), []) is AbsenceReason.NO_PAGES


@pytest.mark.parametrize(
    ("method", "reason"),
    [
        ("chat", AbsenceReason.CHAT_FOUND),
        ("booking", AbsenceReason.BOOKING_FOUND),
        ("after_hours", AbsenceReason.AFTER_HOURS_FOUND),
    ],
)
def test_a_chat_booking_or_after_hours_line_on_any_page_refutes_it(
    law: SegmentDef, method: str, reason: AbsenceReason
) -> None:
    assert contact_form_only(law, methods(form=[CONTACT], **{method: [TEAM]}), pages()) is reason


def test_no_form_is_no_form(law: SegmentDef) -> None:
    assert contact_form_only(law, methods(phone=[HOME]), pages()) is AbsenceReason.NO_FORM


@pytest.mark.parametrize("text", [None, "", SHORT])
def test_a_page_with_no_text_or_under_500_characters_needs_render(
    law: SegmentDef, text: str | None
) -> None:
    checked = pages(team=(text, ()))

    assert contact_form_only(law, methods(**FORM), checked) is AbsenceReason.PAGE_NEEDS_RENDER


def test_a_page_never_checked_for_tag_managers_is_unknown(law: SegmentDef) -> None:
    checked = pages(home=(LONG, None))

    assert contact_form_only(law, methods(**FORM), checked) is AbsenceReason.TAG_MANAGER_UNKNOWN


def test_a_page_that_loads_a_tag_manager_is_tag_manager(law: SegmentDef) -> None:
    checked = pages(contact=(LONG, ("google_tag_manager",)))

    assert contact_form_only(law, methods(**FORM), checked) is AbsenceReason.TAG_MANAGER


ORDER_CASES = [
    # each case satisfies every reason from its own onwards; the first one wins. ``found`` None
    # means no page was checked.
    (AbsenceReason.NOT_IN_SEGMENT, "ops", None, None, None),
    (AbsenceReason.NOT_IN_SEGMENT, "ops", {"chat": [HOME]}, None, None),
    (AbsenceReason.CHAT_FOUND, "law", {"chat": [HOME], "booking": [HOME]}, None, None),
    (AbsenceReason.BOOKING_FOUND, "law", {"booking": [HOME], "after_hours": [HOME]}, None, None),
    (AbsenceReason.AFTER_HOURS_FOUND, "law", {"after_hours": [HOME]}, None, None),
    (AbsenceReason.NO_FORM, "law", {}, None, None),
    (AbsenceReason.PAGE_NEEDS_RENDER, "law", FORM, None, None),
    (AbsenceReason.TAG_MANAGER_UNKNOWN, "law", FORM, LONG, None),
]


@pytest.mark.parametrize(("reason", "segment", "found", "text", "tags"), ORDER_CASES)
def test_the_first_reason_that_applies_wins(
    law: SegmentDef,
    ops: SegmentDef,
    reason: AbsenceReason,
    segment: str,
    found: dict[str, list[str]] | None,
    text: str | None,
    tags: tuple[str, ...] | None,
) -> None:
    chosen = law if segment == "law" else ops
    if found is None:  # no page checked: it also satisfies no_pages
        assert contact_form_only(chosen, summarize(RUN, AT, []), []) is reason
        return
    checked = pages(text, tags)
    # one page also loads a tag manager: still the same first reason
    with_tag = [*checked[:2], CheckedPage(CONTACT, text, ("segment",))]

    assert contact_form_only(chosen, methods(**found), checked) is reason
    assert contact_form_only(chosen, methods(**found), with_tag) is reason


def test_a_tag_manager_wins_over_nothing_but_holds(law: SegmentDef) -> None:
    checked = pages(LONG, (), home=(LONG, ("tealium", "segment")))

    assert contact_form_only(law, methods(**FORM), checked) is AbsenceReason.TAG_MANAGER


@pytest.mark.parametrize(
    "checked",
    [
        [CheckedPage(HOME, LONG, ()), CheckedPage(TEAM, LONG, ())],  # one missing
        [*pages(), CheckedPage("https://smith-law.com/other", LONG, ())],  # one too many
        [*pages(), CheckedPage(HOME, LONG, ())],  # one twice
    ],
)
def test_the_pages_must_be_exactly_the_pages_checked(
    law: SegmentDef, checked: list[CheckedPage]
) -> None:
    with pytest.raises(ValueError):
        contact_form_only(law, methods(**FORM), checked)


def test_the_page_check_comes_before_any_reason(ops: SegmentDef) -> None:
    with pytest.raises(ValueError):
        contact_form_only(ops, methods(**FORM), pages()[:1])


@pytest.mark.parametrize(
    "arguments",
    [
        {"segment": "law_private_ai"},
        {"methods": {"form": [CONTACT]}},
        {"pages": [(HOME, LONG, ())]},
    ],
)
def test_arguments_of_the_wrong_type_raise(law: SegmentDef, arguments: dict[str, object]) -> None:
    values: dict[str, object] = {"segment": law, "methods": methods(**FORM), "pages": pages()}
    values.update(arguments)

    with pytest.raises(TypeError):
        contact_form_only(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("url", "text", "tags"),
    [
        ("", LONG, ()),
        (None, LONG, ()),
        (HOME, b"text", ()),
        (HOME, LONG, ["segment"]),
        (HOME, LONG, ("segment", 1)),
    ],
)
def test_a_bad_checked_page_is_refused(url: object, text: object, tags: object) -> None:
    with pytest.raises(TypeError):
        CheckedPage(url, text, tags)  # type: ignore[arg-type]


def test_a_checked_pages_text_never_shows_in_its_repr() -> None:
    page = CheckedPage(HOME, "Call Jane Doe on 0161 234 5678", ("segment",))

    assert "Jane" not in repr(page)
    assert HOME in repr(page) and "segment" in repr(page)


# Reconciling ----------------------------------------------------------------------------------

PAGES = (CONTACT, HOME, TEAM)
OTHER_PAGES = (HOME, TEAM)


def row(
    row_id: str,
    minutes: int,
    pages_checked: tuple[str, ...] = PAGES,
    sought: str = CONTACT_FORM_ONLY_SOUGHT,
) -> LiveAbsence:
    return LiveAbsence(row_id, sought, pages_checked, AT + timedelta(minutes=minutes))


def test_with_no_live_row_a_holding_absence_is_inserted() -> None:
    assert reconcile_absence([], True, PAGES) == AbsencePlan(insert=True)


def test_with_no_live_row_an_absence_that_doesnt_hold_writes_nothing() -> None:
    assert reconcile_absence([], False, PAGES) == AbsencePlan()


def test_when_it_doesnt_hold_every_live_row_is_retracted_oldest_first() -> None:
    live = [row("B", 2), row("A", 1), row("C", 2, OTHER_PAGES)]

    assert reconcile_absence(live, False, PAGES) == AbsencePlan(retract=("A", "B", "C"))


def test_the_oldest_row_with_the_same_sought_and_pages_is_confirmed() -> None:
    live = [row("A", 1, OTHER_PAGES), row("B", 2), row("C", 3)]

    assert reconcile_absence(live, True, PAGES) == AbsencePlan(confirm="B", retract=("A", "C"))


def test_another_sought_is_not_the_same_row() -> None:
    live = [row("A", 1, sought="a chat widget")]

    assert reconcile_absence(live, True, PAGES) == AbsencePlan(supersede="A")


def test_with_no_row_the_same_the_oldest_is_superseded_and_the_rest_retracted() -> None:
    live = [row("C", 3, OTHER_PAGES), row("A", 1, OTHER_PAGES), row("B", 1, (HOME,))]

    assert reconcile_absence(live, True, PAGES) == AbsencePlan(supersede="A", retract=("B", "C"))


def test_ties_on_created_at_go_by_id() -> None:
    live = [row("Z", 1), row("Y", 1)]

    assert reconcile_absence(live, True, PAGES) == AbsencePlan(confirm="Y", retract=("Z",))


def test_the_same_pages_means_an_equal_tuple() -> None:
    # the rule writes pages_checked sorted, as ContactMethods holds it; another order differs
    live = [row("A", 1, (HOME, CONTACT, TEAM))]

    assert reconcile_absence(live, True, PAGES) == AbsencePlan(supersede="A")


def test_a_holding_absence_needs_a_page() -> None:
    with pytest.raises(ValueError):
        reconcile_absence([], True, ())
    assert reconcile_absence([row("A", 1)], False, ()) == AbsencePlan(retract=("A",))


@pytest.mark.parametrize(
    ("live", "holds", "pages_checked"),
    [
        ([], 1, PAGES),
        ([], True, list(PAGES)),
        ([], True, (HOME, 1)),
        ([("A", CONTACT_FORM_ONLY_SOUGHT, PAGES, AT)], True, PAGES),
    ],
)
def test_reconcile_refuses_arguments_of_the_wrong_type(
    live: object, holds: object, pages_checked: object
) -> None:
    with pytest.raises(TypeError):
        reconcile_absence(live, holds, pages_checked)  # type: ignore[arg-type]


def test_a_plan_sets_at_most_one_of_confirm_insert_and_supersede() -> None:
    cases = [
        ([], True),
        ([row("A", 1)], True),
        ([row("A", 1, OTHER_PAGES)], True),
        ([row("A", 1)], False),
    ]
    for live, holds in cases:
        plan = reconcile_absence(live, holds, PAGES)
        chosen = [plan.confirm is not None, plan.insert, plan.supersede is not None]
        assert sum(chosen) == (1 if holds else 0)
