"""The SuppressionEntry machine (``nextron.domain.suppression_entry``, AD-4, AD-16, Story 3.3):
the exact statuses, sources, events and causes; ``OPT_OUT_SOURCES``, ``VOIDABLE_SOURCES`` and
``TERMINAL_STATUSES``; ``CREATION`` for every source; the one-row transition table; and
``IllegalTransition`` for every other (status, event) pair, ``voided`` included, and for values
that are no status or event. The tables are read-only."""

import itertools
from types import MappingProxyType

import pytest

from nextron.domain.errors import DomainError
from nextron.domain.machines import IllegalTransition, Transition
from nextron.domain.suppression_entry import (
    CREATION,
    MACHINE,
    OPT_OUT_SOURCES,
    TERMINAL_STATUSES,
    TRANSITIONS,
    VOIDABLE_SOURCES,
    EntryCause,
    EntryEvent,
    EntryStatus,
    SuppressionSource,
    transition,
)

SOURCES = ["reply_rule", "reply_class", "one_click", "mailto", "operator", "import", "dsr"]


def test_the_statuses_sources_events_and_causes_are_closed() -> None:
    assert MACHINE == "suppression_entry"
    assert [str(status) for status in EntryStatus] == ["active", "voided"]
    assert [str(source) for source in SuppressionSource] == SOURCES
    assert [str(event) for event in EntryEvent] == ["void"]
    assert [str(cause) for cause in EntryCause] == [*SOURCES, "operator_reversal"]


def test_the_opt_out_voidable_and_terminal_groups() -> None:
    assert [str(source) for source in OPT_OUT_SOURCES] == [
        "reply_rule",
        "reply_class",
        "one_click",
        "mailto",
    ]
    assert [str(source) for source in VOIDABLE_SOURCES] == ["reply_rule"]
    assert TERMINAL_STATUSES == (EntryStatus.VOIDED,)
    assert "operator" not in OPT_OUT_SOURCES
    assert "reply_rule" in OPT_OUT_SOURCES  # a plain code matches too


@pytest.mark.parametrize("source", list(SuppressionSource))
def test_every_entry_is_created_active_with_its_source_as_the_cause(
    source: SuppressionSource,
) -> None:
    assert CREATION[source] == Transition(EntryStatus.ACTIVE, EntryCause(str(source)))


def test_creation_covers_exactly_the_sources() -> None:
    assert set(CREATION) == set(SuppressionSource)


def test_the_table_has_one_row_active_void_to_voided() -> None:
    assert dict(TRANSITIONS) == {
        (EntryStatus.ACTIVE, EntryEvent.VOID): Transition(
            EntryStatus.VOIDED, EntryCause.OPERATOR_REVERSAL
        )
    }
    assert transition(EntryStatus.ACTIVE, EntryEvent.VOID) == Transition(
        EntryStatus.VOIDED, EntryCause.OPERATOR_REVERSAL
    )


@pytest.mark.parametrize(
    ("status", "event"),
    [
        pair
        for pair in itertools.product(EntryStatus, EntryEvent)
        if pair != (EntryStatus.ACTIVE, EntryEvent.VOID)
    ],
)
def test_every_other_pair_raises_illegal_transition(
    status: EntryStatus, event: EntryEvent
) -> None:
    with pytest.raises(IllegalTransition) as caught:
        transition(status, event)

    assert str(caught.value) == f"suppression_entry: {event} is not allowed from {status}"
    assert caught.value.code == "illegal_transition"
    assert isinstance(caught.value, DomainError)


def test_voided_is_terminal() -> None:
    assert all(status not in TERMINAL_STATUSES for status, _ in TRANSITIONS)
    with pytest.raises(IllegalTransition):
        transition(EntryStatus.VOIDED, EntryEvent.VOID)


@pytest.mark.parametrize(
    ("status", "event"),
    [
        ("active", "reactivate"),
        ("voided", "unvoid"),
        ("deleted", "void"),
        ("expired", "void"),
        (None, "void"),
        (["active"], "void"),
        ("active", {"void"}),
    ],
)
def test_values_that_are_no_status_or_event_raise_illegal_transition(
    status: object, event: object
) -> None:
    with pytest.raises(IllegalTransition):
        transition(status, event)  # type: ignore[arg-type]


def test_the_tables_are_read_only() -> None:
    assert isinstance(TRANSITIONS, MappingProxyType)
    assert isinstance(CREATION, MappingProxyType)
    with pytest.raises(TypeError):
        TRANSITIONS[(EntryStatus.VOIDED, EntryEvent.VOID)] = Transition(  # type: ignore[index]
            EntryStatus.ACTIVE, EntryCause.OPERATOR
        )
    with pytest.raises(TypeError):
        CREATION[SuppressionSource.DSR] = Transition(  # type: ignore[index]
            EntryStatus.VOIDED, EntryCause.DSR
        )
