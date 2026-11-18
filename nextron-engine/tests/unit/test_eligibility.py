"""``nextron.domain.eligibility`` (FR-18, FR-2, FR-63, NFR-5; Story 3.6), table-driven with a
fixed ``now`` and the shipped US and UK Market definitions: one case per ``ReasonKind``, one
passing US case, one passing UK case and several reasons together; every ``no_enabled_market``
cause and its text; the Market rules (cold email, legal forms, free webmail, the GB defence in
depth); the address; every ``BlockReason`` mapped; the FR-63 predicate (the four AC cases and
the 90-day boundary); the Not Now date boundary; ``Reason``, ``Decision`` and fact validation;
no text holding the address; and every GB corporate form labelled. Story 3.7: the
``provenance_missing`` case, the required ``provenance_recorded`` fact, the 128-character
``basis_rule`` boundary, ``basis_country`` and every label's basis within it."""

import dataclasses
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest

from nextron import main
from nextron.domain import markets as markets_module
from nextron.domain import seed_yaml
from nextron.domain.contact_market import MarketBasis
from nextron.domain.contactability import Allowed, Blocked, BlockReason
from nextron.domain.eligibility import (
    ASKED_TEXT,
    CONTACT_ERASED_TEXT,
    BLOCK_REASON_KINDS,
    CLOSED_STAGE_LABELS,
    LEGAL_FORM_LABELS,
    LEGAL_FORM_UNCONFIRMED_TEXT,
    MAX_BASIS_RULE_LENGTH,
    NO_ADDRESS_CLASS_TEXT,
    NO_EMAIL_TEXT,
    NO_MARKET_BASIS_TEXTS,
    NO_MARKET_GONE_TEXT,
    NO_MARKET_OFFICE_TEXT,
    NO_MARKET_OTHER_TEXT,
    NO_MARKET_RULES_TEXT,
    NO_MARKET_STALE_TEXT,
    NO_MARKET_UNRESOLVED_TEXT,
    NOT_NOW_UNDATED_TEXT,
    PROVENANCE_MISSING_TEXT,
    RECONTACT_TEXT,
    RECONTACT_WAIT,
    SUPPRESSED_TEXT,
    UK_BASIS,
    US_BASIS,
    Decision,
    EligibilityContact,
    EligibilityLead,
    EndedSequence,
    Reason,
    ReasonKind,
    basis_country,
    evaluate,
    recontact_allowed,
)
from nextron.domain.eligibility import _BLOCK_STAGES as BLOCK_STAGES
from nextron.domain.markets import MarketDef, validate_markets
from nextron.domain.pipeline_stage import CLOSED_STAGES, PipelineStage

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
SHIPPED_MARKETS = main.SEED_DIR / main.MARKETS_FILE_NAME
US_ID, UK_ID, CA_ID, IE_ID = "m_us", "m_uk", "m_ca", "m_ie"
US_EMAIL = "zelda.quill@acmelaw.com"
UK_EMAIL = "zelda.quill@acmelaw.co.uk"
# Text that must never reach a reason or a basis: the address, its parts, a person's name.
PLAIN = ("zelda", "quill", "acmelaw", "@", "gmail")


def shipped() -> tuple[MarketDef, MarketDef]:
    found = validate_markets(seed_yaml.parse(SHIPPED_MARKETS.read_text(encoding="utf-8")))
    [us] = [market for market in found if market.country_code == "US"]
    [uk] = [market for market in found if market.country_code == "GB"]
    return us, uk


US, UK = shipped()
CANADA = dataclasses.replace(
    US, code="ca", name="Canada", country_code="CA", cctlds=("ca",), cold_email_allowed=False
)
IRELAND = dataclasses.replace(US, code="ie", name="Ireland", country_code="IE", cctlds=("ie",))
RULES: dict[str, MarketDef] = {US_ID: US, UK_ID: UK}


def us_contact(**changes: Any) -> EligibilityContact:
    fields: dict[str, Any] = {
        "email": US_EMAIL,
        "address_class": "published",
        "market_id": US_ID,
        "market_basis": "lead_country",
        "office_country": None,
        "office_region": None,
        "contactable": Allowed(),
        "provenance_recorded": True,
    }
    fields.update(changes)
    return EligibilityContact(**fields)


def us_lead(**changes: Any) -> EligibilityLead:
    fields: dict[str, Any] = {"country_code": "US", "stage": PipelineStage.NEW}
    fields.update(changes)
    return EligibilityLead(**fields)


def uk_contact(**changes: Any) -> EligibilityContact:
    fields: dict[str, Any] = {
        "email": UK_EMAIL,
        "address_class": "verified",
        "market_id": UK_ID,
        "market_basis": "lead_country",
    }
    fields.update(changes)
    return us_contact(**fields)


def uk_lead(**changes: Any) -> EligibilityLead:
    fields: dict[str, Any] = {
        "country_code": "GB",
        "legal_form": "ltd",
        "legal_form_confirmed": True,
    }
    fields.update(changes)
    return us_lead(**fields)


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def ended(last_step_days: float, ended_days: float, replied: bool = False) -> EndedSequence:
    return EndedSequence(ago(last_step_days), ago(ended_days), replied)


def decide(
    contact: EligibilityContact,
    lead: EligibilityLead,
    rules: dict[str, MarketDef] | None = None,
    now: datetime = NOW,
) -> Decision:
    return evaluate(contact, lead, RULES if rules is None else rules, now)


def texts(decision: Decision) -> list[str]:
    return [reason.text for reason in decision.reasons]


