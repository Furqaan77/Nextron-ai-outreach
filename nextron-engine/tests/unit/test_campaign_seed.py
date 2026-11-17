"""The shipped seed files ``seed/offers.yaml`` and ``seed/campaigns.yaml`` (FR-3, Story 4.2).

They validate into exactly the spec's Design Notes content: one Offer, ``default``, whose ladder
is a reply, then the audit at the Glossary's $2,500 to $3,500, then a build at $10,000 to
$30,000; and the four Campaigns of the AC in its order, each on Offer ``default``, Sequence
``default`` and one Variant ``A``, active, with no angle notes. Their references pass against
the shipped Segments, Markets, Offers and Sequences.
"""

from nextron.domain import seed_yaml
from nextron.domain.campaign import (
    CampaignDef,
    StepAngleNote,
    VariantDef,
    VariantStatus,
    campaign_form,
    campaign_from_form,
    check_campaign_references,
    validate_campaigns,
)
from nextron.domain.markets import validate_markets
from nextron.domain.offers import (
    OfferDef,
    OfferRung,
    OfferRungKind,
    offer_form,
    offer_from_form,
    validate_offers,
)
from nextron.domain.segments import validate_segments
from nextron.domain.sequences import validate_sequences
from nextron.main import (
    CAMPAIGNS_FILE_LABEL,
    CAMPAIGNS_FILE_NAME,
    MARKETS_FILE_NAME,
    OFFERS_FILE_LABEL,
    OFFERS_FILE_NAME,
    SEED_DIR,
    SEGMENTS_FILE_NAME,
    SEQUENCES_FILE_NAME,
)
from tests.contracts.support import ENGINE_ROOT

OFFERS_TEXT = (ENGINE_ROOT / "seed" / "offers.yaml").read_text(encoding="utf-8")
CAMPAIGNS_TEXT = (ENGINE_ROOT / "seed" / "campaigns.yaml").read_text(encoding="utf-8")
ONE_ACTIVE_A = (VariantDef("A", VariantStatus.ACTIVE, ()),)


def read(name: str) -> object:
    return seed_yaml.parse((SEED_DIR / name).read_text(encoding="utf-8"))


def test_the_files_are_where_the_command_reads_them() -> None:
    assert (OFFERS_FILE_NAME, OFFERS_FILE_LABEL) == ("offers.yaml", "seed/offers.yaml")
    assert (CAMPAIGNS_FILE_NAME, CAMPAIGNS_FILE_LABEL) == ("campaigns.yaml", "seed/campaigns.yaml")
    assert SEED_DIR == ENGINE_ROOT / "seed"


def test_the_offer_is_the_glossarys_ladder() -> None:
    assert validate_offers(seed_yaml.parse(OFFERS_TEXT)) == (
        OfferDef(
            key="default",
            name="Reply, then the audit, then a build",
            ladder=(
                OfferRung(
                    OfferRungKind.REPLY,
                    "A reply",
                    None,
                    None,
                    "The first ask is only a reply; Steps never name a price or propose the "
                    "audit.",
                ),
                OfferRung(
                    OfferRungKind.AUDIT,
                    "Private AI & Workflow Audit",
                    2500,
                    3500,
                    "Fixed fee, credited in full toward a build.",
                ),
                OfferRung(OfferRungKind.BUILD, "Build", 10000, 30000, None),
            ),
        ),
    )


def test_the_campaigns_are_the_acs_four_in_order() -> None:
    assert validate_campaigns(seed_yaml.parse(CAMPAIGNS_TEXT)) == (
        CampaignDef(
            "law_private_ai_us",
            "Law-PrivateAI × US",
            "law_private_ai",
            "us",
            "default",
            "default",
            ONE_ACTIVE_A,
        ),
        CampaignDef(
            "law_private_ai_uk",
            "Law-PrivateAI × UK",
            "law_private_ai",
            "uk",
            "default",
            "default",
            ONE_ACTIVE_A,
        ),
        CampaignDef(
            "ops_automation_us",
            "Ops-Automation × US",
            "ops_automation",
            "us",
            "default",
            "default",
            ONE_ACTIVE_A,
        ),
        CampaignDef(
            "ops_automation_uk",
            "Ops-Automation × UK",
            "ops_automation",
            "uk",
            "default",
            "default",
            ONE_ACTIVE_A,
        ),
    )


def test_the_campaigns_file_never_sets_a_status_or_an_lia() -> None:
    for item in seed_yaml.parse(CAMPAIGNS_TEXT)["campaigns"]:  # type: ignore[index]
        assert "status" not in item and not any(name.startswith("lia") for name in item)


def test_the_references_pass_against_the_shipped_files() -> None:
    segments = validate_segments(read(SEGMENTS_FILE_NAME))
    markets = validate_markets(read(MARKETS_FILE_NAME))
    sequences = validate_sequences(read(SEQUENCES_FILE_NAME))
    offers = validate_offers(read(OFFERS_FILE_NAME))
    campaigns = validate_campaigns(read(CAMPAIGNS_FILE_NAME))

    check_campaign_references(
        campaigns,
        {segment.key for segment in segments},
        {market.code: market.country_code for market in markets},
        {offer.key for offer in offers},
        {sequence.key: len(sequence.steps) for sequence in sequences},
    )
    assert {campaign.market for campaign in campaigns} == {"us", "uk"}
    assert all(len(sequence.steps) == 4 for sequence in sequences)


def test_the_shipped_definitions_round_trip_through_their_forms() -> None:
    for offer in validate_offers(seed_yaml.parse(OFFERS_TEXT)):
        assert offer_from_form(offer_form(offer)) == offer
    for campaign in validate_campaigns(seed_yaml.parse(CAMPAIGNS_TEXT)):
        assert campaign_from_form(campaign_form(campaign)) == campaign
        assert all(variant.step_angles == () for variant in campaign.variants)
    assert StepAngleNote(1, "x") != StepAngleNote(2, "x")  # notes key on their Step
