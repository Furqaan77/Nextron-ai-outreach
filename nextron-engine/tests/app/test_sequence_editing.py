"""Dashboard Sequence editing (FR-4, AD-3, AD-4, AD-12; Story 11.7): ``SequenceEditor.edit``,
the ``sequence_versions`` rows the repository writes, the Enrollment's bound version and the
``queries.setup``/``queries.sequencing`` reads it adds, on the world of
``tests.app.test_enroll`` (a temporary database at head, the shipped seeds, Law-PrivateAI x US
active). Each row of the spec's I/O matrix that isn't a seed sync or a page is here; the seed
sync rows are in ``tests/app/test_sequence_sync.py``, the running-Enrollment rows in
``tests/app/test_draft_slots.py`` and the page rows in ``tests/app/test_sequences_page.py``."""

import json
import logging
import sqlite3
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.sequence_editing import (
    FIRST_KEPT,
    NO_SUCH_SEQUENCE,
    NOT_REORDERED,
    STALE,
    UNREADABLE,
    SequenceEditor,
    SequenceSaved,
)
from nextron.db.repos.setup import sequences as sequences_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.enrollment import NewEnrollment
from nextron.domain.sequences import SequenceDef, SequenceRefused, sequence_form
from nextron.web.sequences_page import enrollment_lines
from tests.app.test_enroll import LATER, US_CAMPAIGN, World, build_world, stamp

UNKNOWN = "01JD3QZ8X5V9K2M7N4P6R8T0AB"


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def default(world: World) -> Any:
    found = world.read(lambda: setup_queries.sequence("default"))
    assert found is not None
    return found


def fields_of(definition: SequenceDef) -> dict[str, Any]:
    """The editor's fields for ``definition``, each row keeping its stored Step."""
    return {
        "name": definition.name,
        "steps": [
            {
                "stored_step": brief.step,
                "delay_days": brief.delay_days,
                "purpose": brief.purpose,
                "word_limit": brief.word_limit,
                "word_comparison": str(brief.word_comparison),
                "allowed_content": list(brief.allowed_content),
                "ask": brief.ask,
            }
            for brief in definition.steps
        ],
    }


def edit(world: World, fields: dict[str, Any], based_on: int | None = None) -> SequenceSaved:
    found = default(world)
    with world.units.open():
        return SequenceEditor(world.clock).edit(
            found.id, found.version if based_on is None else based_on, fields
        )


def refused(world: World, fields: dict[str, Any], **kwargs: Any) -> dict[str, str]:
    before = world.counts()
    with pytest.raises(SequenceRefused) as caught:
        edit(world, fields, **kwargs)
    assert world.counts() == before  # nothing written
    return caught.value.problems


def versions(world: World) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT v.version, v.actor, v.created_at FROM sequence_versions v "
        "JOIN sequences s ON s.id = v.sequence_id WHERE s.key = 'default' ORDER BY v.version"
    )


# The matrix: edit ---------------------------------------------------------------------------------


def test_the_seed_bootstrap_stored_version_1_with_actor_seed(world: World) -> None:
    [(version, actor, _)] = versions(world)

    assert (version, actor) == (1, "seed")
    [(definition,)] = world.sql("SELECT definition FROM sequence_versions")
    assert json.loads(definition) == sequence_form(default(world).definition)


def test_an_edit_based_on_v2_stores_v3_with_its_row_and_event(world: World) -> None:
    fields = fields_of(default(world).definition)
    fields["steps"][2]["ask"] = "Offer a short case study by reply"
    first = edit(world, fields)
    assert (first.version, first.changed) == (2, True)
    fields["steps"][3]["purpose"] = "A respectful last note"

    saved = edit(world, fields, based_on=2)

    found = default(world)
    assert (saved.key, saved.version, saved.changed, found.version) == ("default", 3, True, 3)
    assert found.definition.steps[3].purpose == "A respectful last note"
    assert [row[:2] for row in versions(world)] == [(1, "seed"), (2, "operator"), (3, "operator")]
    events = world.sql(
        "SELECT from_status, to_status, cause, actor, at FROM events "
        "WHERE entity = 'sequences' ORDER BY rowid"
    )
    assert events[-1] == ("v2", "v3", "operator_edit", "operator", stamp(LATER))
    [(definition,)] = world.sql("SELECT definition FROM sequence_versions WHERE version = 3")
    assert json.loads(definition) == sequence_form(found.definition)