def assert_no_personal_data(decision: Decision) -> None:
    every = " ".join([*texts(decision), decision.basis_rule or ""]).lower()
    for plain in PLAIN:
        assert plain not in every, plain


# One case per reason ------------------------------------------------------------------------------

ONE_REASON: list[tuple[str, Callable[[], Decision], ReasonKind, str]] = [
    (
        "no_enabled_market",
        lambda: decide(
            us_contact(market_id=None, market_basis="location_outside_markets"),
            us_lead(country_code="CA"),
        ),
        ReasonKind.NO_ENABLED_MARKET,
        "No enabled Market: the office location or the Lead's country isn't in an enabled Market",
    ),
    (
        "cold_email_not_allowed",
        lambda: decide(
            us_contact(email="zelda.quill@acmelaw.com", market_id=CA_ID),
            us_lead(country_code="CA"),
            {**RULES, CA_ID: CANADA},
        ),
        ReasonKind.COLD_EMAIL_NOT_ALLOWED,
        "The Canada Market doesn't allow cold email",
    ),
    (
        "uk_legal_form_ineligible",
        lambda: decide(uk_contact(), uk_lead(legal_form="old-public-company")),
        ReasonKind.UK_LEGAL_FORM_INELIGIBLE,
        "The Lead's legal form (old-public-company) isn't a corporate subscriber the United "
        "Kingdom Market allows",
    ),
    (
        "uk_legal_form_unconfirmed",
        lambda: decide(uk_contact(), uk_lead(legal_form_confirmed=False)),
        ReasonKind.UK_LEGAL_FORM_UNCONFIRMED,
        LEGAL_FORM_UNCONFIRMED_TEXT,
    ),
    (
        "uk_free_webmail",
        lambda: decide(uk_contact(email="zelda.quill@gmail.com"), uk_lead()),
        ReasonKind.UK_FREE_WEBMAIL,
        "The address is on a free-webmail domain; the United Kingdom Market allows corporate "
        "addresses only",
    ),
    (
        "address_not_sendable",
        lambda: decide(us_contact(address_class="guessed"), us_lead()),
        ReasonKind.ADDRESS_NOT_SENDABLE,
        "The address is Guessed; only Published or Verified addresses can be emailed",
    ),
    (
        "provenance_missing",
        lambda: decide(us_contact(provenance_recorded=False), us_lead()),
        ReasonKind.PROVENANCE_MISSING,
        "Provenance missing: no Provenance Record shows where this Contact or its email address "
        "came from",
    ),
    (
        "address_or_domain_suppressed",
        lambda: decide(
            us_contact(contactable=Blocked((BlockReason.ADDRESS_SUPPRESSED,))), us_lead()
        ),
        ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,
        SUPPRESSED_TEXT,
    ),
    (
        "asked_not_to_be_contacted",
        lambda: decide(
            us_contact(contactable=Blocked((BlockReason.PERSON_SUPPRESSED,))), us_lead()
        ),
        ReasonKind.ASKED_NOT_TO_BE_CONTACTED,
        ASKED_TEXT,
    ),
    (
        "recontact_not_allowed",
        lambda: decide(us_contact(history=(ended(60, 30),)), us_lead(events_first_seen=(ago(1),))),
        ReasonKind.RECONTACT_NOT_ALLOWED,
        RECONTACT_TEXT,
    ),
    (
        "not_now_until",
        lambda: decide(
            us_contact(), us_lead(stage=PipelineStage.NOT_NOW, recontact_on=date(2027, 1, 15))
        ),
        ReasonKind.NOT_NOW_UNTIL,
        "Parked Not Now until 2027-01-15",
    ),
    (
        "not_now_undated",
        lambda: decide(us_contact(), us_lead(stage=PipelineStage.NOT_NOW)),
        ReasonKind.NOT_NOW_UNDATED,
        NOT_NOW_UNDATED_TEXT,
    ),
    (
        "lead_closed",
        lambda: decide(us_contact(), us_lead(stage=PipelineStage.LOST)),
        ReasonKind.LEAD_CLOSED,
        "The Lead is closed (Lost)",
    ),
    (  # Story 3.13 met Story 3.6 at the merge: the gate blocks an erased Contact
        "contact_erased",
        lambda: decide(
            us_contact(contactable=Blocked((BlockReason.CONTACT_ERASED,))), us_lead()
        ),
        ReasonKind.CONTACT_ERASED,
        CONTACT_ERASED_TEXT,
    ),
]


@pytest.mark.parametrize(
    ("run", "kind", "text"),
    [case[1:] for case in ONE_REASON],
    ids=[case[0] for case in ONE_REASON],
)
def test_each_reason_alone(run: Callable[[], Decision], kind: ReasonKind, text: str) -> None:
    decision = run()

    assert decision.eligible is False
    assert decision.basis_rule is None
    assert decision.kinds == (kind,)
    assert texts(decision) == [text]
    assert_no_personal_data(decision)


def test_the_table_has_one_case_per_reason() -> None:
    assert [case[2] for case in ONE_REASON] == list(ReasonKind)
    assert [case[0] for case in ONE_REASON] == [kind.value for kind in ReasonKind]


def test_a_us_contact_passes_with_the_can_spam_basis() -> None:
    decision = decide(us_contact(), us_lead())

    assert decision == Decision(True, US_BASIS, ())
    assert decision.basis_rule == "US: cold email allowed (CAN-SPAM)"
    assert decision.kinds == ()


