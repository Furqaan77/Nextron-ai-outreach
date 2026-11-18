"""Campaigns, Offers and Variants (FR-3, AD-4, AD-5, NFR-3, NFR-7, Story 4.2) on real units of
work in a temporary data dir: the seed rows of the spec's I/O matrix through
``main.seed_sync(env="test", seed_dir=...)``, the ``Campaigns`` use cases with their events and
logs, ``queries.setup.campaign_active`` (the live country included), the database triggers and
constraints, a row that no longer decodes, today in ``sender_timezone`` (set and unset) and the
command's help."""

import dataclasses
import io
import logging
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nextron import main
from nextron.app.ops.api import Seeds
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import (
    CampaignChanged,
    Campaigns,
    LiaSaved,
    Setup,
    VariantAdded,
)
from nextron.bootstrap import BootstrapConfig, load_bootstrap
from nextron.db.migrations import head_revision
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import seed_yaml
from nextron.domain.campaign import (
    LIA_FIRST,
    TOO_MANY_VARIANTS,
    CampaignDef,
    CampaignRefused,
    CampaignStatus,
    InvalidCampaigns,
    Lia,
    StepAngleNote,
    VariantStatus,
    campaign_form,
    validate_campaigns,
)
from nextron.domain.company_facts import validate_company_facts
from nextron.domain.markets import validate_markets
from nextron.domain.offers import InvalidOffers, OfferDef, offer_form, validate_offers
from nextron.domain.segments import validate_segments
from nextron.domain.sequences import validate_sequences
from nextron.domain.settings_schema import InvalidSettingValue, Key
from tests.fakes.clock import DEFAULT_NOW, FrozenClock
from tests.migration_chains import run_sql, sha256

SEED_NAMES = (
    main.SEGMENTS_FILE_NAME,
    main.MARKETS_FILE_NAME,
    main.SEQUENCES_FILE_NAME,
    main.COMPANY_FACTS_FILE_NAME,
    main.OFFERS_FILE_NAME,
    main.CAMPAIGNS_FILE_NAME,
)
SHIPPED = {name: (main.SEED_DIR / name).read_text(encoding="utf-8") for name in SEED_NAMES}
OFFERS_TEXT = SHIPPED[main.OFFERS_FILE_NAME]
CAMPAIGNS_TEXT = SHIPPED[main.CAMPAIGNS_FILE_NAME]
SEQUENCES_TEXT = SHIPPED[main.SEQUENCES_FILE_NAME]
KEYS = ("law_private_ai_us", "law_private_ai_uk", "ops_automation_us", "ops_automation_uk")
LATER = DEFAULT_NOW + timedelta(days=3)
STAMP = "2026-11-16T09:00:00.000000Z"
LATER_STAMP = "2026-11-19T09:00:00.000000Z"
OFFERS_FIRST = "offers: 1 changed, 0 unchanged\n  default: created (version 1)\n"
CAMPAIGNS_FIRST = "campaigns: 4 changed, 0 unchanged\n" + "".join(
    f"  {key}: created (version 1)\n" for key in KEYS
)
OFFERS_UNCHANGED = "offers: 0 changed, 1 unchanged\n"
CAMPAIGNS_UNCHANGED = "campaigns: 0 changed, 4 unchanged\n"
NO_CAMPAIGN = "No Campaign has this id"
ONLY_UK = "Only a UK Campaign records a legitimate-interests assessment"


@dataclasses.dataclass
class Run:
    code: int
    out: str
    err: str


@pytest.fixture
def config() -> BootstrapConfig:
    return load_bootstrap()


@pytest.fixture
def database(config: BootstrapConfig) -> Path:
    """``nextron.db`` at head in the test data dir, as ``serve`` leaves it."""
    config.data_dir.mkdir(parents=True)
    units, revision = main.open_database(config)
    units.engine.dispose()
    assert revision == head_revision()
    return config.data_dir / main.DB_FILE_NAME


@pytest.fixture
def units(database: Path, config: BootstrapConfig) -> Iterator[UnitOfWorkFactory]:
    factory = main.connect_database(config)
    yield factory
    factory.engine.dispose()


def seed_dir(
    tmp_path: Path,
    *,
    campaigns: str = CAMPAIGNS_TEXT,
    offers: str = OFFERS_TEXT,
    sequences: str = SEQUENCES_TEXT,
    name: str = "seed",
) -> Path:
    folder = tmp_path / name
    folder.mkdir()
    texts = {
        **SHIPPED,
        main.CAMPAIGNS_FILE_NAME: campaigns,
        main.OFFERS_FILE_NAME: offers,
        main.SEQUENCES_FILE_NAME: sequences,
    }
    for file_name, text in texts.items():
        (folder / file_name).write_text(text, encoding="utf-8", newline="")
    return folder


def edited(old: str, new: str, text: str = CAMPAIGNS_TEXT) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


def sync(folder: Path | None = None, *, clock: FrozenClock | None = None) -> Run:
    out, err = io.StringIO(), io.StringIO()
    code = main.seed_sync(
        env="test",
        seed_dir=folder,
        clock=clock if clock is not None else FrozenClock(),
        out=out,
        err=err,
    )
    return Run(code, out.getvalue(), err.getvalue())


def query(path: Path, sql: str) -> list[tuple[Any, ...]]:
    reader = sqlite3.connect(path)
    try:
        return reader.execute(sql).fetchall()
    finally:
        reader.close()


def campaign_rows(path: Path) -> list[tuple[Any, ...]]:
    """``(key, name, status, lia_ref, lia_date, version)`` of each Campaign, by key."""
    return query(
        path,
        "SELECT key, name, status, lia_ref, lia_date, version FROM campaigns ORDER BY key",
    )


def variant_rows(path: Path) -> list[tuple[Any, ...]]:
    """``(campaign key, name, status, step_angles)`` of each Variant."""
    return query(
        path,
        "SELECT c.key, v.name, v.status, v.step_angles FROM variants v "
        "JOIN campaigns c ON c.id = v.campaign_id ORDER BY c.key, v.name",
    )


def event_rows(path: Path, entity: str | None = None) -> list[tuple[Any, ...]]:
    where = "" if entity is None else f" WHERE entity = '{entity}'"
    return query(
        path,
        "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor, at "
        f"FROM events{where} ORDER BY rowid",
    )


def ids(units: UnitOfWorkFactory) -> dict[str, str]:
    with units.open(read_only=True):
        return {seeded.key: seeded.id for seeded in setup_queries.campaigns()}


