"""``Compliance.evaluate_eligibility`` (FR-18, FR-2, FR-63, AD-16, NFR-7; Story 3.6) on a temporary
database at head with ``FrozenClock``, the real subscriber registry and the shipped Segments and
Markets: every app row of the I/O matrix through real units of work. A US Contact passes; a CSV
UK Contact fails as every UK Contact does today; a Contact without a Market says why; a Market
disabled (and one enabled later) with ``Setup.sync_markets``; a stale resolution; unreadable
Markets (a row broken by raw SQL) and an unreadable office; the Market rules; the address;
suppression; Not Now; closed Leads; several reasons; the live re-evaluation after each change;
bad input. A recording fake of ``assert_contactable`` sees the one gate call, and a recording
wrapper of ``evaluate`` sees the facts (decisions 4, 6 and 7). It runs in a read-only unit of
work and inside a transaction alike, writes nothing (no write statement, no new row) and logs
nothing."""

import dataclasses
import logging
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event

from nextron import main
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.discovery.api import Discovery, ImportRequest, LeadImports
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.research.api import Research
from nextron.app.setup.api import Setup
from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.contactability import (
    Allowed,
    Blocked,
    BlockReason,
    ContactSubject,
    SuppressionScope,
    suppression_key,
)
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.eligibility import (
    ASKED_TEXT,
    LEGAL_FORM_UNCONFIRMED_TEXT,
    NO_EMAIL_TEXT,
    NO_MARKET_OFFICE_TEXT,
    NO_MARKET_RULES_TEXT,
    NO_MARKET_STALE_TEXT,
    NO_MARKET_UNRESOLVED_TEXT,
    NOT_NOW_UNDATED_TEXT,
    SUPPRESSED_TEXT,
    US_BASIS,
    Decision,
    EligibilityContact,
    EligibilityLead,
    ReasonKind,
)
from nextron.domain.geo import Location
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.markets import MarketDef, validate_markets
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.segments import validate_segments
from nextron.domain.suppression_entry import SuppressionSource
from tests.fakes.clock import FrozenClock
from tests.fakes.email_syntax import FakeEmailSyntax

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
LATER = T0 + timedelta(hours=1)
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
SHIPPED_MARKETS = main.SEED_DIR / main.MARKETS_FILE_NAME
ORIGIN = "Law Society directory, Manchester, 2026"
UNKNOWN = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
US_WEB = "acmelaw.com"
UK_WEB = "acmelaw.co.uk"
CA_WEB = "acmelaw.ca"
US_EMAIL = "zelda.quill@acmelaw.com"
UK_EMAIL = "zelda.quill@acmelaw.co.uk"
NAME = "Zelda Quill"
OTHER_MAIL = "zelda.quill@acme-mail.com"  # an address on another domain than the Lead's
LINKEDIN = "https://www.linkedin.com/in/zelda-quill"
TABLES = (
    "leads",
    "contacts",
    "provenance_records",
    "events",
    "jobs",
    "suppression_entries",
    "markets",
)
UK_LIA = "UK: corporate subscriber"
# Text that must never reach a reason, a basis or the log.
PLAIN = ("zelda", "quill", "acmelaw", "@")


def csv_bytes(*lines: str) -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def shipped_markets() -> tuple[MarketDef, MarketDef]:
    found = validate_markets(seed_yaml.parse(SHIPPED_MARKETS.read_text(encoding="utf-8")))
    [us] = [market for market in found if market.code == "us"]
    [uk] = [market for market in found if market.code == "uk"]
    return us, uk


US, UK = shipped_markets()
CANADA = dataclasses.replace(
    UK,
    code="ca",
    name="Canada",
    country_code="CA",
    cctlds=("ca",),
    cold_email_allowed=False,
    eligible_legal_forms=None,
    free_webmail_blocked=False,
    default_timezone="America/Toronto",
    unknown_timezone="America/Toronto",
)


