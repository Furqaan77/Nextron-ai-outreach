"""``nextron seed sync`` loads ``seed/fallback_templates.yaml`` (FR-22, AD-12; Story 4.19): the
seventh seed file, its summary line, the file as the whole set, and the refusals (an unknown
placeholder, a fact not allowed for the Segment and every Market of its variant, an unknown
Segment, a variant no Market uses, a missing file), each with nothing loaded."""

import shutil
from pathlib import Path

import pytest

from nextron import main
from nextron.app.setup.api import check_seed
from nextron.domain.fallback_copy import InvalidFallbackTemplates, validate_fallback_templates
from nextron.domain.markets import validate_markets
from nextron.domain.offers import validate_offers
from nextron.domain.segments import validate_segments
from nextron.domain.sequences import validate_sequences
from tests.app.test_enroll import shipped
from tests.app.test_seed_sync import (  # noqa: F401  (the fixtures)
    config,
    database,
    query,
    sync,
    table_dump,
)

SHIPPED_TEXT = (main.SEED_DIR / main.FALLBACK_TEMPLATES_FILE_NAME).read_text(encoding="utf-8")
LABEL = "seed/fallback_templates.yaml"
FIRST = "fallback_templates: 16 created, 0 changed, 0 unchanged, 0 removed"
AGAIN = "fallback_templates: 0 created, 0 changed, 16 unchanged, 0 removed"
ROWS = (
    "SELECT s.key, f.step, f.english_variant, f.subject, f.body FROM fallback_templates f "
    "JOIN segments s ON s.id = f.segment_id ORDER BY s.key, f.step, f.english_variant"
)


def folder_with(tmp_path: Path, text: str | None, name: str = "seed") -> Path:
    """The shipped seed folder copied, with ``text`` as the templates file (``None``: none)."""
    folder = tmp_path / name
    shutil.copytree(main.SEED_DIR, folder)
    path = folder / main.FALLBACK_TEMPLATES_FILE_NAME
    if text is None:
        path.unlink()
    else:
        path.write_text(text, encoding="utf-8", newline="")
    return folder


def edited(old: str, new: str, text: str = SHIPPED_TEXT) -> str:
    assert text.count(old) >= 1, old
    return text.replace(old, new, 1)


def test_the_shipped_file_loads_sixteen_templates_then_changes_nothing(
    database: Path, tmp_path: Path
) -> None:
    first = sync(folder_with(tmp_path, SHIPPED_TEXT))

    assert first.code == 0, first.err
    assert first.out.endswith(FIRST + "\n")
    rows = query(database, ROWS)
    assert len(rows) == 16
    assert rows[0][:4] == ("law_private_ai", 1, "en_gb", "Client data and AI tools")
    assert all((step == 1) == (subject is not None) for _, step, _, subject, _ in rows)
    assert "\n\n" in rows[0][4] and "{fact:legal_ai}" in rows[0][4]

    again = sync(folder_with(tmp_path, SHIPPED_TEXT, "again"))

    assert again.code == 0 and again.out.endswith(AGAIN + "\n")
    assert query(database, ROWS) == rows


def test_the_file_is_the_whole_set(database: Path, tmp_path: Path) -> None:
    assert sync(folder_with(tmp_path, SHIPPED_TEXT)).code == 0
    changed = edited("Hi {first_name},", "Hello {first_name},")
    start = changed.index("  - segment: ops_automation\n    step: 4\n    english_variant: en_gb")
    trimmed = changed[:start]

    result = sync(folder_with(tmp_path, trimmed, "next"))

    assert result.code == 0, result.err
    assert result.out.endswith(
        "fallback_templates: 0 created, 1 changed, 14 unchanged, 1 removed\n"
        "  ops_automation/4/en_gb: removed, not in seed/fallback_templates.yaml\n"
    )
    rows = query(database, ROWS)
    assert len(rows) == 15
    assert rows[1][4].startswith("Hello {first_name},")  # law_private_ai/1/en_us


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            edited("'Hi {first_name},'", "'Hi {email},'"),
            f"{LABEL}: fallback_templates[law_private_ai/1/en_us].body[0]: holds an unknown "
            "placeholder; use {first_name}, {company}, {role} or {fact:<fact_id>}",
        ),
        (
            edited("{fact:aba_formal_opinion_512}", "{fact:sra_ai_notice}"),
            f"{LABEL}: fallback_templates[law_private_ai/2/en_us].body: {{fact:sra_ai_notice}} "
            "isn't an Approved Company Fact allowed for this Segment and the Market us",
        ),
        (
            edited("{fact:aba_formal_opinion_512}", "{fact:no_such_fact}"),
            f"{LABEL}: fallback_templates[law_private_ai/2/en_us].body: {{fact:no_such_fact}} "
            "isn't an Approved Company Fact allowed for this Segment and the Market us",
        ),
        (
            SHIPPED_TEXT.replace("segment: ops_automation", "segment: ops_unknown"),
            f"{LABEL}: fallback_templates[ops_unknown/1/en_us].segment: isn't the key of a "
            "Segment",
        ),
        (None, f"{LABEL}: the file doesn't exist"),
    ],
)
def test_a_bad_seed_fails_naming_the_file_and_key_with_nothing_loaded(
    database: Path, tmp_path: Path, text: str | None, message: str
) -> None:
    before = table_dump(database)

    result = sync(folder_with(tmp_path, text))

    assert (result.code, result.err) == (1, message + "\n")
    assert table_dump(database) == before


def test_a_bad_fact_after_a_good_sync_changes_nothing(database: Path, tmp_path: Path) -> None:
    assert sync(folder_with(tmp_path, SHIPPED_TEXT)).code == 0
    before = table_dump(database)
    bad = edited("{fact:sra_ai_notice}", "{fact:aba_formal_opinion_512}")

    result = sync(folder_with(tmp_path, bad, "bad"))

    assert result.code == 1
    assert "fallback_templates[law_private_ai/2/en_gb].body: {fact:aba_formal_opinion_512}" in (
        result.err
    )
    assert table_dump(database) == before


def test_a_variant_no_market_uses_is_refused_before_any_transaction() -> None:
    markets = tuple(
        market
        for market in shipped(main.MARKETS_FILE_NAME, validate_markets)
        if market.code == "us"
    )

    with pytest.raises(InvalidFallbackTemplates) as caught:
        check_seed(
            shipped(main.SEGMENTS_FILE_NAME, validate_segments),
            markets,
            shipped(main.SEQUENCES_FILE_NAME, validate_sequences),
            shipped(main.OFFERS_FILE_NAME, validate_offers),
            (),
            fallback_templates=shipped(
                main.FALLBACK_TEMPLATES_FILE_NAME, validate_fallback_templates
            ),
        )

    assert caught.value.message == (
        "fallback_templates[law_private_ai/1/en_gb].english_variant: no Market in "
        "seed/markets.yaml uses this English variant"
    )


def test_check_seed_refuses_a_repeated_template() -> None:
    found = shipped(main.FALLBACK_TEMPLATES_FILE_NAME, validate_fallback_templates)

    with pytest.raises(ValueError, match="law_private_ai/1/en_us"):
        check_seed(
            shipped(main.SEGMENTS_FILE_NAME, validate_segments),
            shipped(main.MARKETS_FILE_NAME, validate_markets),
            (),
            (),
            (),
            fallback_templates=(found[0], found[0]),
        )
