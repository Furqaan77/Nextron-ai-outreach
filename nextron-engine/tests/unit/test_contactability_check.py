"""The contactability gate's pure core (``nextron.domain.contactability``, FR-50, FR-15, FR-63,
AD-16, Story 3.4): one table-driven case per ``BlockReason``, a voided (absent) entry giving
``Allowed``, every reason at once in enum order, ``channel`` and ``purpose`` never changing the
answer, ``Blocked``'s checks, the closed enums and their parsers, ``lookup_keys`` (order,
de-duplication, free webmail, values that can't be normalised) and the facts' and subjects'
type checks, with no personal value in a ``repr``."""

import dataclasses
import itertools

import pytest

from nextron.domain.contactability import (
    SUPPRESSION_REASONS,
    Allowed,
    Blocked,
    BlockReason,
    Channel,
    ContactFacts,
    KeyKind,
    LeadFacts,
    NewContactSubject,
    NewLeadSubject,
    Purpose,
    SuppressionKey,
    channel_of,
    check,
    key_hash,
    lookup_keys,
    purpose_of,
    suppression_key,
)
from nextron.domain.pipeline_stage import PipelineStage

ULID = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
EMAIL = "jane.doe@firm.co.uk"
NAME = "Jane Doe"
LINKEDIN = "https://www.linkedin.com/in/jane-doe"
WEB = "firm.co.uk"


def pair(kind: str, value: str | tuple[str, str]) -> tuple[str, str]:
    return (kind, key_hash(kind, value))


def jane(lead: LeadFacts | None = None, **values: str | None) -> ContactFacts:
    fields = {"email": EMAIL, "name": NAME, "linkedin_url": LINKEDIN} | values
    return ContactFacts(lead or LeadFacts(WEB), **fields)


ADDRESS = pair("address", EMAIL)
FIRM_DOMAIN = pair("domain", WEB)
PERSON = pair("person", (NAME, WEB))
PROFILE = pair("profile", LINKEDIN)


# One case per reason ----------------------------------------------------------------------------

REASON_CASES = [
    ("address", jane(), {ADDRESS}, BlockReason.ADDRESS_SUPPRESSED),
    (
        "address domain",
        jane(email="jane@mail.firm-group.com"),
        {pair("domain", "firm-group.com")},
        BlockReason.ADDRESS_DOMAIN_SUPPRESSED,
    ),
    (
        "lead web domain, a Contact",
        jane(email=None),
        {FIRM_DOMAIN},
        BlockReason.LEAD_DOMAIN_SUPPRESSED,
    ),
    ("lead web domain, a Lead", LeadFacts(WEB), {FIRM_DOMAIN}, BlockReason.LEAD_DOMAIN_SUPPRESSED),
    (
        "lead mail domain",
        LeadFacts(WEB, ("firm-mail.com",)),
        {pair("domain", "firm-mail.com")},
        BlockReason.LEAD_DOMAIN_SUPPRESSED,
    ),
    ("person", jane(), {PERSON}, BlockReason.PERSON_SUPPRESSED),
    ("profile", jane(), {PROFILE}, BlockReason.PROFILE_SUPPRESSED),
    (
        "lost Contact",
        jane(LeadFacts(WEB, stage=PipelineStage.LOST)),
        set(),
        BlockReason.LEAD_LOST,
    ),
    ("lost Lead", LeadFacts(WEB, stage=PipelineStage.LOST), set(), BlockReason.LEAD_LOST),
    (
        "disqualified Contact",
        jane(LeadFacts(WEB, stage=PipelineStage.DISQUALIFIED)),
        set(),
        BlockReason.LEAD_DISQUALIFIED,
    ),
    (
        "disqualified Lead",
        LeadFacts(None, stage=PipelineStage.DISQUALIFIED),
        set(),
        BlockReason.LEAD_DISQUALIFIED,
    ),
]


@pytest.mark.parametrize(
    ("facts", "active", "reason"),
    [case[1:] for case in REASON_CASES],
    ids=[case[0] for case in REASON_CASES],
)
def test_each_reason_blocks_alone(
    facts: LeadFacts | ContactFacts, active: set[tuple[str, str]], reason: BlockReason
) -> None:
    assert check(facts, "email", "cold", active) == Blocked((reason,))


def test_every_reason_is_covered_by_a_case() -> None:
    assert {case[3] for case in REASON_CASES} == set(BlockReason)


