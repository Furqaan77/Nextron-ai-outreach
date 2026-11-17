"""The ``sequences`` and ``step_briefs`` tables' repository (FR-4, AD-3, AD-12): the only code
that reads or writes them.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. A Sequence is one ``sequences`` row (its ``key``, ``name`` and ``version``) and one
``step_briefs`` row per Step, keyed (``sequence_id``, ``step``), stored as typed columns from its
file form (``nextron.domain.sequences.sequence_form``): ``allowed_content`` and
``segment_angles`` as canonical JSON lists (``[]`` for no angles) and ``subject_rule`` as a
canonical JSON object, NULL when the Step has none. Canonical JSON is ``json.dumps`` with
``sort_keys``, ``ensure_ascii=False`` and no spaces. A Sequence is decoded back into its form,
its rows in Step order, and validated again through ``sequence_from_form``, so a row set that no
longer validates (a JSON column that isn't JSON, or Steps that aren't numbered 1 to n) raises
``InvalidSequences``.

- ``all_sequences()``: every Sequence as a ``StoredSequence``, by key;
- ``by_key(key)``: the Sequence ``key``, or ``None``;
- ``insert(sequence, at) -> id``: a new Sequence at version 1 and one brief row per Step, all
  created and updated at ``at``;
- ``update(sequence_id, sequence, version, at)``: sets the Sequence's ``name``, ``version`` and
  ``updated_at``; updates each brief row whose content differs (``updated_at`` = ``at``),
  inserts a row for each new Step and deletes the row of each Step no longer in the Sequence.
  An equal row is untouched.
"""

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy import delete as delete_rows
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.setup import SequenceRecord, StepBriefRecord
from nextron.db.uow import current
from nextron.domain.sequences import (
    KEY_PATTERN,
    InvalidSequences,
    SequenceDef,
    sequence_form,
    sequence_from_form,
)

FIRST_VERSION = 1
_JSON_COLUMNS = ("allowed_content", "subject_rule", "segment_angles")
_SCALAR_COLUMNS = (
    "delay_days",
    "purpose",
    "word_limit",
    "word_comparison",
    "ask",
    "link_rule",
    "no_price",
    "no_audit",
)
_CONTENT_COLUMNS = ("step", *_SCALAR_COLUMNS, *_JSON_COLUMNS)  # what a brief row's content is


@dataclass(frozen=True, slots=True)
class StoredSequence:
    """One ``sequences`` row with its Step briefs, decoded."""

    id: str
    key: str
    version: int
    created_at: datetime
    updated_at: datetime
    sequence: SequenceDef


_SEQUENCE_COLUMNS = (
    SequenceRecord.id,
    SequenceRecord.key,
    SequenceRecord.name,
    SequenceRecord.version,
    SequenceRecord.created_at,
    SequenceRecord.updated_at,
)
_BRIEF_COLUMNS = (
    StepBriefRecord.id,
    StepBriefRecord.sequence_id,
    StepBriefRecord.step,
    StepBriefRecord.delay_days,
    StepBriefRecord.purpose,
    StepBriefRecord.word_limit,
    StepBriefRecord.word_comparison,
    StepBriefRecord.allowed_content,
    StepBriefRecord.ask,
    StepBriefRecord.link_rule,
    StepBriefRecord.no_price,
    StepBriefRecord.no_audit,
    StepBriefRecord.subject_rule,
    StepBriefRecord.segment_angles,
)


def _json_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _brief_values(sequence: SequenceDef) -> list[dict[str, Any]]:
    """Each Step's brief columns (without ids and times), in Step order, from the file form."""
    values = []
    steps = sequence_form(sequence)["steps"]
    assert isinstance(steps, list)
    for number, form in enumerate(steps, start=1):
        assert isinstance(form, dict)
        subject_rule = form.get("subject_rule")
        values.append(
            {
                "step": number,
                **{name: form[name] for name in _SCALAR_COLUMNS},
                "allowed_content": _json_text(form["allowed_content"]),
                "subject_rule": None if subject_rule is None else _json_text(subject_rule),
                "segment_angles": _json_text(form.get("segment_angles", [])),
            }
        )
    return values


def _shown(key: object) -> str:
    return key if isinstance(key, str) and KEY_PATTERN.fullmatch(key) else "0"


def _json_value(text: object, key: object, step: int, column: str) -> object:
    """A JSON column's value, or ``InvalidSequences`` when it isn't JSON."""
    try:
        return json.loads(text)  # type: ignore[arg-type]
    except (TypeError, ValueError, RecursionError):  # JSONDecodeError is a ValueError
        raise InvalidSequences(
            f"sequences[{_shown(key)}].steps[{step}].{column}", "is not valid JSON"
        ) from None