def test_a_uk_contact_passes_with_the_legitimate_interests_basis() -> None:
    decision = decide(uk_contact(), uk_lead())

    assert decision.eligible is True
    assert decision.reasons == ()
    assert decision.basis_rule == (
        "UK: corporate subscriber (private limited company), legitimate interests"
    )


def test_several_reasons_are_all_listed_in_enum_order() -> None:
    blocked = Blocked(
        (BlockReason.ADDRESS_SUPPRESSED, BlockReason.PERSON_SUPPRESSED, BlockReason.LEAD_LOST)
    )
    decision = decide(
        uk_contact(
            address_class="guessed", contactable=blocked, history=(ended(10, 5),)
        ),
        uk_lead(stage=PipelineStage.LOST, legal_form=None, legal_form_confirmed=False),
    )

    assert decision.kinds == (
        ReasonKind.UK_LEGAL_FORM_UNCONFIRMED,
        ReasonKind.ADDRESS_NOT_SENDABLE,
        ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,
        ReasonKind.ASKED_NOT_TO_BE_CONTACTED,
        ReasonKind.RECONTACT_NOT_ALLOWED,
        ReasonKind.LEAD_CLOSED,
    )
    assert decision.basis_rule is None
    assert_no_personal_data(decision)


def test_every_failing_rule_of_a_market_is_listed() -> None:
    decision = decide(
        uk_contact(email="zelda.quill@gmail.com"),
        uk_lead(legal_form="old-public-company", legal_form_confirmed=False),
    )

    assert decision.kinds == (
        ReasonKind.UK_LEGAL_FORM_INELIGIBLE,
        ReasonKind.UK_LEGAL_FORM_UNCONFIRMED,
        ReasonKind.UK_FREE_WEBMAIL,
    )


# The Market ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("basis", "text"),
    [
        (None, NO_MARKET_UNRESOLVED_TEXT),
        (
            "cctld_outside_markets",
            "No enabled Market: the email address is on a country-code domain outside the "
            "enabled Markets",
        ),
        (
            "location_outside_markets",
            "No enabled Market: the office location or the Lead's country isn't in an enabled "
            "Market",
        ),
        (
            "location_unknown",
            "No enabled Market: neither the office location nor the Lead's country is known",
        ),
        (
            "conflict_stricter",
            "No enabled Market: the location and the email's country-code domain point to "
            "equally strict Markets",
        ),
        ("office_location", NO_MARKET_OTHER_TEXT),  # a basis that names a Market, with none
        ("retired_basis", NO_MARKET_OTHER_TEXT),
    ],
)
def test_a_contact_without_a_market_says_why(basis: str | None, text: str) -> None:
    decision = decide(us_contact(market_id=None, market_basis=basis), us_lead())

    assert decision.kinds == (ReasonKind.NO_ENABLED_MARKET,)
    assert texts(decision) == [text]


def test_every_blocking_market_basis_has_its_own_text() -> None:
    blocking = {
        MarketBasis.CCTLD_OUTSIDE_MARKETS,
        MarketBasis.LOCATION_OUTSIDE_MARKETS,
        MarketBasis.LOCATION_UNKNOWN,
        MarketBasis.CONFLICT_STRICTER,
    }

    assert set(NO_MARKET_BASIS_TEXTS) == blocking
    assert len(set(NO_MARKET_BASIS_TEXTS.values())) == len(blocking)


@pytest.mark.parametrize(
    ("contact", "lead", "rules", "text"),
    [
        (us_contact(), us_lead(), None, NO_MARKET_RULES_TEXT),
        (us_contact(market_id="m_gone"), us_lead(), RULES, NO_MARKET_GONE_TEXT),
        (
            us_contact(),
            us_lead(),
            {US_ID: dataclasses.replace(US, enabled=False), UK_ID: UK},
            "No enabled Market: the United States Market is disabled",
        ),
        (us_contact(office_country="US", office_region="ZZ"), us_lead(), RULES, None),
        (us_contact(office_country="us"), us_lead(), RULES, None),
        (us_contact(office_region="NY"), us_lead(), RULES, None),  # a region with no country
        # stale: the Lead's country moved to the UK, so a fresh resolution gives the UK
        (us_contact(), us_lead(country_code="GB"), RULES, NO_MARKET_STALE_TEXT),
        # stale: a fresh resolution gives no Market (.de is outside the enabled Markets)
        (us_contact(email="zelda.quill@acmelaw.de"), us_lead(), RULES, NO_MARKET_STALE_TEXT),
    ],
    ids=[
        "unreadable",
        "gone",
        "disabled",
        "bad-region",
        "bad-country",
        "region-only",
        "moved",
        "de",
    ],
)
def test_a_stored_market_counts_only_when_it_is_confirmed_live(
    contact: EligibilityContact,
    lead: EligibilityLead,
    rules: dict[str, MarketDef] | None,
    text: str | None,
) -> None:
    decision = evaluate(contact, lead, rules, NOW)

    assert decision.kinds == (ReasonKind.NO_ENABLED_MARKET,)
    assert texts(decision) == [text or NO_MARKET_OFFICE_TEXT]


def test_a_blocked_contact_stays_blocked_when_its_country_is_enabled_later() -> None:
    contact = us_contact(market_id=None, market_basis="location_outside_markets")

    decision = decide(contact, us_lead(country_code="CA"), {**RULES, CA_ID: CANADA})

    assert decision.kinds == (ReasonKind.NO_ENABLED_MARKET,)