@dataclass
class World:
    """Discovery, Research, Compliance and the CSV import on real units of work."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    discovery: Discovery
    research: Research
    compliance: Compliance
    imports: LeadImports
    segment: str = ""

    def transact[R](self, fn: Callable[[], R]) -> R:
        """Run ``fn`` inside a caller's use case and transaction, as a service would."""

        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def evaluate(self, contact_id: str) -> Decision:
        """The live Decision, in a read-only unit of work (as the Lead page runs it)."""
        with self.units.open(read_only=True):
            return self.compliance.evaluate_eligibility(contact_id)

    def lead(self, web_domain: str, country: str | None, name: str = "Acme Law") -> str:
        identity = LeadIdentity(name, self.segment, web_domain=web_domain, country_code=country)
        lead_id = self.transact(
            lambda: self.discovery.upsert_lead(identity, LeadSource.CSV)
        ).lead_id
        assert lead_id is not None
        return lead_id

    def contact(
        self,
        lead_id: str,
        source: ProvenanceSource = ProvenanceSource.WEBSITE,
        **fields: Any,
    ) -> str:
        """A stored Contact; the default source, the website, makes its address Published."""
        candidate = ContactCandidate(**fields)
        added = self.transact(
            lambda: self.research.add_contact(
                lead_id, candidate, source=source, detail=ORIGIN, retrieved_at=T0
            )
        )
        assert added.contact_id is not None
        return added.contact_id

    def us_contact(self) -> str:
        return self.contact(self.lead(US_WEB, "US"), email=US_EMAIL, name=NAME)

    def sync(self, *markets: MarketDef) -> None:
        with self.units.open():
            Setup(self.clock).sync_markets(list(markets))

    def suppress(self, contact_id: str, scope: SuppressionScope = SuppressionScope.PERSON) -> None:
        self.transact(
            lambda: self.compliance.suppress(
                ContactSubject(contact_id), scope, SuppressionSource.OPERATOR, None
            )
        )

    def entry(self, kind: str, value: str | tuple[str, str]) -> None:
        """An ``active`` Suppression List entry for the key, stored through the repository."""
        key = suppression_key(kind, value)
        self.transact(
            lambda: entries_repo.insert(
                kind=str(key.kind),
                value_hash=key.value_hash,
                domain_plain=key.domain_plain,
                source="operator",
                cause_ref=None,
                created_at=self.clock.now(),
            )
        )

    @property
    def db_file(self) -> Path:
        assert self.engine.url.database
        return Path(self.engine.url.database)

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        connection = sqlite3.connect(self.db_file, isolation_level=None)
        try:
            return [tuple(row) for row in connection.execute(statement, params).fetchall()]
        finally:
            connection.close()

    def counts(self) -> dict[str, int]:
        return {table: self.sql(f"SELECT COUNT(*) FROM {table}")[0][0] for table in TABLES}

    def set_stage(self, lead_id: str, stage: str) -> None:
        self.sql("UPDATE leads SET pipeline_stage = ? WHERE id = ?", (stage, lead_id))

    def lead_of(self, contact_id: str) -> str:
        [(lead_id,)] = self.sql("SELECT lead_id FROM contacts WHERE id = ?", (contact_id,))
        return str(lead_id)


def no_jobs() -> Any:
    """``build_job_types``'s services: no job runs in these tests."""
    raise AssertionError("no job runs in these tests")


def queue_on(clock: FrozenClock) -> JobQueue:
    """A job queue on the real job types; ``upsert_lead`` enqueues a CSV Lead's crawl there."""
    job_types = main.build_job_types(
        alerts=no_jobs,
        backups=no_jobs,
        restore_tests=no_jobs,
        recovery=no_jobs,
        contact_markets=no_jobs,
        crawls=no_jobs,
        discovery=no_jobs,
        research=no_jobs,
        extractions=no_jobs,
        primary=no_jobs,
        job_boards=no_jobs,
    )
    return JobQueue(clock, job_types)


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    with units.open():
        Setup(clock).sync_markets([US, UK])
    discovery = Discovery(clock, Pipeline(clock), queue_on(clock))
    research = Research(clock)
    found = World(
        units,
        db_engine,
        clock,
        discovery,
        research,
        Compliance(clock),
        LeadImports(clock, discovery, research, FakeEmailSyntax()),
    )
    [(segment,), *_] = found.sql("SELECT id FROM segments ORDER BY key")
    found.segment = segment
    clock.set(LATER)
    return found


