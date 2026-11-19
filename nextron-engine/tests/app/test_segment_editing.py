"""Story 11.5's Segment editing and version pinning on a temporary database at head (FR-1,
AD-3, AD-4, AD-12): ``SegmentEditor`` (every row of the spec's I/O matrix), the immutable
``segment_versions`` rows, and ``score_lead`` with the stored, missing and current version.
The world is Story 2.9's (``tests.app.test_signal_extraction``), loaded with the shipped
Segments; the scoring helpers are Story 2.15's (``tests.app.test_lead_scoring``)."""

import copy
import json
import logging
import sqlite3
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from nextron.app.queries import setup as setup_queries
from nextron.app.scoring.api import RescoreResult
from nextron.app.setup.segment_editing import (
    KEY_FIXED,
    NO_SUCH_SEGMENT,
    STALE,
    SegmentEditor,
    SegmentSaved,
)
from nextron.domain.segments import EDITING_RULE, FORM_FIELD, FORM_SEPARATOR, SegmentRefused
from tests.app.test_lead_scoring import (
    enriched,
    lawyers,
    lead_scores,
    score,
    scored_law_lead,
    scores,
    signal,
)
from tests.app.test_signal_extraction import World, build_world

LAW = "law_private_ai"
OPS = "ops_automation"


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine, prompts=False)


def stored_form(world: World, key: str = LAW) -> dict[str, Any]:
    [(text,)] = world.sql("SELECT definition FROM segments WHERE key = ?", (key,))
    return json.loads(text)


def segment_id(world: World, key: str = LAW) -> str:
    [(found,)] = world.sql("SELECT id FROM segments WHERE key = ?", (key,))
    return found


def version_of(world: World, key: str = LAW) -> int:
    [(found,)] = world.sql("SELECT version FROM segments WHERE key = ?", (key,))
    return found


def versions(world: World, key: str = LAW) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT v.version, v.actor, v.definition FROM segment_versions v "
        "JOIN segments s ON s.id = v.segment_id WHERE s.key = ? ORDER BY v.version",
        (key,),
    )


def segment_events(world: World, entity_id: str) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT from_status, to_status, cause, actor FROM events "
        "WHERE entity = 'segments' AND entity_id = ? ORDER BY rowid",
        (entity_id,),
    )


def snapshot(world: World) -> list[Any]:
    return [
        world.sql("SELECT * FROM segments ORDER BY key"),
        world.sql("SELECT * FROM segment_versions ORDER BY id"),
        world.sql("SELECT count(*) FROM events"),
        world.sql("SELECT count(*) FROM jobs"),
    ]


def edit(world: World, form: dict[str, Any], based_on: int | None = None) -> SegmentSaved:
    with world.units.open():
        return SegmentEditor(world.clock).edit(
            segment_id(world), version_of(world) if based_on is None else based_on, form
        )


def refused_edit(world: World, form: dict[str, Any], based_on: int | None = None) -> dict[str, str]:
    before = snapshot(world)
    with pytest.raises(SegmentRefused) as caught:
        edit(world, form, based_on)
    assert snapshot(world) == before  # nothing written
    return caught.value.problems


def weight(form: dict[str, Any], key: str, value: object) -> dict[str, Any]:
    changed = copy.deepcopy(form)
    for each in changed["signal_types"]:
        if each["key"] == key:
            each["weight"] = value
    return changed


def new_segment(world: World, key: str = "accountants") -> dict[str, Any]:
    """The Law Segment's form under a new key, with fresh Signal type and disqualifier keys."""
    form = stored_form(world)
    form["key"] = key
    form["name"] = "Accountants"
    for index, each in enumerate(form["signal_types"]):
        each["key"] = f"{key}_signal_{index}"
    for index, each in enumerate(form["disqualifiers"]):
        each["key"] = f"{key}_rule_{index}"
    return form


# The backfill and the version rows ---------------------------------------------------------------