def test_a_valid_office_and_a_conflict_resolved_to_the_uk_pass() -> None:
    office = us_contact(office_country="US", office_region="NY")
    conflict = uk_contact(market_basis="conflict_stricter", office_country="US")

    assert decide(office, us_lead()).eligible is True
    # a US office with a .co.uk address is UK (stricter), and stays UK when resolved afresh
    assert decide(conflict, uk_lead(country_code="US")).basis_rule == (
        "UK: corporate subscriber (private limited company), legitimate interests"
    )


def test_the_current_definition_of_the_stored_market_applies() -> None:
    edited = {US_ID: US, UK_ID: dataclasses.replace(UK, name="Britain")}

    decision = decide(uk_contact(), uk_lead(legal_form_confirmed=False), edited)

    assert texts(decision) == [LEGAL_FORM_UNCONFIRMED_TEXT]
    assert decide(uk_contact(email="zelda.quill@gmail.com"), uk_lead(), edited).reasons[0].text == (
        "The address is on a free-webmail domain; the Britain Market allows corporate "
        "addresses only"
    )


# The Market's rules -------------------------------------------------------------------------------


def test_cold_email_needs_a_us_or_gb_country_as_well_as_the_flag() -> None:
    contact = us_contact(email="zelda.quill@acmelaw.com", market_id=IE_ID)

    decision = decide(contact, us_lead(country_code="IE"), {**RULES, IE_ID: IRELAND})

    assert decision.kinds == (ReasonKind.COLD_EMAIL_NOT_ALLOWED,)
    assert texts(decision) == ["The Ireland Market doesn't allow cold email"]


def test_an_unlisted_gb_form_names_its_label() -> None:
    narrow = {US_ID: US, UK_ID: dataclasses.replace(UK, eligible_legal_forms=("ltd", "plc"))}

    decision = decide(uk_contact(), uk_lead(legal_form="llp"), narrow)

    assert decision.kinds == (ReasonKind.UK_LEGAL_FORM_INELIGIBLE,)
    assert texts(decision) == [
        "The Lead's legal form (limited liability partnership) isn't a corporate subscriber "
        "the United Kingdom Market allows"
    ]


def test_no_legal_form_is_unconfirmed() -> None:
    decision = decide(uk_contact(), uk_lead(legal_form=None, legal_form_confirmed=False))

    assert decision.kinds == (ReasonKind.UK_LEGAL_FORM_UNCONFIRMED,)


def test_a_gb_market_always_checks_legal_forms_and_free_webmail() -> None:
    loose = dataclasses.replace(UK, eligible_legal_forms=None, free_webmail_blocked=False)
    rules = {US_ID: US, UK_ID: loose}

    unlisted = decide(uk_contact(), uk_lead(), rules)
    webmail = decide(uk_contact(email="zelda.quill@gmail.com"), uk_lead(), rules)

    assert unlisted.kinds == (ReasonKind.UK_LEGAL_FORM_INELIGIBLE,)  # nothing is listed
    assert ReasonKind.UK_FREE_WEBMAIL in webmail.kinds
    assert decide(uk_contact(), uk_lead(legal_form=None), rules).kinds == (
        ReasonKind.UK_LEGAL_FORM_UNCONFIRMED,
    )


def test_a_market_that_lists_legal_forms_checks_them_outside_the_uk() -> None:
    listing = {US_ID: dataclasses.replace(US, eligible_legal_forms=("llc",)), UK_ID: UK}

    assert decide(us_contact(), us_lead(), listing).kinds == (
        ReasonKind.UK_LEGAL_FORM_UNCONFIRMED,
    )
    passing = decide(us_contact(), us_lead(legal_form="llc", legal_form_confirmed=True), listing)
    assert passing.basis_rule == US_BASIS


def test_free_webmail_is_allowed_in_the_us_and_blocked_where_the_market_says() -> None:
    blocking = {US_ID: dataclasses.replace(US, free_webmail_blocked=True), UK_ID: UK}

    assert decide(us_contact(email="zelda.quill@gmail.com"), us_lead()).eligible is True
    assert decide(us_contact(email="zelda.quill@gmail.com"), us_lead(), blocking).kinds == (
        ReasonKind.UK_FREE_WEBMAIL,
    )
    assert decide(uk_contact(email="zelda.quill@mail.btinternet.com"), uk_lead()).kinds == (
        ReasonKind.UK_FREE_WEBMAIL,
    )


def test_an_unlabelled_listed_form_uses_its_code_in_the_basis() -> None:
    listing = {US_ID: US, UK_ID: dataclasses.replace(UK, eligible_legal_forms=("widget-co",))}

    decision = decide(uk_contact(), uk_lead(legal_form="widget-co"), listing)

    assert decision.basis_rule == UK_BASIS.format(label="widget-co")


# The address --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "text"),
    [
        (
            {"address_class": "guessed"},
            "The address is Guessed; only Published or Verified addresses can be emailed",
        ),
        (
            {"address_class": "invalid"},
            "The address is Invalid; only Published or Verified addresses can be emailed",
        ),
        ({"email": None, "address_class": None}, NO_EMAIL_TEXT),
        ({"email": None, "address_class": "published"}, NO_EMAIL_TEXT),
        ({"address_class": None}, NO_ADDRESS_CLASS_TEXT),
        ({"address_class": "Published"}, NO_ADDRESS_CLASS_TEXT),  # not a stored class
    ],
)
def test_an_address_that_cant_be_emailed_says_why(changes: dict[str, Any], text: str) -> None:
    decision = decide(us_contact(**changes), us_lead())

    assert decision.kinds == (ReasonKind.ADDRESS_NOT_SENDABLE,)
    assert texts(decision) == [text]