def test_a_voided_entry_is_not_active_so_nothing_blocks() -> None:
    # the gate passes only active pairs: a voided entry's pair is simply absent
    assert check(jane(), "email", "cold", frozenset()) == Allowed()
    assert check(jane(), "email", "cold", {pair("address", "other@firm.co.uk")}) == Allowed()


OPEN_OR_PARKED = [
    stage
    for stage in PipelineStage
    if stage not in (PipelineStage.LOST, PipelineStage.DISQUALIFIED)
]


@pytest.mark.parametrize("stage", OPEN_OR_PARKED)
def test_every_other_stage_allows(stage: PipelineStage) -> None:
    assert check(jane(LeadFacts(WEB, stage=stage)), "email", "cold", ()) == Allowed()


def test_every_reason_at_once_comes_in_enum_order() -> None:
    facts = jane(LeadFacts(WEB, ("firm-mail.com",), PipelineStage.LOST))
    active = {ADDRESS, FIRM_DOMAIN, PERSON, PROFILE, pair("domain", "firm-mail.com")}

    answer = check(facts, "email", "cold", active)

    # the address's domain is the Lead's web domain: one key, two reasons
    assert answer == Blocked(
        (
            BlockReason.ADDRESS_SUPPRESSED,
            BlockReason.ADDRESS_DOMAIN_SUPPRESSED,
            BlockReason.LEAD_DOMAIN_SUPPRESSED,
            BlockReason.PERSON_SUPPRESSED,
            BlockReason.PROFILE_SUPPRESSED,
            BlockReason.LEAD_LOST,
        )
    )


def test_a_mail_domain_alone_gives_the_lead_domain_reason_once() -> None:
    facts = LeadFacts(WEB, ("firm-mail.com", "firm-mail.co.uk"))
    active = {pair("domain", "firm-mail.com"), pair("domain", "firm-mail.co.uk")}

    assert check(facts, "email", "cold", active) == Blocked((BlockReason.LEAD_DOMAIN_SUPPRESSED,))


def test_the_active_pairs_may_use_key_kinds_or_texts() -> None:
    by_kind = {(KeyKind.ADDRESS, key_hash("address", EMAIL))}

    assert check(jane(), "email", "cold", by_kind) == Blocked((BlockReason.ADDRESS_SUPPRESSED,))
    assert check(jane(), "email", "cold", [ADDRESS]) == Blocked((BlockReason.ADDRESS_SUPPRESSED,))


def test_a_person_key_needs_the_leads_web_domain() -> None:
    no_web = jane(LeadFacts(None), email=None, linkedin_url=None)

    assert lookup_keys(no_web) == ()
    assert check(no_web, "email", "cold", {PERSON}) == Allowed()


def test_the_suppression_reasons_are_the_five_key_reasons() -> None:
    assert SUPPRESSION_REASONS == {
        BlockReason.ADDRESS_SUPPRESSED,
        BlockReason.ADDRESS_DOMAIN_SUPPRESSED,
        BlockReason.LEAD_DOMAIN_SUPPRESSED,
        BlockReason.PERSON_SUPPRESSED,
        BlockReason.PROFILE_SUPPRESSED,
    }


# Channel and purpose ----------------------------------------------------------------------------


def test_channel_and_purpose_never_change_the_answer() -> None:
    for channel, purpose in itertools.product(Channel, Purpose):
        assert check(jane(), channel, purpose, {PROFILE}) == Blocked(
            (BlockReason.PROFILE_SUPPRESSED,)
        )
        assert check(jane(), str(channel), str(purpose), ()) == Allowed()


def test_the_enums_are_closed() -> None:
    assert [str(c) for c in Channel] == ["email", "linkedin", "whatsapp", "instagram", "phone"]
    assert [str(p) for p in Purpose] == [
        "cold",
        "follow_up",
        "reply",
        "referral",
        "rebook",
        "recontact",
        "task",
        "call",
        "discovery",
    ]
    assert [str(r) for r in BlockReason] == [
        "address_suppressed",
        "address_domain_suppressed",
        "lead_domain_suppressed",
        "person_suppressed",
        "profile_suppressed",
        "lead_lost",
        "lead_disqualified",
    ]


def test_the_parsers_take_a_member_or_its_text() -> None:
    assert channel_of("whatsapp") is Channel.WHATSAPP
    assert channel_of(Channel.PHONE) is Channel.PHONE
    assert purpose_of("follow_up") is Purpose.FOLLOW_UP
    assert purpose_of(Purpose.DISCOVERY) is Purpose.DISCOVERY


