"""Extraction contracts (AD-8, AD-18; Story 2.9): what ``nextron.domain.extraction`` restates of
the AI routes, so ``nextron.app`` never imports ``nextron.adapters.ai`` to learn it.

- **The budget class.** Every ``extract_signals`` route candidate (``routes.candidates(ROUTE)``,
  the default and any later one) has ``budget_class == BUDGET_CLASS``; the service checks
  ``budget.allow(BUDGET_CLASS, ...)`` before each call. So does every ``extract_people``
  candidate with ``nextron.domain.people.BUDGET_CLASS`` (Story 2.10), and its output schemas
  still have the ``people`` list with each person's ``name``, ``role``, ``block_id`` and
  ``quote``.
- **The fact kinds.** ``FactKind`` is exactly the profile-fact and size-clue kinds of every
  ``extract_signals`` output schema: ``PROFILE_FACTS`` the ``profile_facts`` kinds and
  ``SIZE_FACTS`` the ``size_clues`` kinds.
"""

import typing

from nextron.adapters.ai import routes
from nextron.adapters.ai.schemas import Person, ProfileFact, SizeClue
from nextron.domain import people
from nextron.domain.extraction import BUDGET_CLASS, PROFILE_FACTS, ROUTE, SIZE_FACTS


def literal_values(annotation: object) -> set[str]:
    return set(typing.get_args(annotation))


def test_every_extract_signals_candidate_has_the_domains_budget_class() -> None:
    candidates = routes.candidates(ROUTE)

    assert candidates  # not vacuous
    assert {candidate.budget_class for candidate in candidates} == {BUDGET_CLASS}


def test_the_fact_kinds_are_the_schemas_kinds() -> None:
    profile = literal_values(ProfileFact.model_fields["kind"].annotation)
    sizes = literal_values(SizeClue.model_fields["kind"].annotation)

    assert profile == {str(kind) for kind in PROFILE_FACTS}
    assert sizes == {str(kind) for kind in SIZE_FACTS}
    for candidate in routes.candidates(ROUTE):  # every version still has these lists
        fields = candidate.output_schema.model.model_fields
        assert {"signals", "profile_facts", "size_clues"} <= set(fields)


def test_every_extract_people_candidate_has_the_people_domains_budget_class() -> None:
    candidates = routes.candidates(people.ROUTE)

    assert candidates  # not vacuous
    assert {candidate.budget_class for candidate in candidates} == {people.BUDGET_CLASS}
    assert set(Person.model_fields) == {"name", "role", "block_id", "quote"}
    for candidate in candidates:  # every version still has the people list
        assert "people" in candidate.output_schema.model.model_fields