@pytest.mark.parametrize("address_class", ["published", "verified"])
def test_a_published_or_verified_address_can_be_emailed(address_class: str) -> None:
    assert decide(us_contact(address_class=address_class), us_lead()).eligible is True


# The Suppression List and the closed Lead ---------------------------------------------------------


def test_every_block_reason_is_mapped() -> None:
    assert list(BLOCK_REASON_KINDS) == list(BlockReason)


def test_every_closed_stage_has_a_label() -> None:
    assert set(CLOSED_STAGE_LABELS) == set(CLOSED_STAGES)
    assert set(BLOCK_STAGES.values()) <= set(CLOSED_STAGES)


@pytest.mark.parametrize(
    ("reason", "kind", "text"),
    [
        (BlockReason.ADDRESS_SUPPRESSED, ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED, SUPPRESSED_TEXT),
        (
            BlockReason.ADDRESS_DOMAIN_SUPPRESSED,
            ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,
            SUPPRESSED_TEXT,
        ),
        (
            BlockReason.LEAD_DOMAIN_SUPPRESSED,
            ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,
            SUPPRESSED_TEXT,
        ),
        (BlockReason.PERSON_SUPPRESSED, ReasonKind.ASKED_NOT_TO_BE_CONTACTED, ASKED_TEXT),
        (BlockReason.PROFILE_SUPPRESSED, ReasonKind.ASKED_NOT_TO_BE_CONTACTED, ASKED_TEXT),
        (BlockReason.LEAD_LOST, ReasonKind.LEAD_CLOSED, "The Lead is closed (Lost)"),
        (
            BlockReason.LEAD_DISQUALIFIED,
            ReasonKind.LEAD_CLOSED,
            "The Lead is closed (Disqualified)",
        ),
        (BlockReason.CONTACT_ERASED, ReasonKind.CONTACT_ERASED, CONTACT_ERASED_TEXT),
    ],
)
def test_each_gate_reason_gives_its_eligibility_reason(
    reason: BlockReason, kind: ReasonKind, text: str
) -> None:
    decision = decide(us_contact(contactable=Blocked((reason,))), us_lead())

    assert decision.kinds == (kind,)
    assert texts(decision) == [text]


@pytest.mark.parametrize("reason", list(BlockReason), ids=[str(r) for r in BlockReason])
def test_every_gate_reason_alone_makes_an_otherwise_eligible_contact_ineligible(
    reason: BlockReason,
) -> None:
    """Fail closed: whatever ``BlockReason`` the gate gives (an erased Contact's included, Story
    3.13), the Contact that would otherwise be eligible isn't, and the reason says why."""
    assert decide(us_contact(), us_lead()).eligible is True  # the control

    decision = decide(us_contact(contactable=Blocked((reason,))), us_lead())

    assert decision.eligible is False
    assert decision.basis_rule is None
    assert BLOCK_REASON_KINDS[reason] in decision.kinds


def test_an_erased_contact_is_ineligible_with_every_other_reason_too() -> None:
    """An erased Contact's gate answer is ``contact_erased`` whatever the list holds, and its
    reason sits last, after any other one (Story 3.13)."""
    blocked = Blocked((BlockReason.ADDRESS_SUPPRESSED, BlockReason.CONTACT_ERASED))

    decision = decide(us_contact(contactable=blocked), us_lead(stage=PipelineStage.LOST))

    assert decision.kinds == (
        ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,
        ReasonKind.LEAD_CLOSED,
        ReasonKind.CONTACT_ERASED,
    )
    assert list(ReasonKind)[-1] is ReasonKind.CONTACT_ERASED


def test_every_suppressed_key_gives_both_reasons_once() -> None:
    decision = decide(us_contact(contactable=Blocked(tuple(BlockReason))), us_lead())

    assert decision.kinds == (
        ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,
        ReasonKind.ASKED_NOT_TO_BE_CONTACTED,
        ReasonKind.LEAD_CLOSED,
        ReasonKind.CONTACT_ERASED,  # Story 3.13's BlockReason, last in both orders
    )


@pytest.mark.parametrize(
    ("stage", "gate", "text"),
    [
        (PipelineStage.LOST, BlockReason.LEAD_LOST, "The Lead is closed (Lost)"),
        (
            PipelineStage.DISQUALIFIED,
            BlockReason.LEAD_DISQUALIFIED,
            "The Lead is closed (Disqualified)",
        ),
    ],
)
def test_a_closed_lead_gives_one_reason_even_with_the_gates(
    stage: PipelineStage, gate: BlockReason, text: str
) -> None:
    alone = decide(us_contact(), us_lead(stage=stage))
    with_gate = decide(us_contact(contactable=Blocked((gate,))), us_lead(stage=stage))

    assert alone.kinds == with_gate.kinds == (ReasonKind.LEAD_CLOSED,)
    assert texts(alone) == texts(with_gate) == [text]