def active(units: UnitOfWorkFactory, campaign_id: str) -> bool:
    with units.open(read_only=True):
        return setup_queries.campaign_active(campaign_id)


def run(units: UnitOfWorkFactory, call: str, *args: Any, clock: FrozenClock | None = None) -> Any:
    with units.open():
        return getattr(Campaigns(clock or FrozenClock()), call)(*args)


def refusal(units: UnitOfWorkFactory, call: str, *args: Any, **kwargs: Any) -> dict[str, str]:
    with pytest.raises(CampaignRefused) as caught:
        run(units, call, *args, **kwargs)
    return caught.value.problems


def set_timezone(units: UnitOfWorkFactory, name: str) -> None:
    with units.open():
        Setup(FrozenClock()).save({"sender_timezone": name})


# The seed: first and second sync ----------------------------------------------------------------


def test_the_first_sync_creates_the_offer_and_four_draft_campaigns(database: Path) -> None:
    result = sync()

    assert (result.code, result.err) == (0, "")
    assert result.out.endswith(OFFERS_FIRST + CAMPAIGNS_FIRST)
    assert campaign_rows(database) == [
        ("law_private_ai_uk", "Law-PrivateAI × UK", "draft", None, None, 1),
        ("law_private_ai_us", "Law-PrivateAI × US", "draft", None, None, 1),
        ("ops_automation_uk", "Ops-Automation × UK", "draft", None, None, 1),
        ("ops_automation_us", "Ops-Automation × US", "draft", None, None, 1),
    ]
    assert variant_rows(database) == [(key, "A", "active", "[]") for key in sorted(KEYS)]
    assert query(
        database,
        "SELECT DISTINCT o.key, s.key FROM campaigns c JOIN offers o ON o.id = c.offer_id "
        "JOIN sequences s ON s.id = c.sequence_id",
    ) == [("default", "default")]
    offers = event_rows(database, "offers")
    campaigns = event_rows(database, "campaigns")
    assert [(e[2], e[3], e[4], e[5], e[6], e[7]) for e in offers + campaigns] == [
        (None, "v1", "seed_sync", None, "seed", STAMP)
    ] * 5
    keys_in_event_order = [
        query(database, f"SELECT key FROM campaigns WHERE id = '{e[1]}'")[0][0] for e in campaigns
    ]
    assert keys_in_event_order == list(KEYS)  # file order


def test_a_second_sync_changes_nothing(database: Path) -> None:
    sync()
    before = sha256(database)

    result = sync(clock=FrozenClock(LATER))

    assert (result.code, result.err) == (0, "")
    assert result.out.endswith(OFFERS_UNCHANGED + CAMPAIGNS_UNCHANGED)
    assert sha256(database) == before


# The seed: edits ---------------------------------------------------------------------------------