def _stored(row: Row[Any], briefs: Sequence[Row[Any]]) -> StoredSequence:
    """The ``StoredSequence`` of one ``sequences`` row and its brief rows in Step order,
    validated again."""
    key = row.key
    steps: list[dict[str, object]] = []
    for number, brief in enumerate(briefs, start=1):
        if brief.step != number:
            raise InvalidSequences(
                f"sequences[{_shown(key)}].steps",
                f"the stored Steps aren't numbered 1 to {len(briefs)}",
            )
        form: dict[str, object] = {name: getattr(brief, name) for name in _SCALAR_COLUMNS}
        form["allowed_content"] = _json_value(brief.allowed_content, key, number, "allowed_content")
        if brief.subject_rule is not None:
            form["subject_rule"] = _json_value(brief.subject_rule, key, number, "subject_rule")
        form["segment_angles"] = _json_value(brief.segment_angles, key, number, "segment_angles")
        steps.append(form)
    return StoredSequence(
        id=row.id,
        key=key,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        sequence=sequence_from_form({"key": key, "name": row.name, "steps": steps}),
    )


def _briefs_of(sequence_ids: Sequence[str]) -> dict[str, list[Row[Any]]]:
    """The brief rows of each of ``sequence_ids``, in Step order."""
    found: dict[str, list[Row[Any]]] = defaultdict(list)
    if not sequence_ids:
        return found
    statement = (
        select(*_BRIEF_COLUMNS)
        .where(StepBriefRecord.sequence_id.in_(sequence_ids))
        .order_by(StepBriefRecord.sequence_id, StepBriefRecord.step)
    )
    for brief in current().session.execute(statement):
        found[brief.sequence_id].append(brief)
    return found


def all_sequences() -> tuple[StoredSequence, ...]:
    """Every stored Sequence, ordered by key."""
    statement = select(*_SEQUENCE_COLUMNS).order_by(SequenceRecord.key)
    rows = list(current().session.execute(statement))
    briefs = _briefs_of([row.id for row in rows])
    return tuple(_stored(row, briefs.get(row.id, [])) for row in rows)


def by_key(key: str) -> StoredSequence | None:
    """The stored Sequence ``key``, or ``None`` when it has no row."""
    statement = select(*_SEQUENCE_COLUMNS).where(SequenceRecord.key == str(key))
    row = current().session.execute(statement).one_or_none()
    if row is None:
        return None
    return _stored(row, _briefs_of([row.id]).get(row.id, []))


def _insert_brief(sequence_id: str, values: dict[str, Any], at: datetime) -> None:
    current().session.execute(
        insert_rows(StepBriefRecord).values(
            id=new_id(), sequence_id=sequence_id, **values, created_at=at, updated_at=at
        )
    )


def insert(sequence: SequenceDef, at: datetime) -> str:
    """Store a new Sequence at version 1 with one brief row per Step, created at ``at``; return
    its new ULID."""
    sequence_id = new_id()
    current().session.execute(
        insert_rows(SequenceRecord).values(
            id=sequence_id,
            key=sequence.key,
            name=sequence.name,
            version=FIRST_VERSION,
            created_at=at,
            updated_at=at,
        )
    )
    for values in _brief_values(sequence):
        _insert_brief(sequence_id, values, at)
    return sequence_id


def update(sequence_id: str, sequence: SequenceDef, version: int, at: datetime) -> None:
    """Set the name and version of the Sequence ``sequence_id`` and bring its brief rows to
    ``sequence``'s Steps, changed at ``at``; an equal brief row is untouched."""
    session = current().session
    session.execute(
        update_rows(SequenceRecord)
        .where(SequenceRecord.id == sequence_id)
        .values(name=sequence.name, version=version, updated_at=at)
    )
    stored = {brief.step: brief for brief in _briefs_of([sequence_id]).get(sequence_id, [])}
    wanted = _brief_values(sequence)
    for values in wanted:
        brief = stored.get(values["step"])
        if brief is None:
            _insert_brief(sequence_id, values, at)
        elif any(getattr(brief, name) != values[name] for name in _CONTENT_COLUMNS):
            session.execute(
                update_rows(StepBriefRecord)
                .where(StepBriefRecord.id == brief.id)
                .values(**values, updated_at=at)
            )
    kept = {values["step"] for values in wanted}
    gone = [brief.id for step, brief in stored.items() if step not in kept]
    if gone:
        session.execute(delete_rows(StepBriefRecord).where(StepBriefRecord.id.in_(gone)))
