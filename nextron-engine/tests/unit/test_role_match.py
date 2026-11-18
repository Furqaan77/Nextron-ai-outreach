"""``nextron.domain.contacts``' decision-maker roles (FR-13, AD-7; Story 2.10): ``role_match``,
``title_matches`` and ``is_lawyer_role`` against the shipped Segments (Law-PrivateAI: ``[Managing
Partner]``, ``[Owner]``; Ops-Automation: ``[COO, VP Operations]``, ``[Owner, CEO]``, preferring
``[Controller, CFO, Finance Director]`` on an AP/AR posting). Every title the AC names is here,
with each normalisation rule, the swapped variants and the roles that never match."""

from dataclasses import replace

import pytest

from nextron.domain import seed_yaml
from nextron.domain.address_class import AddressClass
from nextron.domain.contacts import (
    INVALID_ADDRESS,
    RoleMatch,
    is_lawyer_role,
    role_match,
    title_matches,
)
from nextron.domain.segments import SegmentDef, validate_segments
from tests.contracts.support import ENGINE_ROOT


def segments() -> dict[str, SegmentDef]:
    text = (ENGINE_ROOT / "seed" / "segments.yaml").read_text(encoding="utf-8")
    return {segment.key: segment for segment in validate_segments(seed_yaml.parse(text))}


LAW = segments()["law_private_ai"]
OPS = segments()["ops_automation"]


@pytest.mark.parametrize(
    ("role", "law", "ops"),
    [
        # the AC's titles
        ("Managing Partner", RoleMatch("Managing Partner", 0), None),
        ("Founding Partner & Owner", RoleMatch("Owner", 1), RoleMatch("Owner", 1)),
        ("Chief Operating Officer", None, RoleMatch("COO", 0)),
        ("VP of Operations", None, RoleMatch("VP Operations", 0)),
        ("Vice President, Operations", None, RoleMatch("VP Operations", 0)),
        ("Financial Controller", None, RoleMatch("Controller", 2)),
        ("Finance Director", None, RoleMatch("Finance Director", 2)),
        ("CFO", None, RoleMatch("CFO", 2)),
        # canonical forms and dropped words
        ("COO", None, RoleMatch("COO", 0)),
        ("Chief Executive Officer", None, RoleMatch("CEO", 1)),
        ("Chief Financial Officer", None, RoleMatch("CFO", 2)),
        ("Chief Finance Officer", None, RoleMatch("CFO", 2)),
        ("V.P. Operations", None, RoleMatch("VP Operations", 0)),
        ("VP Ops", None, RoleMatch("VP Operations", 0)),
        ("VP, Operation", None, RoleMatch("VP Operations", 0)),
        ("SVP Operations", None, RoleMatch("VP Operations", 0)),
        ("EVP of Operations", None, RoleMatch("VP Operations", 0)),
        ("Senior Vice President of Operations", None, RoleMatch("VP Operations", 0)),
        ("Vice-President of the Operations", None, RoleMatch("VP Operations", 0)),
        ("President & CEO", None, RoleMatch("CEO", 1)),
        ("Co-Owner", RoleMatch("Owner", 1), RoleMatch("Owner", 1)),
        ("MANAGING PARTNER", RoleMatch("Managing Partner", 0), None),
        # the swapped variant around the first "of" or comma
        ("Director of Finance", None, RoleMatch("Finance Director", 2)),
        ("Director, Finance", None, RoleMatch("Finance Director", 2)),
        ("Director of Financial Operations", None, None),
        # the best rank wins
        ("COO & Owner", RoleMatch("Owner", 1), RoleMatch("COO", 0)),
        ("Owner and Managing Partner", RoleMatch("Managing Partner", 0), RoleMatch("Owner", 1)),
        ("Controller and COO", None, RoleMatch("COO", 0)),
        # not a decision-maker
        ("Partner", None, None),
        ("Associate", None, None),
        ("Paralegal", None, None),
        ("Operations Manager", None, None),
        ("Head of Ops", None, None),
        ("Vice President", None, None),
        ("Chief Operating", None, None),
        ("", None, None),
        # never a match
        ("Assistant to the Managing Partner", None, None),
        ("Deputy COO", None, None),
        ("Former Managing Partner", None, None),
        ("Managing Partner (retired)", None, None),
        ("Managing Partner Emeritus", None, None),
        ("Operations Intern", None, None),
        ("Secretary to the Owner", None, None),
        ("PA to the CEO", None, None),
        ("EA to the COO", None, None),
        ("Ex-CEO", None, None),
        ("AVP Operations", None, None),
        ("Executive Assistant to the CEO", None, None),
        # review pass 1: more canonical forms
        ("Chief Operations Officer", None, RoleMatch("COO", 0)),
        ("Chief Ops Officer", None, RoleMatch("COO", 0)),
        ("Chief Executive", None, RoleMatch("CEO", 1)),
        ("Deputy Chief Executive", None, None),
        # review pass 1: a junior qualifier takes a one-word Controller or Owner away
        ("Credit Controller", None, None),
        ("Document Controller", None, None),
        ("Stock Controller", None, None),
        ("Quality Controller", None, None),
        ("Product Owner", None, None),
        ("Process Owner", None, None),
        ("Account Owner", None, None),
        ("Business Owner", RoleMatch("Owner", 1), RoleMatch("Owner", 1)),
    ],
)
def test_role_match_maps_title_variants_to_each_segments_ranks(
    role: str, law: RoleMatch | None, ops: RoleMatch | None
) -> None:
    assert role_match(role, LAW) == law
    assert role_match(role, OPS) == ops