@pytest.fixture
def statements(world: World) -> Iterator[list[str]]:
    """Every SQL statement the engine runs while the test records."""
    seen: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(" ".join(statement.split()))

    event.listen(world.engine, "before_cursor_execute", record)
    yield seen
    event.remove(world.engine, "before_cursor_execute", record)


def writes(seen: list[str]) -> list[str]:
    return [s for s in seen if s.split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}]


def texts(decision: Decision) -> list[str]:
    return [reason.text for reason in decision.reasons]


def assert_no_personal_data(values: object) -> None:
    text = repr(values).lower()
    for plain in PLAIN:
        assert plain not in text, plain


# Passing, and the UK today ------------------------------------------------------------------------


def test_a_us_contact_with_a_published_address_is_eligible(world: World) -> None:
    contact_id = world.us_contact()

    decision = world.evaluate(contact_id)

    assert decision == Decision(True, US_BASIS, ())


def test_a_csv_uk_contact_is_not_eligible_until_its_legal_form_is_confirmed(
    world: World,
) -> None:
    with world.units.open():
        world.imports.run(
            ImportRequest(
                ORIGIN,
                "law_private_ai",
                csv_bytes(
                    "Organization name,Website,Country,Contact name,Email",
                    f"Acme Law,https://{UK_WEB}/,UK,{NAME},{UK_EMAIL}",
                ),
            )
        )
    [(contact_id,)] = world.sql("SELECT id FROM contacts")

    decision = world.evaluate(contact_id)

    assert decision.eligible is False
    assert decision.kinds == (
        ReasonKind.UK_LEGAL_FORM_UNCONFIRMED,
        ReasonKind.ADDRESS_NOT_SENDABLE,
    )
    assert texts(decision) == [
        LEGAL_FORM_UNCONFIRMED_TEXT,
        "The address is Guessed; only Published or Verified addresses can be emailed",
    ]
    assert_no_personal_data(decision)


def test_a_published_uk_contact_still_waits_for_its_legal_form(world: World) -> None:
    contact_id = world.contact(world.lead(UK_WEB, "GB"), email=UK_EMAIL, name=NAME)

    decision = world.evaluate(contact_id)

    assert decision.kinds == (ReasonKind.UK_LEGAL_FORM_UNCONFIRMED,)
    assert UK_LIA not in repr(decision)


# The Market ---------------------------------------------------------------------------------------


def test_a_contact_without_a_market_says_why_then_that_it_isnt_resolved(world: World) -> None:
    contact_id = world.contact(world.lead(UK_WEB, "GB"), email="zelda.quill@acmelaw.de")

    blocked = world.evaluate(contact_id)
    world.sql(
        "UPDATE contacts SET market_id = NULL, tz = NULL, market_basis = NULL, tz_basis = NULL"
    )
    unresolved = world.evaluate(contact_id)

    assert blocked.kinds == unresolved.kinds == (ReasonKind.NO_ENABLED_MARKET,)
    assert texts(blocked) == [
        "No enabled Market: the email address is on a country-code domain outside the enabled "
        "Markets"
    ]
    assert texts(unresolved) == [NO_MARKET_UNRESOLVED_TEXT]
    assert_no_personal_data(blocked)


def test_a_disabled_market_blocks_at_once_and_enabling_it_again_passes(world: World) -> None:
    contact_id = world.us_contact()

    world.sync(dataclasses.replace(US, enabled=False), UK)
    disabled = world.evaluate(contact_id)
    world.sync(US, UK)
    enabled = world.evaluate(contact_id)

    assert disabled.kinds == (ReasonKind.NO_ENABLED_MARKET,)
    assert texts(disabled) == ["No enabled Market: the United States Market is disabled"]
    assert enabled.eligible is True