# FR-63 --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("history", "events", "allowed"),
    [
        ((), (), True),  # an empty history
        ((ended(91, 80),), (ago(10),), True),  # 91 days and an Event first seen after the end
        ((ended(91, 80),), (), False),  # 91 days, no new Event
        ((ended(91, 80),), (ago(85),), False),  # an Event only before the end
        ((ended(60, 50),), (ago(10),), False),  # 60 days
        ((ended(90, 80),), (ago(10),), True),  # exactly 90 days
        ((ended(90 - 1 / 86400, 80),), (ago(10),), False),  # a second short of 90 days
        ((ended(91, 80),), (ago(80),), False),  # an Event first seen as it ended: not after
        ((ended(91, 80),), (NOW,), True),  # an Event first seen at now counts
        ((ended(91, 80),), (NOW + timedelta(seconds=1),), False),  # not yet seen at now
        ((ended(30, 20, replied=True),), (), True),  # a Reply: outside this predicate
        ((ended(200, 190), ended(60, 50)), (ago(10),), False),  # every Sequence must pass
        ((ended(200, 190), ended(100, 95)), (ago(10),), True),
    ],
)
def test_the_recontact_predicate(
    history: tuple[EndedSequence, ...], events: tuple[datetime, ...], allowed: bool
) -> None:
    assert recontact_allowed(history, events, NOW) is allowed
    decision = decide(us_contact(history=history), us_lead(events_first_seen=events))
    assert (ReasonKind.RECONTACT_NOT_ALLOWED not in decision.kinds) is allowed


def test_the_recontact_wait_is_90_days() -> None:
    assert RECONTACT_WAIT == timedelta(days=90)