def test_a_title_only_in_preferred_roles_ranks_after_every_rank() -> None:
    assert len(OPS.decision_maker_roles) == 2
    assert role_match("Controller", OPS) == RoleMatch("Controller", 2)
    no_preferences = replace(
        OPS, signal_types=tuple(replace(t, preferred_roles=()) for t in OPS.signal_types)
    )
    assert role_match("Controller", no_preferences) is None


@pytest.mark.parametrize(
    ("role", "title", "matches"),
    [
        ("Financial Controller", "Controller", True),
        ("Accounts Payable Controller", "Controller", True),
        ("Director of Finance", "Finance Director", True),
        ("Finance Director", "Director", True),
        ("Chief Financial Officer", "CFO", True),
        ("CFO", "Chief Financial Officer", True),  # a title is normalised too
        ("Vice President, Operations", "VP Operations", True),
        ("Operations VP", "VP Operations", False),  # not contiguous in the role as written
        ("Vice President, Operations", "Operations VP", True),  # the swapped variant
        ("Assistant Controller", "Controller", False),
        ("Controller", "", False),
    ],
)
def test_title_matches(role: str, title: str, matches: bool) -> None:
    assert title_matches(role, title) is matches


@pytest.mark.parametrize(
    ("role", "lawyer"),
    [
        ("Managing Partner", True),
        ("Partner", True),
        ("Associate", True),
        ("Senior Associate", True),
        ("Attorney", True),
        ("Attorneys at Law", True),
        ("Lawyer", True),
        ("Of Counsel", True),
        ("Solicitor", True),
        ("Barrister", True),
        ("Partners", True),
        ("Paralegal", False),
        ("Legal Assistant", False),
        ("Legal Secretary to the Partners", False),
        ("Paralegal to the Associates", False),
        ("Office Manager", False),
        ("Owner", False),
        ("Counsellor", False),
        # review pass 1: not practising, or not a lawyer's work
        ("Retired Partner", False),
        ("Partner Emeritus", False),
        ("Former Partner", False),
        ("Ex-Partner", False),
        ("Summer Associate", False),
        ("Intern Associate", False),
        ("Marketing Associate", False),
        ("Business Development Associate", False),
        ("Client Relations Partner", False),
        ("Partner Relations Manager", False),
    ],
)
def test_is_lawyer_role(role: str, lawyer: bool) -> None:
    assert is_lawyer_role(role) is lawyer


@pytest.mark.parametrize(
    ("role", "title", "matches"),
    [
        ("Financial Controller", "Controller", True),
        ("Controller", "Controller", True),
        ("Credit Controller", "Controller", False),
        ("Sales Controller", "Controller", False),
        ("Founding Partner & Owner", "Owner", True),
        ("Co-Owner", "Owner", True),
        ("Owner", "Owner", True),
        ("Product Owner", "Owner", False),
        ("Data Owner", "Owner", False),
        ("Process Owner & COO", "COO", True),  # only the one-word title is qualified
    ],
)
def test_a_junior_qualifier_takes_a_one_word_title_away(
    role: str, title: str, matches: bool
) -> None:
    assert title_matches(role, title) is matches


def test_role_match_refuses_what_isnt_a_role_or_a_segment() -> None:
    with pytest.raises(TypeError):
        role_match(7, LAW)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        role_match("Owner", "law_private_ai")  # type: ignore[arg-type]


def test_the_invalid_address_class_is_the_address_class_machines() -> None:
    assert INVALID_ADDRESS == AddressClass.INVALID