@pytest.mark.parametrize("value", ["Email", "sms", "", " email", Purpose.CALL])
def test_an_unknown_channel_raises_value_error_without_the_value(value: str) -> None:
    with pytest.raises(ValueError, match="channel must be one of") as raised:
        channel_of(value)
    assert "sms" not in str(raised.value) and "Email" not in str(raised.value)


@pytest.mark.parametrize("value", ["COLD", "newsletter", "", Channel.EMAIL])
def test_an_unknown_purpose_raises_value_error(value: str) -> None:
    with pytest.raises(ValueError, match="purpose must be one of"):
        purpose_of(value)


@pytest.mark.parametrize("value", [None, 1, b"email", ("email",), True])
def test_a_channel_or_purpose_that_is_not_a_str_raises_type_error(value: object) -> None:
    with pytest.raises(TypeError, match="channel must be a str"):
        channel_of(value)
    with pytest.raises(TypeError, match="purpose must be a str"):
        purpose_of(value)


def test_check_validates_channel_and_purpose_first() -> None:
    with pytest.raises(ValueError, match="channel"):
        check(jane(), "fax", "cold", ())
    with pytest.raises(ValueError, match="purpose"):
        check(jane(), "email", "spam", ())
    with pytest.raises(TypeError, match="channel"):
        check(jane(), None, "cold", ())


@pytest.mark.parametrize("facts", [None, "firm.co.uk", NewLeadSubject(WEB), {"web_domain": WEB}])
def test_check_takes_only_facts(facts: object) -> None:
    with pytest.raises(TypeError, match="facts must be a LeadFacts or ContactFacts"):
        check(facts, "email", "cold", ())  # type: ignore[arg-type]


@pytest.mark.parametrize("active", [None, "address", b"x", 3])
def test_check_takes_a_collection_of_pairs(active: object) -> None:
    with pytest.raises(TypeError, match="active must be a collection"):
        check(jane(), "email", "cold", active)  # type: ignore[arg-type]


# Blocked ----------------------------------------------------------------------------------------


def test_blocked_holds_distinct_reasons_in_order() -> None:
    reasons = (BlockReason.ADDRESS_SUPPRESSED, BlockReason.LEAD_LOST)

    assert Blocked(reasons).reasons == reasons
    assert Blocked(tuple(BlockReason)).reasons == tuple(BlockReason)


@pytest.mark.parametrize(
    "reasons",
    [
        (),
        ("address_suppressed",),
        (BlockReason.LEAD_LOST, "lead_disqualified"),
        (BlockReason.LEAD_LOST, BlockReason.LEAD_LOST),
        (BlockReason.LEAD_LOST, BlockReason.ADDRESS_SUPPRESSED),
        (
            BlockReason.PERSON_SUPPRESSED,
            BlockReason.PROFILE_SUPPRESSED,
            BlockReason.PERSON_SUPPRESSED,
        ),
    ],
    ids=["empty", "plain text", "mixed", "repeat", "out of order", "repeat later"],
)
def test_blocked_refuses_bad_reasons(reasons: tuple[object, ...]) -> None:
    with pytest.raises(ValueError, match="Blocked"):
        Blocked(reasons)  # type: ignore[arg-type]


def test_blocked_reasons_must_be_a_tuple() -> None:
    with pytest.raises(TypeError, match="must be a tuple"):
        Blocked([BlockReason.LEAD_LOST])  # type: ignore[arg-type]


def test_the_answers_are_frozen_values() -> None:
    assert Allowed() == Allowed()
    assert hash(Blocked((BlockReason.LEAD_LOST,))) == hash(Blocked((BlockReason.LEAD_LOST,)))
    with pytest.raises(dataclasses.FrozenInstanceError):
        Blocked((BlockReason.LEAD_LOST,)).reasons = ()  # type: ignore[misc]


# lookup_keys ------------------------------------------------------------------------------------


def test_lookup_keys_follow_the_check_order() -> None:
    facts = jane(LeadFacts(WEB, ("firm-mail.com",)), email="jane@mail.firm-group.com")

    assert lookup_keys(facts) == (
        suppression_key("address", "jane@mail.firm-group.com"),
        suppression_key("domain", "firm-group.com"),
        suppression_key("domain", WEB),
        suppression_key("domain", "firm-mail.com"),
        suppression_key("person", (NAME, WEB)),
        suppression_key("profile", LINKEDIN),
    )


