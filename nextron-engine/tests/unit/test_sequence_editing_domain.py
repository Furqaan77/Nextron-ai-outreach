"""The Sequence editing helpers of ``nextron.domain.sequences`` (Story 11.7):
``definition_text``, ``SequenceRefused``, ``edited_field`` and ``sequence_form_diff``."""

import dataclasses
import json

import pytest

from nextron import main
from nextron.domain import seed_yaml
from nextron.domain.segments import FieldChange
from nextron.domain.sequences import (
    SequenceRefused,
    definition_text,
    edited_field,
    sequence_form,
    sequence_form_diff,
    sequence_from_form,
    validate_sequences,
)

[DEFAULT] = validate_sequences(
    seed_yaml.parse((main.SEED_DIR / main.SEQUENCES_FILE_NAME).read_text(encoding="utf-8"))
)


def test_definition_text_is_the_canonical_json_of_the_file_form() -> None:
    text = definition_text(DEFAULT)

    assert json.loads(text) == sequence_form(DEFAULT)
    assert text == json.dumps(
        sequence_form(DEFAULT), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    assert sequence_from_form(json.loads(text)) == DEFAULT


def test_sequence_refused_names_the_fields_never_a_value() -> None:
    refused = SequenceRefused({"steps[2].ask": "must not be blank", "form": "stale"})

    assert refused.problems == {"steps[2].ask": "must not be blank", "form": "stale"}
    assert str(refused) == "sequence refused: steps[2].ask, form"
    assert refused.code == "sequence_refused"


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ("sequences[default].name", "name"),
        ("sequences[0].steps", "steps"),
        ("sequences[default].steps[3]", "steps[3]"),
        ("sequences[default].steps[3].delay_days", "steps[3].delay_days"),
        ("sequences[default].steps[2].allowed_content[1]", "steps[2].allowed_content"),
        ("sequences[default].steps[1].subject_rule.word_limit", "steps[1].subject_rule"),
        ("sequences[default].key", "form"),
        ("(top level)", "form"),
        ("sequences[default].steps[0].ask", "steps"),  # no Step 0: the Steps as a whole
    ],
)
def test_edited_field_maps_a_validator_path_to_the_form_field(path: str, field: str) -> None:
    assert edited_field(path) == field


def test_the_diff_lists_changed_fields_by_step_and_whole_added_or_removed_steps() -> None:
    steps = list(DEFAULT.steps)
    steps[1] = dataclasses.replace(steps[1], ask="A new ask", word_limit=60)
    changed = dataclasses.replace(DEFAULT, name="Renamed", steps=tuple(steps[:3]))

    diff = sequence_form_diff(DEFAULT, changed)

    assert [change.path for change in diff] == [
        "name",
        "steps[2].word_limit",
        "steps[2].ask",
        "steps[4]",
    ]
    assert diff[0] == FieldChange("name", '"Default four-Step Sequence"', '"Renamed"')
    assert diff[1] == FieldChange("steps[2].word_limit", "70", "60")
    assert diff[3].new is None and diff[3].old is not None
    assert sequence_form_diff(DEFAULT, DEFAULT) == ()
    grown = sequence_form_diff(changed, DEFAULT)
    assert grown[-1].path == "steps[4]" and grown[-1].old is None