def test_a_market_enabled_later_leaves_a_blocked_contact_blocked(world: World) -> None:
    contact_id = world.contact(world.lead(CA_WEB, "CA"), email="zelda.quill@acmelaw.com")

    world.sync(US, UK, CANADA)
    decision = world.evaluate(contact_id)

    assert decision.kinds == (ReasonKind.NO_ENABLED_MARKET,)
    assert texts(decision) == [
        "No enabled Market: the office location or the Lead's country isn't in an enabled Market"
    ]


def test_a_contact_in_a_market_without_cold_email_is_refused(world: World) -> None:
    world.sync(US, UK, CANADA)
    contact_id = world.contact(world.lead(CA_WEB, "CA"), email="zelda.quill@acmelaw.com")

    decision = world.evaluate(contact_id)

    assert decision.kinds == (ReasonKind.COLD_EMAIL_NOT_ALLOWED,)
    assert texts(decision) == ["The Canada Market doesn't allow cold email"]


def test_a_stale_resolution_is_no_market(world: World) -> None:
    contact_id = world.us_contact()
    world.sql("UPDATE leads SET country_code = 'GB' WHERE id = ?", (world.lead_of(contact_id),))

    decision = world.evaluate(contact_id)

    assert decision.kinds == (ReasonKind.NO_ENABLED_MARKET,)
    assert texts(decision) == [NO_MARKET_STALE_TEXT]


def test_unreadable_markets_fail_closed(world: World) -> None:
    contact_id = world.us_contact()
    world.sql("UPDATE markets SET cctlds = 'not json' WHERE code = 'uk'")

    decision = world.evaluate(contact_id)

    assert decision.kinds == (ReasonKind.NO_ENABLED_MARKET,)
    assert texts(decision) == [NO_MARKET_RULES_TEXT]


def test_an_unreadable_office_fails_closed(world: World) -> None:
    office = Location("US", "NY")
    contact_id = world.contact(
        world.lead(US_WEB, "US"), email=US_EMAIL, name=NAME, office=office
    )
    assert world.evaluate(contact_id).eligible is True
    world.sql("UPDATE contacts SET office_region = 'ZZ' WHERE id = ?", (contact_id,))

    decision = world.evaluate(contact_id)

    assert decision.kinds == (ReasonKind.NO_ENABLED_MARKET,)
    assert texts(decision) == [NO_MARKET_OFFICE_TEXT]


def test_a_market_rule_edit_applies_at_once(world: World) -> None:
    contact_id = world.contact(world.lead(UK_WEB, "GB"), email="zelda.quill@gmail.com")

    before = world.evaluate(contact_id)
    world.sync(US, dataclasses.replace(UK, name="Britain"))
    after = world.evaluate(contact_id)

    assert before.kinds == after.kinds == (
        ReasonKind.UK_LEGAL_FORM_UNCONFIRMED,
        ReasonKind.UK_FREE_WEBMAIL,
    )
    assert texts(before)[1] == (
        "The address is on a free-webmail domain; the United Kingdom Market allows corporate "
        "addresses only"
    )
    assert "the Britain Market" in texts(after)[1]


# The address --------------------------------------------------------------------------------------


def test_an_address_that_cant_be_emailed_names_its_class(world: World) -> None:
    lead_id = world.lead(US_WEB, "US")
    guessed = world.contact(lead_id, ProvenanceSource.CSV, email=US_EMAIL)
    invalid = world.contact(lead_id, email="info@acmelaw.com")
    world.sql("UPDATE contacts SET address_class = 'invalid' WHERE id = ?", (invalid,))
    no_email = world.contact(lead_id, name=NAME)

    decisions = [world.evaluate(found) for found in (guessed, invalid, no_email)]

    assert [decision.kinds for decision in decisions] == [(ReasonKind.ADDRESS_NOT_SENDABLE,)] * 3
    assert [texts(decision) for decision in decisions] == [
        ["The address is Guessed; only Published or Verified addresses can be emailed"],
        ["The address is Invalid; only Published or Verified addresses can be emailed"],
        [NO_EMAIL_TEXT],
    ]


