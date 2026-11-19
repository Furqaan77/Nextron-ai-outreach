"""Dashboard Sequence editing (FR-4, AD-3, AD-4, AD-12; Story 11.7): part of the ``setup``
service, the only writer of ``sequences``, ``step_briefs`` and ``sequence_versions``.

``SequenceEditor(clock)`` has one ``@use_case``, which reads the clock once and runs one
transaction. Every refusal raises ``SequenceRefused(problems)``, ``{form field: text}`` (``name``,
``steps``, ``steps[<n>]`` or ``steps[<n>].<field>`` with ``n`` the Step's number in the proposed
Sequence; ``form`` for the Sequence as a whole), and writes nothing.

``edit(sequence_id, based_on, fields) -> SequenceSaved``. ``fields`` is ``{"name": ..., "steps":
[...]}``; each Step is a mapping of the editable fields, ``delay_days``, ``purpose``,
``word_limit``, ``word_comparison``, ``allowed_content`` (a list of texts) and ``ask`` (a
number the Operator typed that isn't a whole number stays text, so the validator names its
field), plus ``stored_step``: the number of the stored Step the row keeps, or ``None`` for an
appended Step. Anything else in a Step is ignored. The checks, in order:

1. an unknown id is ``{"form": "No Sequence has this id"}``; a stored Sequence that no longer
   decodes (``InvalidSequences`` from the repository) is ``{"form": "The stored Sequence no
   longer reads back as valid: <field>: <reason>"}``;
2. ``based_on`` (the version the form showed) other than the stored version is ``{"form":
   "This Sequence changed since you opened it"}``;
3. the rows' shape: the first row must keep stored Step 1 (``{"steps[1]": "Step 1 can't be
   removed"}``), kept rows keep their stored order and come before every appended row, and each
   names a stored Step (``{"steps": "Steps can be removed or appended, never reordered"}``);
4. the Variant check, over the **active** Variants only (Story 11.8: a retired Variant gets no
   new Enrollment): a Campaign using this Sequence with a Variant whose ``step_angles`` names a
   Step beyond the proposed Step count is ``{"steps": "Campaign <key>'s Variant <name> has an
   angle note for Step <n>, so the Sequence needs at least <n> Steps"}``; then removing any
   stored Step ``k`` while such a Variant has a note on a Step ``k`` or later (which would move
   that note onto another Step) is ``{"steps": "Campaign <key>'s Variant <name> has angle notes
   on Steps after a removed Step; retire it and add a new one before removing Step <k>"}`` (the
   smallest such ``k``). The first problem, by Campaign key then Variant name;
5. validation: the editor builds each Step's file form itself, never from the form, for the
   fixed FR-4 fields: Step 1's ``delay_days`` is 0 and it keeps its stored ``subject_rule``; a
   later Step has none; every ``link_rule`` is ``footer_only`` and ``no_price`` and ``no_audit``
   are true; a kept Step keeps its stored ``segment_angles`` and an appended one has none. The
   result goes through ``nextron.domain.sequences.sequence_from_form`` unchanged, its
   ``InvalidSequences`` turned into a refusal on ``edited_field(field)``.

A Sequence equal to the stored one writes nothing (``changed`` false). Otherwise it stores
version n+1 through the ``sequences`` repository (its brief rows and its ``sequence_versions``
row, actor ``operator``) and one ``events`` row (entity ``sequences``, ``v<n>`` to ``v<n+1>``,
cause ``operator_edit``, actor ``operator``). Running Enrollments stay bound to the version row
they started with. After the commit it logs ``sequence_saved`` with ``key`` and ``version``
(only when something was written).
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nextron.app.queries import setup as setup_queries
from nextron.db import eventlog
from nextron.db.repos.setup import sequences as sequences_repo
from nextron.db.repos.setup.sequences import StoredSequence
from nextron.db.uow import current, use_case
from nextron.domain.campaign import VariantStatus
from nextron.domain.ids import is_ulid
from nextron.domain.segments import FORM_FIELD
from nextron.domain.sequences import (
    FIRST_STEP,
    FIRST_STEP_DELAY,
    InvalidSequences,
    LinkRule,
    SequenceDef,
    SequenceRefused,
    definition_text,
    edited_field,
    sequence_form,
    sequence_from_form,
)
from nextron.ports.clock import Clock

log = logging.getLogger(__name__)

SEQUENCE_SAVED: Final = "sequence_saved"
SEQUENCES_ENTITY: Final = "sequences"
OPERATOR_ACTOR: Final = "operator"
EDIT_CAUSE: Final = "operator_edit"
STEPS_FIELD: Final = "steps"
STORED_STEP: Final = "stored_step"  # the stored Step a form row keeps, or None when appended
EDITABLE: Final = (
    "delay_days",
    "purpose",
    "word_limit",
    "word_comparison",
    "allowed_content",
    "ask",
)
NO_SUCH_SEQUENCE: Final = "No Sequence has this id"
STALE: Final = "This Sequence changed since you opened it"
UNREADABLE: Final = "The stored Sequence no longer reads back as valid"
FIRST_KEPT: Final = "Step 1 can't be removed"
NOT_REORDERED: Final = "Steps can be removed or appended, never reordered"
NO_STEPS: Final = "A Sequence needs at least Step 1"


@dataclass(frozen=True, slots=True)
class SequenceSaved:
    """What a save did: the Sequence's id, key and (new or unchanged) version, and whether
    anything was written."""

    sequence_id: str
    key: str
    version: int
    changed: bool


def _rows(fields: Mapping[str, object]) -> list[Mapping[str, object]]:
    if not isinstance(fields, Mapping):
        raise TypeError(f"a Sequence save takes a mapping, not {type(fields).__name__}")
    steps = fields.get(STEPS_FIELD)
    if not isinstance(steps, list | tuple) or not all(isinstance(s, Mapping) for s in steps):
        raise TypeError("a Sequence save takes its steps as a list of mappings")
    return list(steps)


def _kept(rows: list[Mapping[str, object]], stored: StoredSequence) -> list[int | None]:
    """The stored Step each row keeps (``None``: appended); ``SequenceRefused`` when the rows
    drop Step 1 or reorder Steps."""
    kept: list[int | None] = []
    for row in rows:
        number = row.get(STORED_STEP)
        if number is not None and (
            isinstance(number, bool)
            or not isinstance(number, int)
            or not FIRST_STEP <= number <= len(stored.sequence.steps)
        ):
            raise SequenceRefused({STEPS_FIELD: NOT_REORDERED})
        kept.append(number)
    if not kept:
        raise SequenceRefused({STEPS_FIELD: NO_STEPS})
    if kept[0] != FIRST_STEP:
        raise SequenceRefused({f"{STEPS_FIELD}[{FIRST_STEP}]": FIRST_KEPT})
    previous = 0
    appended = False
    for number in kept:
        if number is None:
            appended = True
        elif appended or number <= previous:
            raise SequenceRefused({STEPS_FIELD: NOT_REORDERED})
        else:
            previous = number
    return kept


def _check_variants(sequence_id: str, kept: list[int | None], stored_steps: int) -> None:
    """Refuse a Step count below an active Variant angle note's Step, then (Story 11.8) the
    removal of a stored Step at or before an active Variant's note, for the first Campaign (by
    key) using this Sequence that has one. Retired Variants get no new Enrollment, so they never
    block."""
    step_count = len(kept)
    removed = sorted(set(range(FIRST_STEP, stored_steps + 1)) - {n for n in kept if n is not None})
    for campaign in setup_queries.campaigns():
        if campaign.sequence_id != sequence_id:
            continue
        for variant in campaign.variants:
            if variant.status != VariantStatus.ACTIVE:
                continue
            steps = [note.step for note in variant.step_angles]
            beyond = [step for step in steps if step > step_count]
            if beyond:
                step = max(beyond)
                raise SequenceRefused(
                    {
                        STEPS_FIELD: f"Campaign {campaign.key}'s Variant {variant.name} has an "
                        f"angle note for Step {step}, so the Sequence needs at least {step} Steps"
                    }
                )
            shifted = [number for number in removed if any(step >= number for step in steps)]
            if shifted:
                raise SequenceRefused(
                    {
                        STEPS_FIELD: f"Campaign {campaign.key}'s Variant {variant.name} has angle "
                        "notes on Steps after a removed Step; retire it and add a new one before "
                        f"removing Step {shifted[0]}"
                    }
                )


def _proposed(
    fields: Mapping[str, object],
    rows: list[Mapping[str, object]],
    kept: list[int | None],
    stored: StoredSequence,
) -> dict[str, object]:
    """The proposed Sequence's file form: the editable fields from the rows, the fixed FR-4
    fields and the kept angles from the stored Sequence."""
    stored_forms = sequence_form(stored.sequence)["steps"]
    assert isinstance(stored_forms, list)
    steps: list[dict[str, object]] = []
    for position, (row, number) in enumerate(zip(rows, kept, strict=True), start=FIRST_STEP):
        step: dict[str, object] = {name: row.get(name) for name in EDITABLE}
        step["link_rule"] = str(LinkRule.FOOTER_ONLY)
        step["no_price"] = True
        step["no_audit"] = True
        if position == FIRST_STEP:
            step["delay_days"] = FIRST_STEP_DELAY
            first = stored_forms[0]
            assert isinstance(first, dict)
            if "subject_rule" in first:
                step["subject_rule"] = first["subject_rule"]
        if number is not None:
            before = stored_forms[number - 1]
            assert isinstance(before, dict)
            if "segment_angles" in before:
                step["segment_angles"] = before["segment_angles"]
        steps.append(step)
    return {"key": stored.key, "name": fields.get("name"), "steps": steps}


def _validated(form: Mapping[str, object]) -> SequenceDef:
    try:
        return sequence_from_form(dict(form))
    except InvalidSequences as refused:
        raise SequenceRefused({edited_field(refused.field): refused.reason}) from None


def _record(sequence_id: str, before: int, after: int, at: datetime) -> None:
    eventlog.append(
        entity=SEQUENCES_ENTITY,
        entity_id=sequence_id,
        from_status=f"v{before}",
        to_status=f"v{after}",
        cause=EDIT_CAUSE,
        cause_ref=None,
        actor=OPERATOR_ACTOR,
        at=at,
    )


class SequenceEditor:
    """The Sequences page's edit; see the module docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @use_case
    def edit(
        self, sequence_id: str, based_on: int, fields: Mapping[str, object]
    ) -> SequenceSaved:
        """Store the Sequence's next version, unless the form is stale, refused or unchanged;
        see the module docstring."""
        rows = _rows(fields)
        now = self._clock.now()
        with current().transaction():
            try:
                stored = sequences_repo.by_id(sequence_id) if is_ulid(sequence_id) else None
            except InvalidSequences as unreadable:
                raise SequenceRefused({FORM_FIELD: f"{UNREADABLE}: {unreadable}"}) from None
            if stored is None:
                raise SequenceRefused({FORM_FIELD: NO_SUCH_SEQUENCE})
            if based_on != stored.version or isinstance(based_on, bool):
                raise SequenceRefused({FORM_FIELD: STALE})
            kept = _kept(rows, stored)
            _check_variants(stored.id, kept, len(stored.sequence.steps))
            sequence = _validated(_proposed(fields, rows, kept, stored))
            if definition_text(sequence) == definition_text(stored.sequence):
                return SequenceSaved(stored.id, stored.key, stored.version, False)
            version = stored.version + 1
            sequences_repo.update(stored.id, sequence, version, now, OPERATOR_ACTOR)
            _record(stored.id, stored.version, version, now)
        log.info(SEQUENCE_SAVED, extra={"key": stored.key, "version": version})
        return SequenceSaved(stored.id, stored.key, version, True)
