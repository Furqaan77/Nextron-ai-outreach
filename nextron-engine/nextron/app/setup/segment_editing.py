"""Dashboard Segment editing (FR-1, AD-3, AD-4, AD-12; Story 11.5): part of the ``setup``
service, the only writer of ``segments`` and ``segment_versions``.

``SegmentEditor(clock)``: each method is a ``@use_case`` that reads the clock once and runs one
transaction. ``fields`` is the proposed Segment's file form (``nextron.domain.segments
.file_form``'s shape, as the Segments page builds it from its form: a number the Operator typed
that isn't a whole number stays text, so the validator names its field). Every refusal raises
``SegmentRefused(problems)``, ``{form field: text}`` (``nextron.domain.segments.edited_field``
names the field; ``form`` for the Segment as a whole), and writes nothing.

- ``create(fields) -> SegmentSaved``: validates (below), then inserts the Segment at version 1
  with its ``segment_versions`` row (actor ``operator``) and one ``events`` row (entity
  ``segments``, ``None`` to ``v1``, cause ``operator_create``, actor ``operator``). A new
  Segment is inert: nothing uses it until a Campaign does.
- ``edit(segment_id, based_on, fields) -> SegmentSaved``: an unknown id is ``{"form": "No
  Segment has this id"}``; ``based_on`` (the version the form showed) other than the stored
  version is ``{"form": "This Segment changed since you opened it"}``; a ``key`` in ``fields``
  other than the stored key is ``{"key": "A Segment's key can't change"}`` (the stored key is
  used when ``fields`` has none). Then it validates; a definition text equal to the stored one
  writes nothing (``changed`` false); otherwise it stores version n+1 with its
  ``segment_versions`` row and one ``events`` row ``v<n>`` to ``v<n+1>`` (cause
  ``operator_edit``, actor ``operator``).

**Validation.** The Story 2.1 validator, unchanged: first ``validate_segments`` over every
other stored Segment's file form (a stored Segment that no longer validates refuses the save
on ``form``: "A stored Segment no longer validates (<its message>)", or "(its stored
definition is not JSON)" for one that doesn't decode), then over those followed
by the proposed one (the edited Segment left out, so it is substituted; it comes last, so a
key it repeats is named on its own field). Then the one extra rule,
``segment_editing_rule``: "A Segment needs a decision-maker role and at least one Tier 1 or 2
Event".

No save enqueues a job or re-scores a Lead: scoring keeps each scored Lead on its stored
version until the Operator chooses "Re-score with the current definition". After the commit it
logs ``segment_saved`` with ``key`` and ``version`` (only when something was written).
"""

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nextron.db import eventlog
from nextron.db.repos.setup import segments as segments_repo
from nextron.db.uow import current, use_case
from nextron.domain.ids import is_ulid
from nextron.domain.segments import (
    FORM_FIELD,
    InvalidSegments,
    SegmentDef,
    SegmentRefused,
    definition_text,
    edited_field,
    segment_editing_rule,
    validate_segments,
)
from nextron.ports.clock import Clock

log = logging.getLogger(__name__)

SEGMENT_SAVED: Final = "segment_saved"
SEGMENTS_ENTITY: Final = "segments"
OPERATOR_ACTOR: Final = "operator"
CREATE_CAUSE: Final = "operator_create"
EDIT_CAUSE: Final = "operator_edit"
KEY_FIELD: Final = "key"
NO_SUCH_SEGMENT: Final = "No Segment has this id"
STALE: Final = "This Segment changed since you opened it"
KEY_FIXED: Final = "A Segment's key can't change"
STORED_INVALID: Final = "A stored Segment no longer validates"
NOT_JSON: Final = "its stored definition is not JSON"


@dataclass(frozen=True, slots=True)
class SegmentSaved:
    """What a save did: the Segment's id, key and (new or unchanged) version, and whether
    anything was written."""

    segment_id: str
    key: str
    version: int
    changed: bool