def test_the_seeded_segments_each_have_their_first_version_row(world: World) -> None:
    for key in (LAW, OPS):
        [(version, actor, text)] = versions(world, key)
        [(definition,)] = world.sql("SELECT definition FROM segments WHERE key = ?", (key,))
        assert (version, actor, text) == (1, "seed", definition)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE segment_versions SET definition = '{}'",
        "UPDATE segment_versions SET actor = 'operator'",
        "DELETE FROM segment_versions",
    ],
)
def test_a_version_row_can_never_be_rewritten_or_deleted(world: World, statement: str) -> None:
    before = world.sql("SELECT * FROM segment_versions ORDER BY id")

    with pytest.raises((IntegrityError, sqlite3.IntegrityError), match="immutable"):
        world.sql(statement)

    assert world.sql("SELECT * FROM segment_versions ORDER BY id") == before


def test_a_version_row_can_not_be_replaced(world: World) -> None:
    [(row_id, segment, definition, created_at)] = world.sql(
        "SELECT v.id, v.segment_id, v.definition, v.created_at FROM segment_versions v "
        "JOIN segments s ON s.id = v.segment_id WHERE s.key = ?",
        (LAW,),
    )

    with pytest.raises((IntegrityError, sqlite3.IntegrityError), match="immutable"):
        world.sql(
            "INSERT OR REPLACE INTO segment_versions "
            "(id, segment_id, version, definition, created_at, actor) "
            "VALUES (?, ?, 1, '{}', ?, 'operator')",
            (row_id, segment, created_at),
        )
    assert versions(world)[0][2] == definition


# Edit ----------------------------------------------------------------------------------------------