def test_the_recontact_predicate_checks_its_arguments() -> None:
    with pytest.raises(TypeError):
        recontact_allowed([], (), NOW)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        recontact_allowed(("x",), (), NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone-aware"):
        recontact_allowed((), (datetime(2026, 1, 1),), NOW)
    with pytest.raises(ValueError, match="timezone-aware"):
        recontact_allowed((), (), datetime(2026, 1, 1))


# Not Now ------------------------------------------------------------------------------------------


def test_a_future_recontact_date_gives_its_dated_reason() -> None:
    decision = decide(
        us_contact(), us_lead(stage=PipelineStage.NOT_NOW, recontact_on=date(2027, 1, 15))
    )

    [reason] = decision.reasons
    assert reason.kind is ReasonKind.NOT_NOW_UNTIL
    assert reason.until == date(2027, 1, 15)
    assert reason.code == "not_now_until_2027-01-15"
    assert reason.text == "Parked Not Now until 2027-01-15"


@pytest.mark.parametrize(
    ("recontact_on", "now", "kinds"),
    [
        (date(2026, 11, 19), NOW, (ReasonKind.NOT_NOW_UNTIL,)),  # tomorrow
        (date(2026, 11, 18), NOW, ()),  # today: the date has arrived
        (date(2026, 1, 1), NOW, ()),  # long past
        (None, NOW, (ReasonKind.NOT_NOW_UNDATED,)),
        # 23:30 at UTC-5 is already the 19th in UTC: the UTC date decides
        (
            date(2026, 11, 19),
            datetime(2026, 11, 18, 23, 30, tzinfo=timezone(timedelta(hours=-5))),
            (),
        ),
        (
            date(2026, 11, 19),
            datetime(2026, 11, 19, 0, 30, tzinfo=timezone(timedelta(hours=5))),
            (ReasonKind.NOT_NOW_UNTIL,),
        ),
    ],
)
def test_the_not_now_date_boundary(
    recontact_on: date | None, now: datetime, kinds: tuple[ReasonKind, ...]
) -> None:
    lead = us_lead(stage=PipelineStage.NOT_NOW, recontact_on=recontact_on)

    assert decide(us_contact(), lead, now=now).kinds == kinds


def test_a_recontact_date_counts_only_for_a_not_now_lead() -> None:
    lead = us_lead(stage=PipelineStage.CONTACTED, recontact_on=date(2027, 1, 15))

    assert decide(us_contact(), lead).eligible is True


# Reason and Decision ------------------------------------------------------------------------------


def test_a_reason_code_is_its_kind_or_the_dated_form() -> None:
    assert Reason(ReasonKind.LEAD_CLOSED, "x").code == "lead_closed"
    assert Reason(ReasonKind.NOT_NOW_UNTIL, "x", date(2027, 1, 5)).code == (
        "not_now_until_2027-01-05"
    )


@pytest.mark.parametrize(
    ("build", "error"),
    [
        (lambda: Reason("lead_closed", "x"), TypeError),  # type: ignore[arg-type]
        (lambda: Reason(ReasonKind.LEAD_CLOSED, None), TypeError),  # type: ignore[arg-type]
        (lambda: Reason(ReasonKind.LEAD_CLOSED, " \t"), ValueError),
        (lambda: Reason(ReasonKind.LEAD_CLOSED, ""), ValueError),
        (lambda: Reason(ReasonKind.NOT_NOW_UNTIL, "x"), ValueError),
        (lambda: Reason(ReasonKind.NOT_NOW_UNTIL, "x", NOW), TypeError),
        (lambda: Reason(ReasonKind.NOT_NOW_UNTIL, "x", "2027-01-15"), TypeError),
        (lambda: Reason(ReasonKind.LEAD_CLOSED, "x", date(2027, 1, 15)), ValueError),
    ],
)
def test_a_bad_reason_is_refused(build: Callable[[], Reason], error: type[Exception]) -> None:
    with pytest.raises(error):
        build()


CLOSED = Reason(ReasonKind.LEAD_CLOSED, "The Lead is closed (Lost)")
UNSENDABLE = Reason(ReasonKind.ADDRESS_NOT_SENDABLE, "The address has no known Address Class")


@pytest.mark.parametrize(
    ("build", "error"),
    [
        (lambda: Decision(True, None, ()), ValueError),
        (lambda: Decision(False, "US: basis", ()), ValueError),
        (lambda: Decision(True, "US: basis", (CLOSED,)), ValueError),
        (lambda: Decision(False, None, ()), ValueError),
        (lambda: Decision(False, "US: basis", (CLOSED,)), ValueError),
        (lambda: Decision(True, "  ", ()), ValueError),
        (lambda: Decision(False, None, (CLOSED, UNSENDABLE)), ValueError),  # out of order
        (lambda: Decision(False, None, (CLOSED, CLOSED)), ValueError),  # a repeated kind
        (lambda: Decision(1, "US: basis", ()), TypeError),  # type: ignore[arg-type]
        (lambda: Decision(True, 3, ()), TypeError),  # type: ignore[arg-type]
        (lambda: Decision(False, None, [CLOSED]), TypeError),  # type: ignore[arg-type]
        (lambda: Decision(False, None, ("lead_closed",)), TypeError),  # type: ignore[arg-type]
    ],
)
def test_a_bad_decision_is_refused(build: Callable[[], Decision], error: type[Exception]) -> None:
    with pytest.raises(error) as raised:
        build()
    assert "US: basis" not in str(raised.value)
    assert "Lost" not in str(raised.value)


def test_a_decision_gives_its_kinds_in_order() -> None:
    decision = Decision(False, None, (UNSENDABLE, CLOSED))

    assert decision.kinds == (ReasonKind.ADDRESS_NOT_SENDABLE, ReasonKind.LEAD_CLOSED)


def test_the_dataclasses_are_frozen_and_slotted() -> None:
    decision = decide(us_contact(), us_lead())
    for value in (decision, CLOSED, us_contact(), us_lead(), ended(91, 80)):
        assert not hasattr(value, "__dict__")
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(value, dataclasses.fields(value)[0].name, None)


# The facts ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("build", "error"),
    [
        (lambda: us_contact(contactable=None), TypeError),
        (lambda: us_contact(contactable="allowed"), TypeError),
        (lambda: us_contact(email=7), TypeError),
        (lambda: us_contact(address_class=b"published"), TypeError),
        (lambda: us_contact(market_id=1), TypeError),
        (lambda: us_contact(market_basis=1), TypeError),
        (lambda: us_contact(office_country=1), TypeError),
        (lambda: us_contact(office_region=1), TypeError),
        (lambda: us_contact(history=[]), TypeError),
        (lambda: us_contact(history=("x",)), TypeError),
        (lambda: us_contact(provenance_recorded=None), TypeError),
        (lambda: us_contact(provenance_recorded="yes"), TypeError),
        (lambda: us_contact(provenance_recorded=1), TypeError),
        (lambda: EndedSequence(datetime(2026, 1, 1), NOW, False), ValueError),
        (lambda: EndedSequence(NOW, datetime(2026, 1, 1), False), ValueError),
        (lambda: EndedSequence(NOW, NOW, 0), TypeError),
        (lambda: EndedSequence(NOW, NOW - timedelta(seconds=1), False), ValueError),
        (lambda: EndedSequence(date(2026, 1, 1), NOW, False), TypeError),
        (lambda: us_lead(stage="new"), TypeError),
        (lambda: us_lead(stage=None), TypeError),
        (lambda: us_lead(country_code=1), TypeError),
        (lambda: us_lead(recontact_on=NOW), TypeError),
        (lambda: us_lead(recontact_on="2027-01-15"), TypeError),
        (lambda: us_lead(legal_form=1), TypeError),
        (lambda: us_lead(legal_form="Ltd"), ValueError),
        (lambda: us_lead(legal_form="zelda.quill@acmelaw.com"), ValueError),
        (lambda: us_lead(legal_form_confirmed="yes"), TypeError),
        (lambda: us_lead(events_first_seen=[NOW]), TypeError),
        (lambda: us_lead(events_first_seen=(datetime(2026, 1, 1),)), ValueError),
        (lambda: us_lead(events_first_seen=(date(2026, 1, 1),)), TypeError),
    ],
)
def test_bad_facts_are_refused_without_their_values(
    build: Callable[[], object], error: type[Exception]
) -> None:
    with pytest.raises(error) as raised:
        build()
    message = str(raised.value).lower()
    for plain in ("zelda", "acmelaw", "2027", "yes"):
        assert plain not in message


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ((us_contact(), us_lead(), RULES, datetime(2026, 11, 18, 9)), ValueError),  # naive now
        ((us_contact(), us_lead(), RULES, date(2026, 11, 18)), TypeError),
        ((None, us_lead(), RULES, NOW), TypeError),
        ((us_contact(), None, RULES, NOW), TypeError),
        ((us_contact(), us_lead(), [US], NOW), TypeError),
        ((us_contact(), us_lead(), {US_ID: "us"}, NOW), TypeError),
        ((us_contact(), us_lead(), {1: US}, NOW), TypeError),
    ],
)
def test_bad_arguments_are_refused(arguments: tuple[Any, ...], error: type[Exception]) -> None:
    with pytest.raises(error):
        evaluate(*arguments)


def test_the_address_never_appears_in_a_repr() -> None:
    assert US_EMAIL not in repr(us_contact())
    assert "acme" not in repr(us_contact()).lower()


def test_no_text_holds_the_address_or_a_domain() -> None:
    decisions = [case[1]() for case in ONE_REASON]
    decisions += [
        decide(us_contact(market_id=None, market_basis=str(basis)), us_lead())
        for basis in MarketBasis
    ]
    decisions += [
        decide(us_contact(email="zelda.quill@acmelaw.de"), us_lead()),
        decide(uk_contact(email="zelda.quill@gmail.com"), uk_lead()),
        decide(uk_contact(), uk_lead()),
    ]

    for decision in decisions:
        assert_no_personal_data(decision)
        assert all(reason.text.strip() for reason in decision.reasons)


