"""The shipped seed file ``seed/company_facts.yaml`` (FR-72, Sales Playbook section 6, Story 4.3).

It validates into exactly the spec's Design Notes content, fact for fact. The Legal AI wording
never says "for a law firm" and the Wish Wheels wording never says "distribut"; every fact has a
source URL and no metric; ABA Formal Opinion 512 is allowed only in ``us`` and the SRA notice
only in ``uk``; the Offer names no price; and every reference passes against the shipped
Segments and Markets.
"""

import re
from datetime import date

from nextron.domain import seed_yaml
from nextron.domain.company_facts import (
    FactContent,
    FactType,
    SeedFact,
    check_seed_facts,
    fact_form,
    fact_from_form,
    is_link,
    validate_company_facts,
)
from nextron.domain.markets import validate_markets
from nextron.domain.segments import mentions_aba_512, mentions_sra_notice, validate_segments
from nextron.main import (
    COMPANY_FACTS_FILE_LABEL,
    COMPANY_FACTS_FILE_NAME,
    MARKETS_FILE_NAME,
    SEED_DIR,
    SEGMENTS_FILE_NAME,
)
from tests.contracts.support import ENGINE_ROOT

SEED_FILE = ENGINE_ROOT / "seed" / "company_facts.yaml"
SEED_TEXT = SEED_FILE.read_text(encoding="utf-8")
NEXTRON = "https://nextronai.com/"
CHECKED = date(2026, 11, 14)
BOTH_SEGMENTS = ("law_private_ai", "ops_automation")
BOTH_MARKETS = ("uk", "us")  # sorted


def case(
    fact_id: str,
    wording: str,
    *,
    fact_type: FactType = FactType.CASE_STUDY,
    scope_note: str | None = None,
) -> SeedFact:
    return SeedFact(
        fact_id,
        FactContent(
            type=fact_type,
            wording=wording,
            source_url=NEXTRON,
            segments=BOTH_SEGMENTS,
            markets=BOTH_MARKETS,
            metric=None,
            metric_confirmed_at=None,
            scope_note=scope_note,
            last_checked_on=CHECKED,
        ),
    )


def regulation(
    fact_id: str, wording: str, source_url: str, market: str, scope_note: str
) -> SeedFact:
    return SeedFact(
        fact_id,
        FactContent(
            type=FactType.REGULATION,
            wording=wording,
            source_url=source_url,
            segments=("law_private_ai",),
            markets=(market,),
            metric=None,
            metric_confirmed_at=None,
            scope_note=scope_note,
            last_checked_on=CHECKED,
        ),
    )


DESIGN_NOTES = (
    case(
        "nextron_team",
        "A 17+ person team of engineers, AI specialists and analysts, based in San Jose.",
        fact_type=FactType.COMPANY,
    ),
    case(
        "legal_ai",
        "Built an AI legal assistant platform for research and document help for a legal-tech "
        "venture (Legal AI).",
        scope_note="The client was a legal-tech venture, not a law firm.",
    ),
    case(
        "wish_wheels",
        "Built a single operations platform covering CRM, orders, inventory, vendors and "
        "analytics for an automotive retail and services business (Wish Wheels).",
        scope_note="An automotive retail and services business, not a distributor; don't "
        "present it as distribution experience.",
    ),
    case(
        "mp_online_digital",
        "Built a citizen-services digital transformation platform with workflow automation and "
        "dashboards (MP Online Digital).",
    ),
    case(
        "property_3d_sites",
        "Built immersive 3D property sites with CRM-connected lead capture (Sapphire Farms, "
        "Woodland 157).",
    ),
    case(
        "audit_offer",
        "A fixed-fee Private AI & Workflow Audit, credited in full toward a build.",
        fact_type=FactType.OFFER,
        scope_note="Name the audit only in Reply Drafts, never in a Step; no price until the "
        "Offer is validated.",
    ),
    regulation(
        "aba_formal_opinion_512",
        "ABA Formal Opinion 512 says a lawyer needs the client's informed consent before "
        "putting client information into a self-learning generative AI tool.",
        "https://www.americanbar.org/content/dam/aba/administrative/professional_responsibility/"
        "ethics-opinions/aba-formal-opinion-512.pdf",
        "us",
        "Its informed-consent point concerns self-learning tools only; it is ethics guidance "
        "for US lawyers.",
    ),
    regulation(
        "sra_ai_notice",
        "The Solicitors Regulation Authority (SRA) warning notice of 17 August 2026 allows "
        "client data into AI tools only with contractual, technical and organisational "
        "safeguards.",
        "https://www.sra.org.uk/solicitors/guidance/misuse-ai/",
        "uk",
        "It also acknowledges that firms are building their own AI systems; cite it only for "
        "what it says about client data.",
    ),
)