def test_a_saved_version_is_logged(world: World, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.sequence_editing")
    fields = fields_of(default(world).definition)
    fields["name"] = "Default Sequence"

    edit(world, fields)

    [record] = [r for r in caplog.records if r.getMessage() == "sequence_saved"]
    assert (record.key, record.version) == ("default", 2)


def test_an_identical_save_writes_nothing(world: World) -> None:
    before = world.counts()

    saved = edit(world, fields_of(default(world).definition))

    assert (saved.version, saved.changed) == (1, False)
    assert world.counts() == before


# The matrix: refusals -----------------------------------------------------------------------------


def test_a_step_3_delay_not_after_step_2_is_refused_on_its_field(world: World) -> None:
    fields = fields_of(default(world).definition)
    fields["steps"][2]["delay_days"] = 3

    problems = refused(world, fields)

    assert problems == {"steps[3].delay_days": "must be more than Step 2's delay_days (3)"}


@pytest.mark.parametrize(
    ("step", "name", "value", "field"),
    [
        (2, "ask", "", "steps[2].ask"),
        (1, "word_limit", 0, "steps[1].word_limit"),
        (4, "word_limit", 151, "steps[4].word_limit"),
        (3, "word_limit", "eighty", "steps[3].word_limit"),
        (2, "allowed_content", [], "steps[2].allowed_content"),
        (2, "purpose", "Proof of SRA readiness", "steps[2].purpose"),
    ],
)
def test_a_bad_field_is_refused_naming_the_step_and_field(
    world: World, step: int, name: str, value: object, field: str
) -> None:
    fields = fields_of(default(world).definition)
    fields["steps"][step - 1][name] = value

    problems = refused(world, fields)

    assert list(problems) == [field]


def test_a_stale_form_is_refused(world: World) -> None:
    fields = fields_of(default(world).definition)
    fields["name"] = "Changed"
    edit(world, fields)

    assert refused(world, fields, based_on=1) == {"form": STALE}


def test_an_unknown_sequence_is_refused(world: World) -> None:
    fields = fields_of(default(world).definition)
    for sequence_id in (UNKNOWN, "not-a-ulid"):
        with world.units.open(), pytest.raises(SequenceRefused) as caught:
            SequenceEditor(world.clock).edit(sequence_id, 1, fields)
        assert caught.value.problems == {"form": NO_SUCH_SEQUENCE}


def test_a_stored_sequence_that_no_longer_decodes_is_refused(world: World) -> None:
    found = default(world)
    fields = fields_of(found.definition)
    world.sql("UPDATE step_briefs SET allowed_content = 'x' WHERE step = 2")
    before = world.counts()

    with world.units.open(), pytest.raises(SequenceRefused) as caught:
        SequenceEditor(world.clock).edit(found.id, found.version, fields)

    assert list(caught.value.problems) == ["form"]
    assert caught.value.problems["form"].startswith(f"{UNREADABLE}: ")
    assert world.counts() == before


def test_step_1_can_t_be_removed_and_steps_aren_t_reordered(world: World) -> None:
    fields = fields_of(default(world).definition)
    without_first = {**fields, "steps": fields["steps"][1:]}
    swapped = {**fields, "steps": [fields["steps"][0], fields["steps"][2], fields["steps"][1]]}
    appended_in_the_middle = {
        **fields,
        "steps": [fields["steps"][0], {**fields["steps"][1], "stored_step": None}, fields["steps"][2]],
    }
    unknown = {**fields, "steps": [fields["steps"][0], {**fields["steps"][1], "stored_step": 9}]}

    assert refused(world, without_first) == {"steps[1]": FIRST_KEPT}
    assert refused(world, swapped) == {"steps": NOT_REORDERED}
    assert refused(world, appended_in_the_middle) == {"steps": NOT_REORDERED}
    assert refused(world, unknown) == {"steps": NOT_REORDERED}


def test_seven_steps_are_refused_on_the_steps(world: World) -> None:
    fields = fields_of(default(world).definition)
    for day in (20, 21, 22):
        fields["steps"].append({**fields["steps"][3], "stored_step": None, "delay_days": day})

    assert refused(world, fields) == {"steps": "must hold at most 6 items"}


def test_fewer_steps_than_a_variant_angle_names_is_refused_naming_campaign_and_variant(
    world: World,
) -> None:
    world.sql(
        "UPDATE variants SET step_angles = '[{\"note\":\"A last nudge\",\"step\":4}]' "
        "WHERE campaign_id = ?",
        (world.campaign(US_CAMPAIGN),),
    )
    fields = fields_of(default(world).definition)
    fields["steps"] = fields["steps"][:3]

    problems = refused(world, fields)

    assert problems == {
        "steps": f"Campaign {US_CAMPAIGN}'s Variant A has an angle note for Step 4, so the "
        "Sequence needs at least 4 Steps"
    }
    # the stale check comes first
    assert refused(world, fields, based_on=7) == {"form": STALE}


# Fixed fields and angles --------------------------------------------------------------------------


def test_the_fixed_fields_come_from_code_and_the_stored_step_never_the_form(
    world: World,
) -> None:
    stored = default(world).definition
    fields = fields_of(stored)
    fields["steps"][0].update(
        delay_days=5,
        link_rule="in_body",
        no_price=False,
        subject_rule={"word_limit": 20, "word_comparison": "at_most", "guidance": ["Long"]},
    )
    fields["steps"][1].update(no_audit=False, segment_angles=[])
    fields["steps"][2]["ask"] = "A changed ask"

    saved = edit(world, fields)

    assert saved.changed
    found = default(world).definition
    assert found.steps[0] == stored.steps[0]  # delay 0, link rule, subject rule all kept
    assert found.steps[1] == stored.steps[1]  # its angles kept, no_audit still true
    assert found.steps[2].ask == "A changed ask"


def test_a_removed_step_takes_its_angles_and_an_appended_one_has_none(world: World) -> None:
    stored = default(world).definition
    assert stored.steps[1].segment_angles  # the shipped Step 2 carries the regulatory angles
    fields = fields_of(stored)
    appended = {**fields["steps"][3], "stored_step": None, "delay_days": 21}
    fields["steps"] = [fields["steps"][0], fields["steps"][2], fields["steps"][3], appended]

    saved = edit(world, fields)

    found = default(world).definition
    assert saved.version == 2
    assert [brief.delay_days for brief in found.steps] == [0, 7, 14, 21]
    assert [brief.segment_angles for brief in found.steps] == [
        stored.steps[0].segment_angles,
        stored.steps[2].segment_angles,
        stored.steps[3].segment_angles,
        (),
    ]
    assert world.sql("SELECT count(*) FROM step_briefs") == [(4,)]


def test_sequence_versions_rows_are_immutable(world: World) -> None:
    [(version_id,)] = world.sql("SELECT id FROM sequence_versions")
    for statement in (
        "UPDATE sequence_versions SET definition = '{}'",
        "DELETE FROM sequence_versions",
        "INSERT OR REPLACE INTO sequence_versions (id, sequence_id, version, definition, "
        f"created_at, actor) SELECT '{version_id}', sequence_id, 9, '{{}}', created_at, "
        "'operator' FROM sequence_versions",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="sequence_versions rows are immutable"):
            world.sql(statement)


# The reads ----------------------------------------------------------------------------------------


def test_the_version_queries_decode_each_stored_version(world: World) -> None:
    v1 = default(world)
    fields = fields_of(v1.definition)
    fields["name"] = "Renamed"
    edit(world, fields)

    found = world.read(lambda: setup_queries.sequence_versions(v1.id))
    current_id = world.read(lambda: setup_queries.current_sequence_version_id(v1.id))

    assert [(each.version, each.actor, each.definition.name) for each in found] == [
        (1, "seed", v1.definition.name),
        (2, "operator", "Renamed"),
    ]
    assert current_id == found[1].id
    assert world.read(lambda: setup_queries.sequence_version(found[0].id)) == found[0]
    assert world.read(lambda: setup_queries.sequence_version("not-a-ulid")) is None
    assert world.read(lambda: setup_queries.sequence_version(UNKNOWN)) is None
    assert world.read(lambda: setup_queries.sequence_versions("not-a-ulid")) == ()
    assert world.read(lambda: setup_queries.sequence_steps()) == {"default": 4}


def test_live_enrollments_are_counted_per_version(world: World) -> None:
    campaign = world.campaign(US_CAMPAIGN)
    for _ in range(2):
        _, contact_id = world.contact()
        assert isinstance(world.enroll(contact_id, campaign), NewEnrollment)
    fields = fields_of(default(world).definition)
    fields["name"] = "Renamed"
    edit(world, fields)
    _, contact_id = world.contact()
    assert isinstance(world.enroll(contact_id, campaign), NewEnrollment)
    sequence_id = default(world).id

    counts = world.read(lambda: dict(sequencing_queries.live_enrollments_by_version(sequence_id)))

    assert counts == {1: 2, 2: 1}
    assert enrollment_lines(counts) == (
        "2 active Enrollments keep version 1",
        "1 active Enrollment keeps version 2",
    )
    assert world.read(lambda: dict(sequencing_queries.live_enrollments_by_version("x"))) == {}


def insert_sequence(world: World, definition: SequenceDef) -> None:
    @use_case
    def write() -> None:
        with current().transaction():
            sequences_repo.insert(definition, LATER, "operator")

    with world.units.open():
        write()


def test_steps_per_sequence_takes_the_active_campaigns_sequences_then_all(world: World) -> None:
    stored = default(world).definition
    extra = stored.steps[3]
    longer = SequenceDef(
        "longer",
        "Five Steps",
        (*stored.steps, type(extra)(**{**_fields(extra), "step": 5, "delay_days": 21})),
    )
    insert_sequence(world, longer)

    assert world.read(setup_queries.steps_per_sequence) == 4  # only default is active

    world.sql("UPDATE campaigns SET status = 'draft'")

    assert world.read(setup_queries.steps_per_sequence) == 5  # no active Campaign: all


def test_steps_per_sequence_is_0_with_no_sequence(units: UnitOfWorkFactory) -> None:
    with units.open(read_only=True):
        assert setup_queries.steps_per_sequence() == 0


def _fields(brief: Any) -> dict[str, Any]:
    return {name: getattr(brief, name) for name in brief.__slots__}