def test_an_edit_stores_the_next_version_with_its_row_and_event(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    form = weight(stored_form(world), "law_contact_form_only", 20)
    caplog.set_level(logging.INFO, logger="nextron.app.setup.segment_editing")

    saved = edit(world, form)

    assert saved == SegmentSaved(segment_id(world), LAW, 2, True)
    assert version_of(world) == 2
    assert [(v, actor) for v, actor, _ in versions(world)] == [(1, "seed"), (2, "operator")]
    assert versions(world)[1][2] == world.sql(
        "SELECT definition FROM segments WHERE key = ?", (LAW,)
    )[0][0]
    assert segment_events(world, segment_id(world))[-1] == ("v1", "v2", "operator_edit", "operator")
    [record] = [r for r in caplog.records if r.getMessage() == "segment_saved"]
    assert (record.key, record.version) == (LAW, 2)  # type: ignore[attr-defined]


def test_an_edit_from_v3_stores_v4(world: World) -> None:
    form = stored_form(world)
    for value in (20, 25, 30):
        edit(world, weight(form, "law_contact_form_only", value))

    assert version_of(world) == 4
    assert segment_events(world, segment_id(world))[-1] == ("v3", "v4", "operator_edit", "operator")
    assert [v for v, _, _ in versions(world)] == [1, 2, 3, 4]


def test_a_save_identical_to_the_stored_definition_writes_nothing(world: World) -> None:
    before = snapshot(world)

    saved = edit(world, stored_form(world))

    assert saved == SegmentSaved(segment_id(world), LAW, 1, False)
    assert snapshot(world) == before


def test_no_save_enqueues_a_job_or_rescores_a_lead(world: World) -> None:
    lead, _, _ = scored_law_lead(world)
    score(world, lead)
    before = scores(world, lead)
    jobs = world.sql("SELECT count(*) FROM jobs")

    edit(world, weight(stored_form(world), "law_contact_form_only", 20))

    assert world.sql("SELECT count(*) FROM jobs") == jobs
    assert scores(world, lead) == before


def test_a_stale_form_is_refused(world: World) -> None:
    edit(world, weight(stored_form(world), "law_contact_form_only", 20))  # now v2

    problems = refused_edit(world, weight(stored_form(world), "law_contact_form_only", 25), 1)

    assert problems == {FORM_FIELD: STALE}
    assert STALE == "This Segment changed since you opened it"


def test_an_unknown_segment_is_refused(world: World) -> None:
    before = snapshot(world)
    with pytest.raises(SegmentRefused) as caught, world.units.open():
        SegmentEditor(world.clock).edit("01ARZ3NDEKTSV4RRFFQ69G5FAV", 1, stored_form(world))

    assert caught.value.problems == {FORM_FIELD: NO_SUCH_SEGMENT}
    assert snapshot(world) == before


def test_the_key_can_never_change(world: World) -> None:
    form = stored_form(world)
    form["key"] = "law_renamed"

    assert refused_edit(world, form) == {"key": KEY_FIXED}


def test_a_form_without_a_key_keeps_the_stored_one(world: World) -> None:
    form = weight(stored_form(world), "law_contact_form_only", 20)
    del form["key"]

    assert edit(world, form).key == LAW


@pytest.mark.parametrize(
    ("change", "field", "needle"),
    [
        (  # a Tier 2 weight at or above a Tier 1 weight
            lambda form: weight(form, "law_expansion", 60),
            "signal_types[2].weight",
            "must be below every Tier 1 weight",
        ),
        (  # an Event allowing Evidence other than Quote
            lambda form: _set(form, ("signal_types", 0, "evidence"), ["quote", "count"]),
            "signal_types[0].evidence",
            "an Event's evidence must be exactly [quote]",
        ),
        (  # a Signal type that represents ABA Formal Opinion 512
            lambda form: _set(form, ("signal_types", 1, "name"), "ABA Formal Opinion 512 post"),
            "signal_types[1].name",
            "market-wide fact",
        ),
        (  # a Signal type key another Segment already uses
            lambda form: _set(form, ("signal_types", 2, "key"), "ops_expansion"),
            "signal_types[2].key",
            "repeats an earlier signal type key (ops_expansion)",
        ),
        (  # a weight that isn't a number names its field
            lambda form: weight(form, "law_expansion", "high"),
            "signal_types[2].weight",
            "must be a whole number",
        ),
        (
            lambda form: _set(form, ("name",), ""),
            "name",
            "must not be blank",
        ),
    ],
)
def test_the_validator_refuses_and_names_the_field(
    world: World, change: Any, field: str, needle: str
) -> None:
    problems = refused_edit(world, change(stored_form(world)))

    assert list(problems) == [field]
    assert needle in problems[field]


def _set(form: dict[str, Any], path: tuple[Any, ...], value: object) -> dict[str, Any]:
    changed = copy.deepcopy(form)
    target: Any = changed
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return changed


def test_a_segment_without_a_tier_1_or_2_event_is_refused(world: World) -> None:
    form = stored_form(world)
    form["signal_types"] = [each for each in form["signal_types"] if each["tier"] == 3]

    problems = refused_edit(world, form)

    assert problems == {"signal_types": EDITING_RULE}


def test_the_editing_rule_needs_an_event_not_an_attribute(world: World) -> None:
    form = stored_form(world)
    for each in form["signal_types"]:
        if each["class"] == "event":
            each["class"] = "attribute"

    assert refused_edit(world, form) == {"signal_types": EDITING_RULE}


# Create --------------------------------------------------------------------------------------------


def test_a_new_segment_is_stored_at_version_1_and_is_inert(world: World) -> None:
    with world.units.open():
        saved = SegmentEditor(world.clock).create(new_segment(world))

    assert (saved.key, saved.version, saved.changed) == ("accountants", 1, True)
    assert [(v, actor) for v, actor, _ in versions(world, "accountants")] == [(1, "operator")]
    assert segment_events(world, saved.segment_id) == [
        (None, "v1", "operator_create", "operator")
    ]
    assert world.sql(
        "SELECT count(*) FROM campaigns WHERE segment_id = ?", (saved.segment_id,)
    ) == [(0,)]
    assert world.sql("SELECT count(*) FROM jobs WHERE type = 'score_lead'") == [(0,)]


def test_a_repeated_segment_key_is_refused_on_key(world: World) -> None:
    before = snapshot(world)
    with pytest.raises(SegmentRefused) as caught, world.units.open():
        SegmentEditor(world.clock).create(new_segment(world, OPS))

    assert list(caught.value.problems) == ["key"]
    assert "repeats an earlier segment key (ops_automation)" in caught.value.problems["key"]
    assert snapshot(world) == before


def test_create_refuses_a_signal_key_another_segment_uses(world: World) -> None:
    form = new_segment(world)
    form["signal_types"][1]["key"] = "law_hiring_legal_ops"

    with pytest.raises(SegmentRefused) as caught, world.units.open():
        SegmentEditor(world.clock).create(form)

    assert list(caught.value.problems) == ["signal_types[1].key"]


def test_a_stored_segment_that_no_longer_validates_refuses_every_save(world: World) -> None:
    world.sql(
        "UPDATE segments SET definition = '{\"key\": \"ops_automation\"}' WHERE key = ?", (OPS,)
    )

    problems = refused_edit(world, weight(stored_form(world), "law_contact_form_only", 20))

    assert list(problems) == [FORM_FIELD]
    assert problems[FORM_FIELD].startswith("A stored Segment no longer validates")


def test_a_stored_segment_that_is_not_json_refuses_every_save(world: World) -> None:
    world.sql("UPDATE segments SET definition = 'not json' WHERE key = ?", (OPS,))

    problems = refused_edit(world, weight(stored_form(world), "law_contact_form_only", 20))

    assert problems == {
        FORM_FIELD: "A stored Segment no longer validates (its stored definition is not JSON)"
    }


def _hiring(form: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    [(index, found)] = [
        (index, each)
        for index, each in enumerate(form["signal_types"])
        if each["key"] == "law_hiring_legal_ops"
    ]
    return index, found


@pytest.mark.parametrize("separator", ["|", "=>"])
@pytest.mark.parametrize("where", ["title", "role", "when_term"])
def test_a_form_separator_in_a_title_role_or_term_is_refused(
    world: World, where: str, separator: str
) -> None:
    form = stored_form(world)
    index, hiring = _hiring(form)
    term = hiring["match_terms"][0]
    roles = ["Managing Partner"]
    if where == "title":
        form["decision_maker_roles"][0] = [f"Managing {separator} Partner"]
    elif where == "role":
        roles = [f"Head {separator} Ops"]
    else:
        term = f"intake {separator} ops"
        hiring["match_terms"] = [*hiring["match_terms"], term]
    hiring["preferred_roles"] = [{"when_terms": [term], "roles": roles}]

    problems = refused_edit(world, form)

    preferred = f"signal_types[{index}].preferred_roles"
    field = "decision_maker_roles" if where == "title" else preferred
    assert problems == {field: FORM_SEPARATOR}
    assert FORM_SEPARATOR == "can't contain | or =>"


def test_preferred_roles_without_a_separator_are_saved(world: World) -> None:
    form = stored_form(world)
    _, hiring = _hiring(form)
    hiring["preferred_roles"] = [{"when_terms": [hiring["match_terms"][0]], "roles": ["COO"]}]

    assert edit(world, form).version == 2


def test_a_save_takes_a_mapping(world: World) -> None:
    with pytest.raises(TypeError), world.units.open():
        SegmentEditor(world.clock).create(["not", "a", "mapping"])  # type: ignore[arg-type]


# The queries --------------------------------------------------------------------------------------


def test_the_queries_read_each_version(world: World) -> None:
    edit(world, weight(stored_form(world), "law_contact_form_only", 20))

    law = segment_id(world)
    with world.units.open(read_only=True):
        found = setup_queries.segment_versions(law)
        first = setup_queries.segment_version(law, 1)
        missing = setup_queries.segment_version(law, 9)
        not_ulid = setup_queries.segment_version("x", 1)

    assert [each.version for each in found] == [1, 2]
    assert [each.actor for each in found] == ["seed", "operator"]
    assert first is not None and first.definition == found[0].definition
    assert {
        each.weight for each in found[1].definition.signal_types if each.key == "law_contact_form_only"
    } == {20}
    assert (missing, not_ulid) == (None, None)


# Scoring with the stored version -----------------------------------------------------------------


def test_an_existing_lead_is_re_scored_with_its_stored_version(world: World) -> None:
    lead, _, _ = scored_law_lead(world)
    score(world, lead)
    assert scores(world, lead) == (65, 60, 63, "A", 1, None)

    edit(world, weight(stored_form(world), "law_contact_form_only", 20))  # v2
    score(world, lead)  # a plain score_lead (the "Re-score" button, or the research chain)

    assert scores(world, lead) == (65, 60, 63, "A", 1, None)  # still the v1 definition


def test_use_current_moves_the_lead_to_the_current_version(world: World) -> None:
    lead, _, _ = scored_law_lead(world)
    score(world, lead)
    edit(world, weight(stored_form(world), "law_contact_form_only", 20))

    with world.units.open():
        lead_scores(world).score_lead(lead, use_current=True)

    assert scores(world, lead) == (70, 60, 65, "A", 2, None)
    score(world, lead)  # and a plain score_lead now keeps v2
    assert scores(world, lead)[4] == 2


def test_a_new_lead_is_scored_with_the_current_version(world: World) -> None:
    edit(world, weight(stored_form(world), "law_contact_form_only", 20))
    lead, _, _ = scored_law_lead(world)

    score(world, lead)

    assert scores(world, lead) == (70, 60, 65, "A", 2, None)


def test_a_stored_version_disqualification_records_that_version(world: World) -> None:
    lead = world.lead()
    enriched(world, lead)
    signal(world, lead, "law_hiring_legal_ops")
    score(world, lead)  # unknown size: scored, never disqualified, at v1
    assert scores(world, lead)[4:] == (1, None)
    form = stored_form(world)
    form["disqualifiers"] = [
        each for each in form["disqualifiers"] if each["key"] != "law_fewer_than_5_lawyers"
    ]
    edit(world, form)  # v2 no longer has the rule
    count = lawyers(world, lead, 3, counted=True)

    outcome = score(world, lead)  # a plain score_lead: still the v1 definition

    assert (outcome.result, outcome.evidence_id) == (RescoreResult.DISQUALIFIED, count)
    assert version_of(world) == 2
    assert scores(world, lead) == (None, None, None, None, 1, "law_fewer_than_5_lawyers")


def test_a_stored_version_with_no_row_is_skipped(world: World) -> None:
    lead, _, _ = scored_law_lead(world)
    score(world, lead)
    edit(world, weight(stored_form(world), "law_contact_form_only", 20))
    world.sql("UPDATE leads SET score_segment_version = 7 WHERE id = ?", (lead,))  # pre-backfill
    before = scores(world, lead)

    outcome = score(world, lead)

    assert (outcome.result, outcome.reason) == ("skipped", "segment_version_missing")
    assert scores(world, lead) == before


def test_rescore_current_enqueues_its_own_job_beside_a_pending_one(world: World) -> None:
    lead, _, _ = scored_law_lead(world)
    with world.units.open():
        lead_scores(world).request_rescore(lead)
    with world.units.open():
        lead_scores(world).request_rescore_current(lead)
    with world.units.open():
        lead_scores(world).request_rescore_current(lead)  # absorbed by the live one

    jobs = world.jobs("score_lead")
    assert [(key, json.loads(payload), lane) for key, _, payload, lane in jobs] == [
        (f"score_lead:{lead}:tick", {"v": 1, "lead_id": lead}, "interactive"),
        (
            f"score_lead:{lead}:current",
            {"v": 1, "lead_id": lead, "use_current": True},
            "interactive",
        ),
    ]


def test_a_segment_rescore_covers_each_open_unmerged_lead(world: World) -> None:
    open_lead, _, _ = scored_law_lead(world)
    closed = world.lead("closed-law.com")
    world.sql("UPDATE leads SET pipeline_stage = 'lost' WHERE id = ?", (closed,))
    survivor = world.lead("survivor-law.com")
    merged = world.lead("merged-law.com")
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (survivor, merged))

    law = segment_id(world)
    with world.units.open():
        covered = lead_scores(world).request_segment_rescore(law)
    with world.units.open():
        none = lead_scores(world).request_segment_rescore("not-a-ulid")

    assert (covered, none) == (2, 0)
    keys = sorted(key for key, _, _, _ in world.jobs("score_lead"))
    assert keys == sorted(f"score_lead:{each}:current" for each in (open_lead, survivor))
    assert {lane for _, _, _, lane in world.jobs("score_lead")} == {"bulk"}