# Suppression --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "value", "kinds"),
    [
        ("address", US_EMAIL, (ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,)),
        ("domain", US_WEB, (ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,)),
        ("person", (NAME, US_WEB), (ReasonKind.ASKED_NOT_TO_BE_CONTACTED,)),
    ],
    ids=["address", "domain", "person"],
)
def test_an_active_entry_blocks_with_its_reason(
    world: World, kind: str, value: str | tuple[str, str], kinds: tuple[ReasonKind, ...]
) -> None:
    contact_id = world.us_contact()
    world.entry(kind, value)

    decision = world.evaluate(contact_id)

    assert decision.kinds == kinds


@pytest.mark.parametrize(
    ("kind", "value", "gate", "kinds"),
    [
        (  # only the Lead's web domain, not the address's
            "domain",
            US_WEB,
            BlockReason.LEAD_DOMAIN_SUPPRESSED,
            (ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,),
        ),
        (  # only the address's domain, not the Lead's
            "domain",
            "acme-mail.com",
            BlockReason.ADDRESS_DOMAIN_SUPPRESSED,
            (ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,),
        ),
        (
            "profile",
            LINKEDIN,
            BlockReason.PROFILE_SUPPRESSED,
            (ReasonKind.ASKED_NOT_TO_BE_CONTACTED,),
        ),
    ],
    ids=["lead domain", "address domain", "profile"],
)
def test_one_real_entry_among_distinct_keys_blocks_with_its_reason(
    world: World,
    kind: str,
    value: str,
    gate: BlockReason,
    kinds: tuple[ReasonKind, ...],
) -> None:
    contact_id = world.contact(
        world.lead(US_WEB, "US"), email=OTHER_MAIL, linkedin_url=LINKEDIN
    )
    assert world.evaluate(contact_id).eligible is True
    world.entry(kind, value)

    with world.units.open(read_only=True):
        answer = compliance_api.assert_contactable(ContactSubject(contact_id), "email", "cold")
    decision = world.evaluate(contact_id)

    assert answer == Blocked((gate,))  # the one key, told apart from the others
    assert decision.kinds == kinds


def test_an_opt_out_of_the_person_shows_both_suppression_reasons(world: World) -> None:
    contact_id = world.us_contact()

    world.suppress(contact_id)  # the address key and the person key
    decision = world.evaluate(contact_id)

    assert decision.kinds == (
        ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,
        ReasonKind.ASKED_NOT_TO_BE_CONTACTED,
    )
    assert texts(decision) == [SUPPRESSED_TEXT, ASKED_TEXT]


# Not Now and closed Leads -------------------------------------------------------------------------


def test_a_not_now_lead_with_no_date_fails_closed(world: World) -> None:
    contact_id = world.us_contact()
    world.set_stage(world.lead_of(contact_id), "not_now")

    decision = world.evaluate(contact_id)

    assert decision.kinds == (ReasonKind.NOT_NOW_UNDATED,)
    assert texts(decision) == [NOT_NOW_UNDATED_TEXT]


@pytest.mark.parametrize(("stage", "label"), [("lost", "Lost"), ("disqualified", "Disqualified")])
def test_a_closed_lead_gives_one_reason(world: World, stage: str, label: str) -> None:
    contact_id = world.us_contact()
    world.set_stage(world.lead_of(contact_id), stage)

    decision = world.evaluate(contact_id)

    assert decision.kinds == (ReasonKind.LEAD_CLOSED,)
    assert texts(decision) == [f"The Lead is closed ({label})"]


def test_several_reasons_are_listed_together_in_order(world: World) -> None:
    contact_id = world.contact(
        world.lead(UK_WEB, "GB"), ProvenanceSource.CSV, email=UK_EMAIL, name=NAME
    )
    world.suppress(contact_id)
    world.set_stage(world.lead_of(contact_id), "lost")

    decision = world.evaluate(contact_id)

    assert decision.kinds == (
        ReasonKind.UK_LEGAL_FORM_UNCONFIRMED,
        ReasonKind.ADDRESS_NOT_SENDABLE,
        ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,
        ReasonKind.ASKED_NOT_TO_BE_CONTACTED,
        ReasonKind.LEAD_CLOSED,
    )
    assert_no_personal_data(decision)


