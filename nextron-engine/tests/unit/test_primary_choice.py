"""``nextron.domain.contacts.choose_primary`` (FR-13, AD-7; Story 2.10): the operator's Primary
stays until its address is Invalid; otherwise an eligible Contact (a decision-maker role, not
departed, an address that isn't Invalid) matching the strongest Event's earliest preferred role
wins, else the best rank; ties go to the earliest found; no eligible Contact means no Primary.
Also ``PrimaryRefused``."""

from datetime import UTC, datetime, timedelta

import pytest

from nextron.domain import seed_yaml
from nextron.domain.contacts import (
    NO_PRIMARY,
    PrimaryCandidate,
    PrimaryChoice,
    PrimaryRefusal,
    PrimaryRefused,
    PrimarySetBy,
    choose_primary,
)
from nextron.domain.errors import DomainError
from nextron.domain.segments import SegmentDef, validate_segments
from tests.contracts.support import ENGINE_ROOT

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
FINANCE = ("Controller", "CFO", "Finance Director")  # the Ops AP/AR posting's preferred roles


def segment(key: str) -> SegmentDef:
    text = (ENGINE_ROOT / "seed" / "segments.yaml").read_text(encoding="utf-8")
    return next(s for s in validate_segments(seed_yaml.parse(text)) if s.key == key)


LAW = segment("law_private_ai")
OPS = segment("ops_automation")


def person(
    contact_id: str,
    role: str | None,
    minutes: int = 0,
    *,
    address_class: str | None = None,
    departed: bool = False,
    primary: PrimarySetBy | None = None,
) -> PrimaryCandidate:
    return PrimaryCandidate(
        contact_id=contact_id,
        role=role,
        created_at=T0 + timedelta(minutes=minutes),
        address_class=address_class,
        departed=departed,
        is_primary=primary is not None,
        set_by=primary,
    )


def system(contact_id: str) -> PrimaryChoice:
    return PrimaryChoice(contact_id, PrimarySetBy.SYSTEM)


def test_the_best_rank_wins() -> None:
    candidates = [person("A", "CEO", 0), person("B", "Chief Operating Officer", 1)]

    assert choose_primary(candidates, OPS, ()) == system("B")


def test_a_tie_goes_to_the_earliest_found_then_the_id() -> None:
    later = person("A", "COO", 5)
    earlier = person("B", "VP of Operations", 1)
    same_time = [person("D", "COO", 0), person("C", "VP Operations", 0)]

    assert choose_primary([later, earlier], OPS, ()) == system("B")
    assert choose_primary(same_time, OPS, ()) == system("C")


def test_the_strongest_events_preferred_role_wins_over_rank() -> None:
    candidates = [person("A", "COO", 0), person("B", "Financial Controller", 1)]

    assert choose_primary(candidates, OPS, FINANCE) == system("B")
    assert choose_primary(candidates, OPS, ()) == system("A")


def test_the_earliest_listed_preferred_role_wins() -> None:
    candidates = [person("A", "Finance Director", 0), person("B", "Chief Financial Officer", 1)]

    assert choose_primary(candidates, OPS, FINANCE) == system("B")  # CFO is listed first
    assert choose_primary(candidates, OPS, ("Finance Director", "CFO")) == system("A")


def test_preferred_roles_no_contact_matches_fall_back_to_rank() -> None:
    candidates = [person("A", "Owner", 0), person("B", "COO", 1)]

    assert choose_primary(candidates, OPS, FINANCE) == system("B")


def test_only_eligible_contacts_count() -> None:
    gone = person("A", "COO", 0, departed=True)
    invalid = person("B", "COO", 1, address_class="invalid")
    no_role = person("C", None, 2)
    not_a_decision_maker = person("D", "Operations Manager", 3)
    owner = person("E", "Owner", 4, address_class="guessed")

    candidates = [gone, invalid, no_role, not_a_decision_maker]
    assert choose_primary(candidates, OPS, ()) == NO_PRIMARY
    assert choose_primary([*candidates, owner], OPS, ()) == system("E")
    assert choose_primary([], LAW, ()) == PrimaryChoice(None, None)


def test_a_preferred_role_must_still_be_eligible() -> None:
    departed_controller = person("A", "Controller", 0, departed=True)
    coo = person("B", "COO", 1)

    assert choose_primary([departed_controller, coo], OPS, FINANCE) == system("B")


def test_the_operators_primary_stays_until_its_address_is_invalid() -> None:
    ceo = person("A", "CEO", 0, primary=PrimarySetBy.OPERATOR)
    coo = person("B", "COO", 1)
    pinned = PrimaryChoice("A", PrimarySetBy.OPERATOR)

    assert choose_primary([ceo, coo], OPS, ()) == pinned
    assert choose_primary([ceo, coo], OPS, FINANCE) == pinned
    # any Contact the operator picked stays, decision-maker role or not, departed or not
    office = person("C", "Office Manager", 2, primary=PrimarySetBy.OPERATOR, departed=True)
    assert choose_primary([office, coo], OPS, ()) == PrimaryChoice("C", PrimarySetBy.OPERATOR)
    invalid = person("A", "CEO", 0, primary=PrimarySetBy.OPERATOR, address_class="invalid")
    assert choose_primary([invalid, coo], OPS, ()) == system("B")


def test_the_engines_primary_is_chosen_again_from_scratch() -> None:
    held = person("A", "CEO", 0, primary=PrimarySetBy.SYSTEM)
    coo = person("B", "COO", 5)

    assert choose_primary([held, coo], OPS, ()) == system("B")
    unknown = person("A", "CEO", 0)
    assert choose_primary([unknown], OPS, ()) == system("A")


def test_choose_primary_refuses_what_isnt_a_candidate_or_a_role_list() -> None:
    with pytest.raises(TypeError):
        choose_primary(["A"], OPS, ())  # type: ignore[list-item]
    with pytest.raises(TypeError):
        choose_primary([], OPS, "CFO")


@pytest.mark.parametrize("reason", list(PrimaryRefusal))
def test_primary_refused_carries_its_reason_code_only(reason: PrimaryRefusal) -> None:
    refused = PrimaryRefused(reason)

    assert isinstance(refused, DomainError)
    assert refused.code == "primary_refused"
    assert refused.reason is reason
    assert str(refused) == f"the Primary Contact wasn't set: {reason}"


def test_the_refusals_and_setters_are_the_specified_codes() -> None:
    assert [str(r) for r in PrimaryRefusal] == [
        "unknown_lead",
        "merged_lead",
        "not_on_lead",
        "address_invalid",
        "contact_erased",  # Story 3.13, at the merge: a tombstone is nobody's Primary
    ]
    assert [str(s) for s in PrimarySetBy] == ["system", "operator"]
    assert max(len(str(s)) for s in PrimarySetBy) <= 8  # contacts.primary_set_by is String(8)