def _form(fields: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(fields, Mapping):
        raise TypeError(f"a Segment save takes a mapping, not {type(fields).__name__}")
    return dict(fields)


def _validated(form: Mapping[str, object], others: list[object]) -> SegmentDef:
    """The proposed Segment, validated after every other stored one; ``SegmentRefused``
    otherwise."""
    try:
        if others:
            validate_segments({"segments": others})
    except InvalidSegments as refused:
        raise SegmentRefused({FORM_FIELD: f"{STORED_INVALID} ({refused.message})"}) from None
    try:
        segment = validate_segments({"segments": [*others, dict(form)]})[-1]
    except InvalidSegments as refused:
        raise SegmentRefused({edited_field(refused.field, form): refused.reason}) from None
    broken = segment_editing_rule(segment)
    if broken is not None:
        raise SegmentRefused({edited_field(broken.field, form): broken.reason})
    return segment


def _others(leave_out: str | None) -> list[object]:
    """Every stored Segment's file form, by key, but ``leave_out``'s; a stored definition that
    isn't JSON refuses on ``form`` like any other stored Segment that no longer validates."""
    others: list[object] = []
    for row in segments_repo.all_segments():
        if row.id == leave_out:
            continue
        try:
            others.append(json.loads(row.definition))
        except ValueError:
            problem = f"{STORED_INVALID} ({NOT_JSON})"
            raise SegmentRefused({FORM_FIELD: problem}) from None
    return others


def _record(segment_id: str, before: int | None, after: int, cause: str, at: datetime) -> None:
    eventlog.append(
        entity=SEGMENTS_ENTITY,
        entity_id=segment_id,
        from_status=None if before is None else f"v{before}",
        to_status=f"v{after}",
        cause=cause,
        cause_ref=None,
        actor=OPERATOR_ACTOR,
        at=at,
    )


class SegmentEditor:
    """The Segments page's create and edit; see the module docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @use_case
    def create(self, fields: Mapping[str, object]) -> SegmentSaved:
        """Store a new Segment at version 1; see the module docstring."""
        form = _form(fields)
        now = self._clock.now()
        with current().transaction():
            segment = _validated(form, _others(None))
            text = definition_text(segment)
            segment_id = segments_repo.insert(segment.key, text, now, OPERATOR_ACTOR)
            _record(segment_id, None, segments_repo.FIRST_VERSION, CREATE_CAUSE, now)
        log.info(SEGMENT_SAVED, extra={"key": segment.key, "version": segments_repo.FIRST_VERSION})
        return SegmentSaved(segment_id, segment.key, segments_repo.FIRST_VERSION, True)

    @use_case
    def edit(self, segment_id: str, based_on: int, fields: Mapping[str, object]) -> SegmentSaved:
        """Store the Segment's next version, unless the form is stale, refused or unchanged;
        see the module docstring."""
        form = _form(fields)
        now = self._clock.now()
        with current().transaction():
            stored = segments_repo.get(segment_id) if is_ulid(segment_id) else None
            if stored is None:
                raise SegmentRefused({FORM_FIELD: NO_SUCH_SEGMENT})
            if based_on != stored.version or isinstance(based_on, bool):
                raise SegmentRefused({FORM_FIELD: STALE})
            if form.setdefault(KEY_FIELD, stored.key) != stored.key:
                raise SegmentRefused({KEY_FIELD: KEY_FIXED})
            segment = _validated(form, _others(stored.id))
            text = definition_text(segment)
            if text == stored.definition:
                return SegmentSaved(stored.id, stored.key, stored.version, False)
            version = stored.version + 1
            segments_repo.update(stored.id, text, version, now, OPERATOR_ACTOR)
            _record(stored.id, stored.version, version, EDIT_CAUSE, now)
        log.info(SEGMENT_SAVED, extra={"key": stored.key, "version": version})
        return SegmentSaved(stored.id, stored.key, version, True)