# The labels ---------------------------------------------------------------------------------------


def test_every_gb_corporate_form_has_a_label() -> None:
    assert set(LEGAL_FORM_LABELS) == markets_module._GB_CORPORATE_FORMS
    assert set(UK.eligible_legal_forms or ()) == set(LEGAL_FORM_LABELS)
    assert LEGAL_FORM_LABELS["ltd"] == "private limited company"
    assert all(label and label == label.strip() for label in LEGAL_FORM_LABELS.values())


@pytest.mark.parametrize("form", sorted(LEGAL_FORM_LABELS))
def test_each_listed_uk_form_passes_with_its_label(form: str) -> None:
    decision = decide(uk_contact(), uk_lead(legal_form=form))

    assert decision.basis_rule == UK_BASIS.format(label=LEGAL_FORM_LABELS[form])


# Story 3.7: Provenance and the basis width ------------------------------------------------------


def test_the_provenance_fact_is_required() -> None:
    fields = {
        "email": US_EMAIL,
        "address_class": "published",
        "market_id": US_ID,
        "market_basis": "lead_country",
        "office_country": None,
        "office_region": None,
        "contactable": Allowed(),
    }

    with pytest.raises(TypeError):
        EligibilityContact(**fields)  # type: ignore[call-arg]
    assert EligibilityContact(**fields, provenance_recorded=True).history == ()
    names = [field.name for field in dataclasses.fields(EligibilityContact)]
    assert names[names.index("contactable") + 1] == "provenance_recorded"


def test_provenance_missing_sits_after_address_not_sendable() -> None:
    kinds = list(ReasonKind)

    assert kinds.index(ReasonKind.PROVENANCE_MISSING) == (
        kinds.index(ReasonKind.ADDRESS_NOT_SENDABLE) + 1
    )
    assert ReasonKind.PROVENANCE_MISSING.value == "provenance_missing"
    assert PROVENANCE_MISSING_TEXT == (
        "Provenance missing: no Provenance Record shows where this Contact or its email address "
        "came from"
    )


def test_provenance_missing_is_listed_with_the_other_reasons_in_order() -> None:
    decision = decide(
        us_contact(
            address_class="guessed",
            provenance_recorded=False,
            contactable=Blocked((BlockReason.ADDRESS_SUPPRESSED,)),
        ),
        us_lead(),
    )

    assert decision.kinds == (
        ReasonKind.ADDRESS_NOT_SENDABLE,
        ReasonKind.PROVENANCE_MISSING,
        ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,
    )
    assert_no_personal_data(decision)


def test_a_uk_contact_with_missing_provenance_is_not_eligible() -> None:
    decision = decide(uk_contact(provenance_recorded=False), uk_lead())

    assert decision == Decision(
        False, None, (Reason(ReasonKind.PROVENANCE_MISSING, PROVENANCE_MISSING_TEXT),)
    )


def test_the_basis_rule_width_boundary() -> None:
    assert MAX_BASIS_RULE_LENGTH == 128
    assert Decision(True, "x" * 128, ()).basis_rule == "x" * 128
    with pytest.raises(ValueError) as raised:
        Decision(True, "secretbasis" + "x" * 118, ())
    assert "secretbasis" not in str(raised.value)
    assert "128" in str(raised.value)


def test_every_labelled_basis_and_the_longest_code_fit_the_width() -> None:
    labelled = [UK_BASIS.format(label=label) for label in LEGAL_FORM_LABELS.values()]
    longest_code = "a" + "b" * 63  # LEGAL_FORM_PATTERN's longest code, which has no label
    listed = dataclasses.replace(UK, eligible_legal_forms=(longest_code,))

    decision = decide(uk_contact(), uk_lead(legal_form=longest_code), {**RULES, UK_ID: listed})

    assert max(len(rule) for rule in labelled) == 108
    assert min(len(rule) for rule in labelled) == 71
    assert len(US_BASIS) <= MAX_BASIS_RULE_LENGTH
    assert decision.basis_rule == UK_BASIS.format(label=longest_code)
    assert len(decision.basis_rule) == 113


@pytest.mark.parametrize(
    ("rule", "country"),
    [
        (US_BASIS, "US"),
        (UK_BASIS.format(label="private limited company"), "GB"),
        (UK_BASIS.format(label="widget-co"), "GB"),
        (UK_BASIS.format(label="x"), "GB"),
        (UK_BASIS.format(label=""), None),
        (UK_BASIS.format(label="   "), None),
        ("UK: corporate subscriber (ltd), legitimate interest", None),
        ("uk: corporate subscriber (ltd), legitimate interests", None),
        ("US: cold email allowed (CAN-SPAM) ", None),
        ("US: cold email allowed", None),
        ("", None),
        (None, None),
        (7, None),
        (b"US: cold email allowed (CAN-SPAM)", None),
    ],
)
def test_basis_country(rule: object, country: str | None) -> None:
    assert basis_country(rule) == country


@pytest.mark.parametrize("form", sorted(LEGAL_FORM_LABELS))
def test_every_basis_evaluate_gives_names_its_country(form: str) -> None:
    uk = decide(uk_contact(), uk_lead(legal_form=form))
    us = decide(us_contact(), us_lead())

    assert basis_country(uk.basis_rule) == "GB"
    assert basis_country(us.basis_rule) == "US"