def test_lookup_keys_give_each_key_once() -> None:
    facts = jane(LeadFacts("www.firm.co.uk", ("firm.co.uk", "mail.firm.co.uk")))

    assert lookup_keys(facts) == (
        suppression_key("address", EMAIL),
        suppression_key("domain", WEB),
        suppression_key("person", (NAME, WEB)),
        suppression_key("profile", LINKEDIN),
    )


def test_a_free_webmail_domain_is_never_a_key() -> None:
    facts = ContactFacts(LeadFacts(None, ("gmail.com",)), email="jane@gmail.com")
    gmail = pair("domain", "gmail.com")

    assert lookup_keys(facts) == (suppression_key("address", "jane@gmail.com"),)
    assert check(facts, "email", "cold", {gmail}) == Allowed()
    assert lookup_keys(LeadFacts("btinternet.com")) == ()


def test_a_value_that_cannot_be_normalised_gives_no_key() -> None:
    facts = ContactFacts(
        LeadFacts("not a domain", ("localhost",)),
        email="not-an-email",
        name="!!!",
        linkedin_url="https://example.com/jane",
    )

    assert lookup_keys(facts) == ()
    assert check(facts, "email", "cold", ()) == Allowed()


def test_a_lead_without_a_web_domain_has_only_its_mail_domains() -> None:
    assert lookup_keys(LeadFacts(None)) == ()
    assert lookup_keys(LeadFacts(None, ("firm-mail.com",))) == (
        suppression_key("domain", "firm-mail.com"),
    )


def test_lookup_keys_are_suppression_keys() -> None:
    assert all(isinstance(key, SuppressionKey) for key in lookup_keys(jane()))


# Facts and subjects -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        lambda: LeadFacts(1),  # type: ignore[arg-type]
        lambda: LeadFacts(WEB, ["firm-mail.com"]),  # type: ignore[arg-type]
        lambda: LeadFacts(WEB, ("firm-mail.com", 2)),  # type: ignore[arg-type]
        lambda: LeadFacts(WEB, stage="lost"),  # type: ignore[arg-type]
        lambda: ContactFacts(WEB),  # type: ignore[arg-type]
        lambda: ContactFacts(LeadFacts(WEB), email=1),  # type: ignore[arg-type]
        lambda: ContactFacts(LeadFacts(WEB), name=b"Jane"),  # type: ignore[arg-type]
        lambda: ContactFacts(LeadFacts(WEB), linkedin_url=[]),  # type: ignore[arg-type]
        lambda: NewContactSubject(ULID, email=1),  # type: ignore[arg-type]
        lambda: NewContactSubject(ULID, name=2),  # type: ignore[arg-type]
        lambda: NewContactSubject(ULID, linkedin_url=3),  # type: ignore[arg-type]
        lambda: NewLeadSubject(1),  # type: ignore[arg-type]
        lambda: NewLeadSubject(WEB, ["firm-mail.com"]),  # type: ignore[arg-type]
        lambda: NewLeadSubject(WEB, (None,)),  # type: ignore[arg-type]
    ],
)
def test_a_wrong_type_raises_type_error(build: object) -> None:
    with pytest.raises(TypeError):
        build()  # type: ignore[operator]


@pytest.mark.parametrize("lead_id", ["", "not-a-ulid", ULID.lower() + "x", None, 7])
def test_a_new_contact_subject_needs_a_ulid_lead(lead_id: object) -> None:
    with pytest.raises(ValueError, match="NewContactSubject.lead_id must be a ULID"):
        NewContactSubject(lead_id)  # type: ignore[arg-type]


def test_the_subjects_and_facts_hold_their_values() -> None:
    subject = NewContactSubject(ULID, email=EMAIL, name=NAME, linkedin_url=LINKEDIN)
    lead = NewLeadSubject(WEB, ("firm-mail.com",))

    assert (subject.lead_id, subject.email, subject.name, subject.linkedin_url) == (
        ULID,
        EMAIL,
        NAME,
        LINKEDIN,
    )
    assert (lead.web_domain, lead.mail_domains) == (WEB, ("firm-mail.com",))
    assert NewLeadSubject(None).web_domain is None
    assert NewContactSubject(ULID) == NewContactSubject(ULID, None, None, None)
    assert LeadFacts(WEB) == LeadFacts(WEB, (), None)


def test_no_personal_value_appears_in_a_repr() -> None:
    texts = repr(NewContactSubject(ULID, email=EMAIL, name=NAME, linkedin_url=LINKEDIN)) + repr(
        jane()
    )

    for planted in ("jane", "Jane", "Doe", "linkedin", "@"):
        assert planted not in texts
    assert ULID in texts