def shipped() -> tuple[SeedFact, ...]:
    return validate_company_facts(seed_yaml.parse(SEED_TEXT))


def by_id(fact_id: str) -> SeedFact:
    [found] = [fact for fact in shipped() if fact.fact_id == fact_id]
    return found


def test_the_file_is_where_seed_sync_reads_it() -> None:
    assert SEED_DIR / COMPANY_FACTS_FILE_NAME == SEED_FILE
    assert COMPANY_FACTS_FILE_LABEL == "seed/company_facts.yaml"


def test_the_shipped_file_validates_into_exactly_the_design_notes() -> None:
    assert shipped() == DESIGN_NOTES


def test_the_header_comment_says_what_the_file_is_and_how_it_loads() -> None:
    header = SEED_TEXT.split("company_facts:\n", 1)[0]

    assert header.startswith("# The Approved Company Facts")
    assert "nextron seed sync" in header
    assert "strict subset of YAML" in header
    assert "Operator's version wins" in header
    assert all(line.startswith("#") or not line for line in header.splitlines())


def test_the_legal_ai_work_is_never_for_a_law_firm() -> None:
    assert "for a law firm" not in by_id("legal_ai").content.wording.casefold()


def test_wish_wheels_is_never_a_distributor() -> None:
    assert "distribut" not in by_id("wish_wheels").content.wording.casefold()


def test_every_fact_has_a_source_link_and_no_metric() -> None:
    for fact in shipped():
        assert is_link(fact.content.source_url), fact.fact_id
        assert fact.content.metric is None and fact.content.metric_confirmed_at is None


def test_the_regulations_are_bound_to_their_market() -> None:
    aba, sra = by_id("aba_formal_opinion_512"), by_id("sra_ai_notice")

    assert (aba.content.type, aba.content.markets) == (FactType.REGULATION, ("us",))
    assert (sra.content.type, sra.content.markets) == (FactType.REGULATION, ("uk",))
    assert mentions_aba_512(aba.content.wording) and not mentions_sra_notice(aba.content.wording)
    assert mentions_sra_notice(sra.content.wording) and not mentions_aba_512(sra.content.wording)
    others = [fact for fact in shipped() if fact.content.type is not FactType.REGULATION]
    for fact in others:
        texts = (fact.content.wording, fact.content.scope_note or "")
        assert not any(mentions_aba_512(t) or mentions_sra_notice(t) for t in texts), fact.fact_id
        assert fact.content.markets == BOTH_MARKETS
        assert fact.content.segments == BOTH_SEGMENTS


def test_the_offer_names_no_price() -> None:
    offer = by_id("audit_offer").content

    assert offer.type is FactType.OFFER
    assert not re.search(r"[$£€]|\d", offer.wording)
    assert "price" not in offer.wording.casefold()


def test_the_references_pass_against_the_shipped_segments_and_markets() -> None:
    segments = validate_segments(
        seed_yaml.parse((SEED_DIR / SEGMENTS_FILE_NAME).read_text(encoding="utf-8"))
    )
    markets = validate_markets(
        seed_yaml.parse((SEED_DIR / MARKETS_FILE_NAME).read_text(encoding="utf-8"))
    )

    check_seed_facts(
        shipped(),
        frozenset(segment.key for segment in segments),
        {market.code: market.country_code for market in markets},
        date(2026, 11, 15),  # the day after the Playbook and the research, as the sync allows
    )


def test_the_file_form_round_trips() -> None:
    for fact in shipped():
        assert fact_from_form(fact_form(fact.fact_id, fact.content)) == fact