def test_a_changed_name_updates_in_place_and_never_touches_status_or_lia(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    sync()
    uk = ids(units)["law_private_ai_uk"]
    run(units, "activate", uk, "LIA-2026-01", "2026-11-16")
    events_before = event_rows(database)
    text = edited("    name: Law-PrivateAI × UK\n", "    name: Law UK\n")

    result = sync(seed_dir(tmp_path, campaigns=text), clock=FrozenClock(LATER))

    assert result.out.endswith(
        "campaigns: 1 changed, 3 unchanged\n  law_private_ai_uk: changed (version 1 -> 2)\n"
    )
    assert campaign_rows(database)[0] == (
        "law_private_ai_uk", "Law UK", "active", "LIA-2026-01", "2026-11-16", 2
    )
    assert event_rows(database) == [
        *events_before,
        ("campaigns", uk, "v1", "v2", "seed_sync", None, "seed", LATER_STAMP),
    ]
    assert active(units, uk)


def test_changed_offer_terms_give_the_offer_version_2(database: Path, tmp_path: Path) -> None:
    sync()
    events_before = event_rows(database)
    offers = edited(
        "        terms: Fixed fee, credited in full toward a build.\n",
        "        terms: Fixed fee.\n",
        OFFERS_TEXT,
    )

    result = sync(seed_dir(tmp_path, offers=offers), clock=FrozenClock(LATER))

    assert "offers: 1 changed, 0 unchanged\n  default: changed (version 1 -> 2)\n" in result.out
    assert result.out.endswith(CAMPAIGNS_UNCHANGED)
    [(offer_id, version)] = query(database, "SELECT id, version FROM offers")
    assert version == 2
    assert event_rows(database) == [
        *events_before,
        ("offers", offer_id, "v1", "v2", "seed_sync", None, "seed", LATER_STAMP),
    ]


def with_variants(*lines: str, key: str = "law_private_ai_us") -> str:
    """The shipped file with Campaign ``key``'s Variant lines replaced by ``lines``."""
    old = "    variants:\n      - name: A\n        status: active\n"
    start = CAMPAIGNS_TEXT.index(f"  - key: {key}\n")
    at = CAMPAIGNS_TEXT.index(old, start)
    new = "    variants:\n" + "".join(lines)
    return CAMPAIGNS_TEXT[:at] + new + CAMPAIGNS_TEXT[at + len(old) :]


A_ACTIVE = "      - name: A\n        status: active\n"
B_ACTIVE = "      - name: B\n        status: active\n"
B_INACTIVE = "      - name: B\n        status: inactive\n"
C_ACTIVE = "      - name: C\n        status: active\n"


def test_an_added_variant_then_its_retirement_each_add_a_version(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    sync()
    us = ids(units)["law_private_ai_us"]
    run(units, "activate", us)

    added = sync(seed_dir(tmp_path, campaigns=with_variants(A_ACTIVE, B_ACTIVE), name="b"))
    retired = sync(seed_dir(tmp_path, campaigns=with_variants(A_ACTIVE, B_INACTIVE), name="r"))

    assert "  law_private_ai_us: changed (version 1 -> 2)\n" in added.out
    assert "  law_private_ai_us: changed (version 2 -> 3)\n" in retired.out
    assert [row for row in variant_rows(database) if row[0] == "law_private_ai_us"] == [
        ("law_private_ai_us", "A", "active", "[]"),
        ("law_private_ai_us", "B", "inactive", "[]"),
    ]
    assert campaign_rows(database)[1][2:] == ("active", None, None, 3)
    statuses = [(e[2], e[3]) for e in event_rows(database, "campaigns") if e[1] == us]
    assert statuses == [(None, "v1"), ("draft", "active"), ("v1", "v2"), ("v2", "v3")]


def test_notes_are_stored_as_canonical_json(database: Path, tmp_path: Path) -> None:
    notes = (
        "      - name: A\n        status: active\n        step_angles:\n"
        "          - step: 2\n            note: Lead with the ABA Formal Opinion 512 angle\n"
    )

    result = sync(seed_dir(tmp_path, campaigns=with_variants(notes)))

    assert result.code == 0, result.err
    assert ("law_private_ai_us", "A", "active",
            '[{"note":"Lead with the ABA Formal Opinion 512 angle","step":2}]') in (
        variant_rows(database)
    )


# The seed: refusals ------------------------------------------------------------------------------


def test_a_third_variant_in_the_file_is_refused(database: Path, tmp_path: Path) -> None:
    sync()
    before = sha256(database)

    result = sync(seed_dir(tmp_path, campaigns=with_variants(A_ACTIVE, B_ACTIVE, C_ACTIVE)))

    assert (result.code, result.err) == (
        1,
        "seed/campaigns.yaml: campaigns[law_private_ai_us].variants: "
        f"{TOO_MANY_VARIANTS}\n",
    )
    assert sha256(database) == before


def test_stored_a_and_c_with_a_file_of_a_and_b_is_refused(
    database: Path, tmp_path: Path
) -> None:
    stored = seed_dir(tmp_path, campaigns=with_variants(A_ACTIVE, C_ACTIVE), name="ac")
    assert sync(stored).code == 0
    before = sha256(database)

    result = sync(seed_dir(tmp_path, campaigns=with_variants(A_ACTIVE, B_ACTIVE), name="ab"))

    assert (result.code, result.err) == (
        1,
        "seed/campaigns.yaml: campaigns[law_private_ai_us].variants: "
        f"{TOO_MANY_VARIANTS}\n",
    )
    assert sha256(database) == before


def test_a_stored_variant_left_out_is_refused(database: Path, tmp_path: Path) -> None:
    sync(seed_dir(tmp_path, campaigns=with_variants(A_ACTIVE, B_ACTIVE), name="ab"))
    before = sha256(database)

    result = sync(seed_dir(tmp_path, campaigns=with_variants(B_ACTIVE), name="b"))

    assert (result.code, result.err) == (
        1,
        "seed/campaigns.yaml: campaigns[law_private_ai_us].variants: leaves out the stored "
        "Variant A: keep it, and set its status to inactive to retire it\n",
    )
    assert sha256(database) == before


def in_campaign(key: str, old: str, new: str, text: str = CAMPAIGNS_TEXT) -> str:
    """``text`` with the first ``old`` after Campaign ``key``'s first line made ``new``."""
    start = text.index(f"  - key: {key}\n")
    at = text.index(old, start)
    return text[:at] + new + text[at + len(old) :]


SWAPPED_SEGMENTS = in_campaign(
    "law_private_ai_us",
    "segment: law_private_ai",
    "segment: ops_automation",
    in_campaign("ops_automation_us", "segment: ops_automation", "segment: law_private_ai"),
)
SWAPPED_MARKETS = in_campaign(
    "law_private_ai_us",
    "market: us",
    "market: uk",
    in_campaign("law_private_ai_uk", "market: uk", "market: us"),
)


@pytest.mark.parametrize(
    ("campaigns", "offers", "expected"),
    [
        (
            SWAPPED_SEGMENTS,
            OFFERS_TEXT,
            "seed/campaigns.yaml: campaigns[law_private_ai_us].segment: can't change: a "
            "Campaign keeps its Segment and Market",
        ),
        (
            SWAPPED_MARKETS,
            OFFERS_TEXT,
            "seed/campaigns.yaml: campaigns[law_private_ai_us].market: can't change: a "
            "Campaign keeps its Segment and Market",
        ),
        (
            with_variants("      - name: A\n        status: inactive\n"),
            OFFERS_TEXT,
            "seed/campaigns.yaml: campaigns[law_private_ai_us].variants: A Campaign needs an "
            "active Variant",
        ),
        (
            in_campaign("ops_automation_us", "segment: ops_automation", "segment: law_private_ai"),
            OFFERS_TEXT,
            "seed/campaigns.yaml: campaigns[ops_automation_us]: A Campaign already exists for "
            "this Segment and Market",
        ),
        (
            in_campaign("law_private_ai_us", "offer: default", "offer: premium"),
            OFFERS_TEXT,
            "seed/campaigns.yaml: campaigns[law_private_ai_us].offer: isn't the key of an Offer "
            "in this seed sync",
        ),
        (
            with_variants(
                A_ACTIVE + "        step_angles:\n          - step: 5\n"
                "            note: A fifth Step\n"
            ),
            OFFERS_TEXT,
            "seed/campaigns.yaml: campaigns[law_private_ai_us].variants[A].step_angles[0].step: "
            "must be at most 4: the Sequence default has 4 Steps",
        ),
        (
            with_variants(
                A_ACTIVE + "        step_angles:\n          - step: 1\n"
                "            note: The audit is only '$2,500'\n"
            ),
            OFFERS_TEXT,
            "seed/campaigns.yaml: campaigns[law_private_ai_us].variants[A].step_angles[0].note: "
            "An angle note can't name a price (FR-4)",
        ),
        (
            with_variants(
                A_ACTIVE + "        step_angles:\n          - step: 2\n"
                "            note: ABA Formal Opinion 512 applies\n",
                key="law_private_ai_uk",
            ),
            OFFERS_TEXT,
            "seed/campaigns.yaml: campaigns[law_private_ai_uk].variants[A].step_angles[0].note: "
            "names ABA Formal Opinion 512, which only a US Campaign's note may (this Market is "
            "GB)",
        ),
        (
            CAMPAIGNS_TEXT,
            edited(
                "      - kind: reply\n        name: A reply\n",
                "      - kind: build\n        name: A reply\n",
                OFFERS_TEXT,
            ),
            "seed/offers.yaml: offers[default].ladder[0].kind: must be reply: the first ask is "
            "only a reply (FR-4)",
        ),
        (
            CAMPAIGNS_TEXT,
            edited("        price_usd_min: 2500\n        price_usd_max: 3500\n", "", OFFERS_TEXT),
            "seed/offers.yaml: offers[default].ladder[1].price_usd_min: missing: an audit rung "
            "needs both prices",
        ),
        (
            edited("  - key: law_private_ai_us\n", "  - key: law_ai_us\n"),
            OFFERS_TEXT,
            "seed/campaigns.yaml: campaigns[law_ai_us]: uses the Segment and Market of the "
            "stored Campaign law_private_ai_us, which is no longer in the file: keep that "
            "Campaign's key",
        ),
    ],
    ids=[
        "segment-change",
        "market-change",
        "no-active",
        "duplicate-pair",
        "unknown-offer",
        "step-5-of-4",
        "price-note",
        "aba-on-uk",
        "offer-reply-first",
        "audit-without-prices",
        "renamed-key",
    ],
)
def test_an_invalid_seed_names_its_field_and_changes_nothing(
    database: Path, tmp_path: Path, campaigns: str, offers: str, expected: str
) -> None:
    sync()
    before = sha256(database)

    result = sync(seed_dir(tmp_path, campaigns=campaigns, offers=offers))

    assert (result.code, result.out, result.err) == (1, "", expected + "\n")
    assert sha256(database) == before


def test_the_reply_rung_must_come_first(database: Path, tmp_path: Path) -> None:
    offers = edited(
        "      - kind: reply\n        name: A reply\n"
        "        terms: The first ask is only a reply; Steps never name a price or propose the "
        "audit.\n",
        "",
        OFFERS_TEXT,
    )

    result = sync(seed_dir(tmp_path, offers=offers))

    assert (result.code, result.err) == (
        1,
        "seed/offers.yaml: offers[default].ladder[0].kind: must be reply: the first ask is "
        "only a reply (FR-4)\n",
    )


def test_a_stored_campaign_or_offer_left_out_stays_and_is_reported(
    database: Path, tmp_path: Path
) -> None:
    extra_offer = OFFERS_TEXT + (
        "  - key: extra\n    name: Extra\n    ladder:\n      - kind: reply\n        name: R\n"
    )
    sync(seed_dir(tmp_path, offers=extra_offer, name="with_extra"))
    without_ops_uk = CAMPAIGNS_TEXT[: CAMPAIGNS_TEXT.index("  - key: ops_automation_uk\n")]
    before = campaign_rows(database)
    events_before = event_rows(database)

    result = sync(seed_dir(tmp_path, campaigns=without_ops_uk, name="without"))

    assert result.code == 0, result.err
    assert result.out.endswith(
        "offers: 0 changed, 1 unchanged\n"
        "  extra: not in seed/offers.yaml, left as it is (version 1)\n"
        "campaigns: 0 changed, 3 unchanged\n"
        "  ops_automation_uk: not in seed/campaigns.yaml, left as it is (version 1)\n"
    )
    assert campaign_rows(database) == before
    assert event_rows(database) == events_before


def test_a_stored_campaign_that_no_longer_decodes(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    run_sql(database, "UPDATE variants SET step_angles = 'x' WHERE name = 'A'")

    with units.open(read_only=True), pytest.raises(InvalidCampaigns) as caught:
        setup_queries.campaigns()
    result = sync()

    assert caught.value.field == "campaigns[law_private_ai_uk].variants"
    assert (result.code, result.err) == (
        1,
        "seed/campaigns.yaml: campaigns[law_private_ai_uk].variants: a stored Variant's "
        "step_angles is not valid JSON\n",
    )


# The use cases -----------------------------------------------------------------------------------


def test_activate_a_us_campaign(
    database: Path, units: UnitOfWorkFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")
    sync()
    us = ids(units)["law_private_ai_us"]
    assert not active(units, us)

    changed = run(units, "activate", us)

    assert changed == CampaignChanged(
        us, "law_private_ai_us", "Law-PrivateAI × US", CampaignStatus.DRAFT, CampaignStatus.ACTIVE
    )
    assert campaign_rows(database)[1][2] == "active"
    assert event_rows(database)[-1] == (
        "campaigns", us, "draft", "active", "operator_activate", None, "operator", STAMP
    )
    assert active(units, us)
    [record] = [r for r in caplog.records if r.getMessage() == "campaign_activated"]
    assert record.campaign == "law_private_ai_us"  # type: ignore[attr-defined]


@pytest.mark.parametrize(("ref", "day"), [(None, None), ("", ""), ("LIA-2026-01", None)])
def test_activate_a_uk_campaign_without_an_lia_is_refused(
    database: Path, units: UnitOfWorkFactory, ref: str | None, day: str | None
) -> None:
    sync()
    uk = ids(units)["law_private_ai_uk"]
    before = sha256(database)

    problems = refusal(units, "activate", uk, ref, day)

    assert problems["form"] == LIA_FIRST
    assert problems["lia_date"] == "Enter a value"
    assert campaign_rows(database)[0][2:5] == ("draft", None, None)
    assert sha256(database) == before


def test_activate_a_uk_campaign_with_its_lia(
    database: Path, units: UnitOfWorkFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")
    sync()
    uk = ids(units)["law_private_ai_uk"]

    run(units, "activate", uk, " LIA-2026-01 ", "2026-11-16")

    assert campaign_rows(database)[0][2:5] == ("active", "LIA-2026-01", "2026-11-16")
    assert event_rows(database)[-2:] == [
        ("campaigns", uk, "draft", "draft", "operator_record_lia", None, "operator", STAMP),
        ("campaigns", uk, "draft", "active", "operator_activate", None, "operator", STAMP),
    ]
    assert active(units, uk)
    with units.open(read_only=True):
        found = setup_queries.campaign(uk)
    assert found is not None and found.lia == Lia("LIA-2026-01", DEFAULT_NOW.date())
    names = [r.getMessage() for r in caplog.records if r.getMessage().startswith("campaign_")]
    assert names == ["campaign_lia_saved", "campaign_activated"]


@pytest.mark.parametrize(
    ("ref", "day", "problems"),
    [
        ("LIA", "2026-11-17", {"lia_date": "can't be in the future"}),
        ("LIA", "2026-02-30", {"lia_date": "Use a date written YYYY-MM-DD"}),
        ("r" * 201, "2026-11-16", {"lia_ref": "must be at most 200 characters"}),
    ],
)
def test_a_bad_lia_is_refused_and_nothing_is_stored(
    database: Path, units: UnitOfWorkFactory, ref: str, day: str, problems: dict[str, str]
) -> None:
    sync()
    uk = ids(units)["law_private_ai_uk"]
    before = sha256(database)

    assert refusal(units, "activate", uk, ref, day) == problems
    assert sha256(database) == before


def test_a_us_campaign_records_no_lia(database: Path, units: UnitOfWorkFactory) -> None:
    sync()
    us = ids(units)["law_private_ai_us"]

    assert refusal(units, "activate", us, "LIA", "2026-11-16") == {"form": ONLY_UK}
    assert refusal(units, "activate", us, "", None) == {"form": ONLY_UK}
    assert refusal(units, "record_lia", us, "LIA", "2026-11-16") == {"form": ONLY_UK}
    assert campaign_rows(database)[1][2:5] == ("draft", None, None)


def test_pause_and_resume(database: Path, units: UnitOfWorkFactory) -> None:
    sync()
    us = ids(units)["law_private_ai_us"]
    run(units, "activate", us)

    paused = run(units, "pause", us)
    after_pause = active(units, us)
    resumed = run(units, "resume", us)

    assert (paused.from_status, paused.to_status) == ("active", "paused")
    assert (resumed.from_status, resumed.to_status) == ("paused", "active")
    assert (after_pause, active(units, us)) == (False, True)
    assert [e[2:5] for e in event_rows(database)[-2:]] == [
        ("active", "paused", "operator_pause"),
        ("paused", "active", "operator_resume"),
    ]


@pytest.mark.parametrize(
    ("setup_calls", "call", "reason"),
    [
        ((), "pause", "This Campaign is draft, so it can't be paused"),
        ((), "resume", "This Campaign is draft, so it can't be resumed"),
        (("activate",), "activate", "This Campaign is active, so it can't be activated"),
        (("activate",), "resume", "This Campaign is active, so it can't be resumed"),
        (("activate", "pause"), "pause", "This Campaign is paused, so it can't be paused"),
        (("activate", "pause"), "activate", "This Campaign is paused, so it can't be activated"),
    ],
)
def test_an_illegal_transition_is_refused(
    database: Path,
    units: UnitOfWorkFactory,
    setup_calls: tuple[str, ...],
    call: str,
    reason: str,
) -> None:
    sync()
    us = ids(units)["law_private_ai_us"]
    for name in setup_calls:
        run(units, name, us)
    before = sha256(database)

    assert refusal(units, call, us) == {"form": reason}
    assert sha256(database) == before


@pytest.mark.parametrize("campaign_id", ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "x", 5])
@pytest.mark.parametrize("call", ["activate", "pause", "resume"])
def test_an_unknown_campaign_is_refused(
    database: Path, units: UnitOfWorkFactory, campaign_id: Any, call: str
) -> None:
    sync()

    assert refusal(units, call, campaign_id) == {"form": NO_CAMPAIGN}
    assert refusal(units, "record_lia", campaign_id, "LIA", "2026-11-16") == {"form": NO_CAMPAIGN}
    assert refusal(units, "add_variant", campaign_id, "B") == {"form": NO_CAMPAIGN}
    assert not active(units, campaign_id)


def test_record_lia_edits_a_uk_campaign_in_any_status(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    uk = ids(units)["law_private_ai_uk"]
    draft = run(units, "record_lia", uk, "LIA-0", "2026-11-01")  # a draft may record it too
    run(units, "activate", uk, "LIA-2026-01", "2026-11-16")
    events_before = event_rows(database)

    saved = run(units, "record_lia", uk, "LIA-2026-02", "2026-11-15")
    again = run(units, "record_lia", uk, "LIA-2026-02", "2026-11-15")

    assert (draft, saved, again) == (LiaSaved(uk, True), LiaSaved(uk, True), LiaSaved(uk, False))
    assert campaign_rows(database)[0][2:5] == ("active", "LIA-2026-02", "2026-11-15")
    assert event_rows(database) == [
        *events_before,
        ("campaigns", uk, "active", "active", "operator_record_lia", None, "operator", STAMP),
    ]
    with units.open(read_only=True):
        found = setup_queries.campaign(uk)
    assert found is not None and found.lia is not None and found.lia.ref == "LIA-2026-02"


def test_record_lia_refuses_a_bad_value(database: Path, units: UnitOfWorkFactory) -> None:
    sync()
    uk = ids(units)["law_private_ai_uk"]

    assert refusal(units, "record_lia", uk, "", "2026-11-16") == {
        "lia_ref": "Enter a value",
        "form": LIA_FIRST,
    }


def test_add_variant_then_a_third_is_refused(
    database: Path, units: UnitOfWorkFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")
    sync()
    us = ids(units)["law_private_ai_us"]

    added = run(units, "add_variant", us, "B", (StepAngleNote(2, "Client-data angle"),))
    problems = refusal(units, "add_variant", us, "C")

    assert isinstance(added, VariantAdded)
    assert (added.campaign_id, added.name, added.version) == (us, "B", 2)
    assert problems == {"form": TOO_MANY_VARIANTS}
    with units.open(read_only=True):
        found = setup_queries.campaign(us)
    assert found is not None and found.version == 2
    assert [(v.name, v.status, v.step_angles) for v in found.variants] == [
        ("A", VariantStatus.ACTIVE, ()),
        ("B", VariantStatus.ACTIVE, (StepAngleNote(2, "Client-data angle"),)),
    ]
    assert event_rows(database)[-1] == (
        "campaigns", us, "v1", "v2", "operator_add_variant", None, "operator", STAMP
    )
    [record] = [r for r in caplog.records if r.getMessage() == "variant_added"]
    assert record.campaign == "law_private_ai_us"  # type: ignore[attr-defined]
    # the seed now needs B in the file, as for any stored Variant
    assert "leaves out the stored Variant B" in sync().err


def test_add_variant_checks_the_steps_and_the_floor(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    uk = ids(units)["law_private_ai_uk"]

    assert refusal(units, "add_variant", uk, "B", (StepAngleNote(5, "x"),)) == {
        "step_angles": "Step 5 is beyond the Sequence's 4 Steps"
    }
    assert refusal(units, "add_variant", uk, "a") == {
        "name": "A Variant with this name already exists"
    }
    assert "ABA Formal Opinion 512" in refusal(
        units, "add_variant", uk, "B", (StepAngleNote(2, "ABA Formal Opinion 512"),)
    )["step_angles"]


# campaign_active and the live country ------------------------------------------------------------


def test_a_market_whose_country_becomes_gb_stops_an_lia_less_campaign(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    us = ids(units)["law_private_ai_us"]
    run(units, "activate", us)
    run(units, "pause", us)
    run(units, "resume", us)
    assert active(units, us)

    run_sql(
        database,
        "UPDATE markets SET country_code = 'XX' WHERE code = 'uk'",
        "UPDATE markets SET country_code = 'GB' WHERE code = 'us'",
    )

    assert not active(units, us)
    # the triggers refuse any write to the row now, so every change waits for the LIA
    assert refusal(units, "pause", us) == {"form": LIA_FIRST}
    assert refusal(units, "add_variant", us, "B") == {"form": LIA_FIRST}
    run(units, "record_lia", us, "LIA-2026-01", "2026-11-16")
    assert active(units, us)
    run(units, "pause", us)
    assert campaign_rows(database)[1][2:5] == ("paused", "LIA-2026-01", "2026-11-16")


def test_resume_re_checks_the_lia_of_a_campaign_whose_market_became_gb(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    us = ids(units)["law_private_ai_us"]
    run(units, "activate", us)
    run(units, "pause", us)

    run_sql(
        database,
        "UPDATE markets SET country_code = 'XX' WHERE code = 'uk'",
        "UPDATE markets SET country_code = 'GB' WHERE code = 'us'",
    )

    assert refusal(units, "resume", us) == {"form": LIA_FIRST}
    assert campaign_rows(database)[1][2] == "paused"


def test_campaign_active_is_false_for_an_unknown_id(units: UnitOfWorkFactory) -> None:
    with units.open(read_only=True):
        assert setup_queries.campaign_active("01ARZ3NDEKTSV4RRFFQ69G5FAV") is False
        assert setup_queries.campaign_active(None) is False  # type: ignore[arg-type]
        assert setup_queries.campaign("01ARZ3NDEKTSV4RRFFQ69G5FAV") is None


# The backstops -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statement", "match"),
    [
        (
            "UPDATE campaigns SET status = 'active' WHERE key = 'law_private_ai_uk'",
            "a UK Campaign needs its legitimate-interests assessment before it runs",
        ),
        (
            "UPDATE campaigns SET status = 'paused' WHERE key = 'ops_automation_uk'",
            "a UK Campaign needs its legitimate-interests assessment before it runs",
        ),
        (
            "INSERT INTO campaigns SELECT '01ARZ3NDEKTSV4RRFFQ69G5FAV', 'copy', name, "
            "segment_id, (SELECT id FROM markets WHERE code = 'uk'), offer_id, sequence_id, "
            "'active', NULL, NULL, version, created_at, updated_at FROM campaigns "
            "WHERE key = 'ops_automation_us'",
            "a UK Campaign needs its legitimate-interests assessment before it runs",
        ),
        (
            "INSERT INTO variants SELECT '01ARZ3NDEKTSV4RRFFQ69G5FA' || n, campaign_id, 'V' || n, "
            "'active', '[]', created_at, updated_at FROM variants, "
            "(SELECT 'V' AS n UNION SELECT 'W') WHERE name = 'A' "
            "AND campaign_id = (SELECT id FROM campaigns WHERE key = 'law_private_ai_us')",
            "a Campaign can have at most 2 Variants",
        ),
        (
            "UPDATE campaigns SET lia_ref = 'LIA' WHERE key = 'law_private_ai_us'",
            "CHECK constraint failed: ck_campaigns_lia",
        ),
        (
            "UPDATE campaigns SET status = 'live' WHERE key = 'law_private_ai_us'",
            "CHECK constraint failed: ck_campaigns_status",
        ),
        (
            "UPDATE variants SET status = 'paused'",
            "CHECK constraint failed: ck_variants_status",
        ),
        (
            "UPDATE campaigns SET version = 0",
            "CHECK constraint failed: ck_campaigns_version",
        ),
        (
            "UPDATE campaigns SET market_id = (SELECT id FROM markets WHERE code = 'us') "
            "WHERE key = 'law_private_ai_uk'",
            "UNIQUE constraint failed: campaigns.segment_id, campaigns.market_id",
        ),
        ("UPDATE offers SET version = 0", "CHECK constraint failed: ck_offers_version"),
        ("UPDATE variants SET step_angles = NULL", "NOT NULL constraint failed"),
    ],
)
def test_the_database_refuses_a_bad_row(database: Path, statement: str, match: str) -> None:
    sync()
    before = sha256(database)

    with pytest.raises(sqlite3.IntegrityError, match=match):
        run_sql(database, statement)

    assert sha256(database) == before


def test_a_us_campaign_may_run_without_an_lia_and_a_uk_draft_needs_none(database: Path) -> None:
    sync()

    run_sql(database, "UPDATE campaigns SET status = 'active' WHERE key = 'law_private_ai_us'")
    run_sql(database, "UPDATE campaigns SET name = 'Renamed' WHERE key = 'law_private_ai_uk'")

    assert campaign_rows(database)[0][1:3] == ("Renamed", "draft")


def test_the_campaign_references_have_no_cascade(database: Path) -> None:
    found = query(
        database,
        'SELECT "table", "from", on_delete FROM pragma_foreign_key_list(\'campaigns\') '
        'ORDER BY "from"',
    )
    found += query(
        database, 'SELECT "table", "from", on_delete FROM pragma_foreign_key_list(\'variants\')'
    )

    assert sorted(found) == [
        ("campaigns", "campaign_id", "NO ACTION"),
        ("markets", "market_id", "NO ACTION"),
        ("offers", "offer_id", "NO ACTION"),
        ("segments", "segment_id", "NO ACTION"),
        ("sequences", "sequence_id", "NO ACTION"),
    ]


# Today in sender_timezone ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("timezone", "now", "accepted", "refused"),
    [
        (None, datetime(2026, 11, 16, 23, 30, tzinfo=UTC), "2026-11-16", "2026-11-17"),
        ("Asia/Tokyo", datetime(2026, 11, 16, 23, 30, tzinfo=UTC), "2026-11-17", "2026-11-18"),
        (
            "America/Los_Angeles",
            datetime(2026, 11, 17, 5, 0, tzinfo=UTC),
            "2026-11-16",
            "2026-11-17",
        ),
        # stored, but no longer readable: UTC
        ("raw:Mars/Base", datetime(2026, 11, 16, 23, 30, tzinfo=UTC), "2026-11-16", "2026-11-17"),
    ],
)
def test_today_is_the_date_in_sender_timezone(
    database: Path,
    units: UnitOfWorkFactory,
    timezone: str | None,
    now: datetime,
    accepted: str,
    refused: str,
) -> None:
    sync()
    if timezone is not None and timezone.startswith("raw:"):
        run_sql(
            database,
            "INSERT INTO settings (id, key, value, updated_at) VALUES "
            f"('01ARZ3NDEKTSV4RRFFQ69G5FAV', 'sender_timezone', '{timezone[4:]}', '{STAMP}')",
        )
        with units.open(read_only=True), pytest.raises(InvalidSettingValue):
            setup_queries.get(Key.SENDER_TIMEZONE)
    elif timezone is not None:
        set_timezone(units, timezone)
    uk = ids(units)["law_private_ai_uk"]
    clock = FrozenClock(now)

    assert refusal(units, "record_lia", uk, "LIA", refused, clock=clock) == {
        "lia_date": "can't be in the future"
    }
    assert run(units, "record_lia", uk, "LIA", accepted, clock=clock) == LiaSaved(uk, True)


# The queries -------------------------------------------------------------------------------------


def test_the_queries_read_the_offers_and_campaigns(units: UnitOfWorkFactory) -> None:
    with units.open(read_only=True):
        assert (setup_queries.offers(), setup_queries.campaigns()) == ((), ())
        assert setup_queries.offer("default") is None
    sync()

    with units.open(read_only=True):
        [offer] = setup_queries.offers()
        one = setup_queries.offer("default")
        campaigns = setup_queries.campaigns()
        markets = {seeded.id: seeded.code for seeded in setup_queries.markets()}
        sequence = setup_queries.sequence("default")

    assert one == offer and (offer.key, offer.version) == ("default", 1)
    assert [rung.price_usd_min for rung in offer.definition.ladder] == [None, 2500, 10000]
    assert [c.key for c in campaigns] == sorted(KEYS)
    first = campaigns[0]
    assert (first.status, first.lia, first.version, markets[first.market_id]) == (
        CampaignStatus.DRAFT,
        None,
        1,
        "uk",
    )
    assert sequence is not None and first.sequence_id == sequence.id
    assert first.offer_id == offer.id
    assert [(v.name, v.status) for v in first.variants] == [("A", VariantStatus.ACTIVE)]
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.status = CampaignStatus.ACTIVE  # type: ignore[misc]


def test_each_use_case_reads_the_clock_once_in_one_transaction(units: UnitOfWorkFactory) -> None:
    sync()
    uk = ids(units)["law_private_ai_uk"]
    opened: list[str] = []

    class CountingClock(FrozenClock):
        def now(self) -> Any:
            opened.append("clock")
            return super().now()

    with units.open() as uow:
        real = uow.transaction

        def counted() -> Any:
            opened.append("transaction")
            return real()

        uow.transaction = counted  # type: ignore[method-assign]
        Campaigns(CountingClock()).activate(uk, "LIA-2026-01", "2026-11-16")

    assert opened == ["clock", "transaction"]


# The logs and the command ------------------------------------------------------------------------


def test_the_sync_logs_the_offers_and_campaigns_counts(
    database: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")

    sync()
    sync()

    counts = ("created_count", "changed_count", "unchanged_count", "not_in_file_count")
    found = {
        name: [[getattr(r, c) for c in counts] for r in caplog.records if r.getMessage() == name]
        for name in ("offers_synced", "campaigns_synced")
    }
    assert found == {
        "offers_synced": [[1, 0, 0, 0], [0, 0, 1, 0]],
        "campaigns_synced": [[4, 0, 0, 0], [0, 0, 4, 0]],
    }


def test_the_operator_logs_carry_the_key_only(
    database: Path, units: UnitOfWorkFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")
    sync()
    uk = ids(units)["law_private_ai_uk"]

    run(units, "activate", uk, "LIA-SECRET-REF", "2026-11-16")
    run(units, "pause", uk)
    run(units, "resume", uk)
    run(units, "record_lia", uk, "LIA-SECRET-REF", "2026-11-16")  # unchanged: not logged

    records = [r for r in caplog.records if r.name == "nextron.app.setup.api"]
    names = [r.getMessage() for r in records if not r.getMessage().endswith("_synced")]
    assert names == [
        "campaign_lia_saved",
        "campaign_activated",
        "campaign_paused",
        "campaign_resumed",
    ]
    for record in records:
        assert "LIA-SECRET-REF" not in str(record.__dict__)


def test_the_cli_help_names_the_two_files(capsys: pytest.CaptureFixture[str]) -> None:
    from nextron import __main__ as cli

    with pytest.raises(SystemExit) as caught:
        cli.main(["seed", "sync", "--help"])

    assert caught.value.code == 0
    shown = " ".join(capsys.readouterr().out.split())
    assert "seed/offers.yaml" in shown and "seed/campaigns.yaml" in shown
    assert "Offers and Campaigns" in shown


def test_a_missing_campaigns_file_is_named(database: Path, tmp_path: Path) -> None:
    folder = seed_dir(tmp_path)
    (folder / main.CAMPAIGNS_FILE_NAME).unlink()

    result = sync(folder)

    assert (result.code, result.err) == (1, "seed/campaigns.yaml: the file doesn't exist\n")
    assert campaign_rows(database) == []


# Review pass 1: moves, notes, a stored Offer, the input checks ------------------------------------

EXTRA_OFFER = OFFERS_TEXT + (
    "  - key: extra\n    name: Extra\n    ladder:\n      - kind: reply\n        name: R\n"
)
_FIRST_SEQUENCE = SEQUENCES_TEXT[SEQUENCES_TEXT.index("  - key: default\n") :]
WITH_OTHER_SEQUENCE = SEQUENCES_TEXT + _FIRST_SEQUENCE.replace(
    "  - key: default\n", "  - key: other\n", 1
)


def test_a_campaign_moved_to_another_offer_or_sequence_updates_in_place(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    first = seed_dir(tmp_path, offers=EXTRA_OFFER, sequences=WITH_OTHER_SEQUENCE, name="first")
    assert sync(first).code == 0
    found = ids(units)
    us, uk = found["law_private_ai_us"], found["law_private_ai_uk"]
    run(units, "activate", us)
    run(units, "activate", uk, "LIA-2026-01", "2026-11-16")
    events_before = event_rows(database)
    moved = in_campaign(
        "law_private_ai_us",
        "offer: default",
        "offer: extra",
        in_campaign("law_private_ai_uk", "sequence: default", "sequence: other"),
    )

    result = sync(
        seed_dir(
            tmp_path,
            campaigns=moved,
            offers=EXTRA_OFFER,
            sequences=WITH_OTHER_SEQUENCE,
            name="moved",
        ),
        clock=FrozenClock(LATER),
    )

    assert (result.code, result.err) == (0, "")
    assert result.out.endswith(
        "campaigns: 2 changed, 2 unchanged\n"
        "  law_private_ai_us: changed (version 1 -> 2)\n"
        "  law_private_ai_uk: changed (version 1 -> 2)\n"
    )
    assert query(
        database,
        "SELECT c.key, o.key, s.key FROM campaigns c JOIN offers o ON o.id = c.offer_id "
        "JOIN sequences s ON s.id = c.sequence_id WHERE c.key LIKE 'law_private_ai_%' "
        "ORDER BY c.key",
    ) == [("law_private_ai_uk", "default", "other"), ("law_private_ai_us", "extra", "default")]
    assert campaign_rows(database)[:2] == [
        ("law_private_ai_uk", "Law-PrivateAI × UK", "active", "LIA-2026-01", "2026-11-16", 2),
        ("law_private_ai_us", "Law-PrivateAI × US", "active", None, None, 2),
    ]
    assert event_rows(database) == [
        *events_before,
        ("campaigns", us, "v1", "v2", "seed_sync", None, "seed", LATER_STAMP),
        ("campaigns", uk, "v1", "v2", "seed_sync", None, "seed", LATER_STAMP),
    ]


NOTES = (
    "      - name: A\n        status: active\n        step_angles:\n"
    "          - step: 2\n            note: Lead with client data\n"
)


def test_notes_re_sync_unchanged_and_a_note_edit_or_a_case_rename_is_a_change(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    assert sync(seed_dir(tmp_path, campaigns=with_variants(NOTES), name="notes")).code == 0
    us = ids(units)["law_private_ai_us"]
    before = sha256(database)

    again = sync(seed_dir(tmp_path, campaigns=with_variants(NOTES), name="again"))

    assert again.out.endswith(OFFERS_UNCHANGED + CAMPAIGNS_UNCHANGED)
    assert sha256(database) == before
    events_before = event_rows(database)

    note = NOTES.replace("Lead with client data", "Lead with their document workload")
    edited_note = sync(
        seed_dir(tmp_path, campaigns=with_variants(note), name="note"), clock=FrozenClock(LATER)
    )
    renamed = sync(
        seed_dir(tmp_path, campaigns=with_variants(note.replace("name: A", "name: a")), name="a"),
        clock=FrozenClock(LATER),
    )

    assert "  law_private_ai_us: changed (version 1 -> 2)\n" in edited_note.out
    assert "  law_private_ai_us: changed (version 2 -> 3)\n" in renamed.out
    assert [row for row in variant_rows(database) if row[0] == "law_private_ai_us"] == [
        (
            "law_private_ai_us",
            "a",
            "active",
            '[{"note":"Lead with their document workload","step":2}]',
        )
    ]
    assert event_rows(database) == [
        *events_before,
        ("campaigns", us, "v1", "v2", "seed_sync", None, "seed", LATER_STAMP),
        ("campaigns", us, "v2", "v3", "seed_sync", None, "seed", LATER_STAMP),
    ]


def test_a_stored_offer_that_no_longer_decodes(database: Path, units: UnitOfWorkFactory) -> None:
    sync()
    run_sql(database, "UPDATE offers SET ladder = 'x'")

    with units.open(read_only=True), pytest.raises(InvalidOffers) as caught:
        setup_queries.offers()
    result = sync()

    assert caught.value.field == "offers[default].ladder"
    assert (result.code, result.err) == (
        1,
        "seed/offers.yaml: offers[default].ladder: is not valid JSON\n",
    )


def shipped(name: str) -> object:
    return seed_yaml.parse(SHIPPED[name])


def shipped_inputs() -> dict[str, Any]:
    return {
        "segments": validate_segments(shipped(main.SEGMENTS_FILE_NAME)),
        "markets": validate_markets(shipped(main.MARKETS_FILE_NAME)),
        "sequences": validate_sequences(shipped(main.SEQUENCES_FILE_NAME)),
        "company_facts": validate_company_facts(shipped(main.COMPANY_FACTS_FILE_NAME)),
        "offers": validate_offers(shipped(main.OFFERS_FILE_NAME)),
        "campaigns": validate_campaigns(shipped(main.CAMPAIGNS_FILE_NAME)),
    }


def no_transaction() -> None:
    raise AssertionError("a transaction was opened")


def refused_before_any_transaction(
    database: Path,
    units: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
    inputs: dict[str, Any],
    error: type[Exception],
    match: str,
) -> None:
    before = sha256(database)

    with units.open() as uow:
        with pytest.raises(error, match=match):
            Seeds(FrozenClock()).sync_seed(**inputs)

    assert sha256(database) == before


@pytest.mark.parametrize(
    ("make", "error", "match"),
    [
        (lambda o: [o, offer_form(o)], TypeError, "sync_seed takes OfferDefs, not dict"),
        (lambda o: [o, o], ValueError, "sync_seed got an Offer key more than once: default"),
        (
            lambda o: [dataclasses.replace(o, ladder=o.ladder[1:])],
            InvalidOffers,
            r"offers\[default\]\.ladder\[0\]\.kind: must be reply",
        ),
    ],
    ids=["dict", "repeat", "rule"],
)
def test_sync_seed_refuses_bad_offers_before_any_transaction(
    database: Path,
    units: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
    make: Any,
    error: type[Exception],
    match: str,
) -> None:
    inputs = shipped_inputs()
    [offer] = inputs["offers"]
    assert isinstance(offer, OfferDef)
    inputs["offers"] = make(offer)

    refused_before_any_transaction(database, units, monkeypatch, inputs, error, match)


@pytest.mark.parametrize(
    ("make", "error", "match"),
    [
        (
            lambda c: [*c, campaign_form(c[0])],
            TypeError,
            "sync_seed takes CampaignDefs, not dict",
        ),
        (
            lambda c: [c[0], c[0]],
            ValueError,
            "sync_seed got a Campaign key more than once: law_private_ai_us",
        ),
        (
            lambda c: [dataclasses.replace(c[0], variants=())],
            InvalidCampaigns,
            r"campaigns\[law_private_ai_us\]\.variants: must hold at least 1 item",
        ),
        (
            lambda c: [dataclasses.replace(c[0], offer="premium")],
            InvalidCampaigns,
            r"campaigns\[law_private_ai_us\]\.offer: isn't the key of an Offer",
        ),
    ],
    ids=["dict", "repeat", "rule", "reference"],
)
def test_sync_seed_refuses_bad_campaigns_before_any_transaction(
    database: Path,
    units: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
    make: Any,
    error: type[Exception],
    match: str,
) -> None:
    inputs = shipped_inputs()
    campaigns = inputs["campaigns"]
    assert all(isinstance(c, CampaignDef) for c in campaigns)
    inputs["campaigns"] = make(campaigns)

    refused_before_any_transaction(database, units, monkeypatch, inputs, error, match)