# Live, stored nowhere -----------------------------------------------------------------------------


def test_each_evaluation_reads_the_current_facts_and_stores_nothing(world: World) -> None:
    contact_id = world.us_contact()
    before = world.counts()

    first = world.evaluate(contact_id)
    assert world.counts() == before
    world.suppress(contact_id)
    after_suppression = world.counts()
    second = world.evaluate(contact_id)

    assert first.eligible is True
    assert second.eligible is False
    assert second != first
    assert world.counts() == after_suppression


def test_evaluation_writes_and_logs_nothing_read_only_or_in_a_transaction(
    world: World, statements: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    contact_id = world.us_contact()
    world.entry("person", (NAME, US_WEB))
    before = world.counts()
    statements.clear()
    caplog.set_level(logging.DEBUG)

    read_only = world.evaluate(contact_id)
    in_transaction = world.transact(lambda: world.compliance.evaluate_eligibility(contact_id))

    assert read_only == in_transaction
    assert read_only.kinds == (ReasonKind.ASKED_NOT_TO_BE_CONTACTED,)
    assert statements  # it read
    assert writes(statements) == []
    assert world.counts() == before
    assert caplog.records == []


def test_the_gate_is_called_once_on_email_for_cold_outreach(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    contact_id = world.us_contact()
    calls: list[tuple[Any, ...]] = []

    def gate(subject: Any, channel: Any, purpose: Any) -> Allowed | Blocked:
        calls.append((subject, channel, purpose))
        return Blocked((BlockReason.PROFILE_SUPPRESSED,))

    monkeypatch.setattr(compliance_api, "assert_contactable", gate)

    decision = world.evaluate(contact_id)

    assert calls == [(ContactSubject(contact_id), "email", "cold")]
    assert decision.kinds == (ReasonKind.ASKED_NOT_TO_BE_CONTACTED,)  # the fake's answer


def test_the_facts_not_yet_recorded_are_passed_as_absent(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    contact_id = world.us_contact()
    world.set_stage(world.lead_of(contact_id), "not_now")
    seen: list[tuple[Any, ...]] = []
    real = compliance_api.evaluate

    def recording(*arguments: Any) -> Decision:
        seen.append(arguments)
        return real(*arguments)

    monkeypatch.setattr(compliance_api, "evaluate", recording)

    world.evaluate(contact_id)

    [(contact, lead, rules, now)] = seen
    assert isinstance(contact, EligibilityContact)
    assert contact.history == ()
    assert contact.address_class == "published"
    assert contact.contactable == Allowed()
    assert isinstance(lead, EligibilityLead)
    assert lead == EligibilityLead(country_code="US", stage=PipelineStage.NOT_NOW)
    assert (lead.recontact_on, lead.legal_form, lead.legal_form_confirmed) == (None, None, False)
    assert lead.events_first_seen == ()
    assert sorted(market.code for market in rules.values()) == ["uk", "us"]
    assert now == LATER
    with pytest.raises(TypeError):
        rules["x"] = US  # a read-only mapping


# Bad input ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("contact_id", [UNKNOWN, "not-a-ulid", "", UNKNOWN.lower()])
def test_an_unknown_contact_raises_lookup_error(world: World, contact_id: str) -> None:
    with pytest.raises(LookupError) as raised:
        world.evaluate(contact_id)
    assert contact_id.upper() not in str(raised.value).upper() or not contact_id


def test_a_contact_on_a_merged_lead_raises_the_gates_value_error(world: World) -> None:
    contact_id = world.us_contact()
    survivor = world.lead("acme-group.com", "US", name="Acme Group")
    world.sql(
        "UPDATE leads SET merged_into = ? WHERE id = ?", (survivor, world.lead_of(contact_id))
    )

    with pytest.raises(ValueError, match="merged"):
        world.evaluate(contact_id)
