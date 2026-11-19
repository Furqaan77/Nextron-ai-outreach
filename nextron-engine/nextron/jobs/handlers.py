"""Thin job handlers (AD-10): each calls one use case and returns ``None``, or the domain's
``DEFERRED`` when the budget refused the use case's work (the runner re-queues the job an hour
later without using a retry).

Import rule: the domain and the standard library only. A handler gets what it calls from
``nextron.main``, which builds the job type registry (``build_job_types``), so this module
never imports ``nextron.app`` or anything below it; the runner opens the unit of work the
handler runs in.

- ``send_alert(alerts)`` (Story 1.12): the ``send_alert`` job's handler. ``alerts`` returns
  the ``notify`` service (``nextron.main`` passes ``lambda: notify``, which breaks the cycle
  registry, handler, ``Notify``, queue, registry); the handler calls
  ``alerts().send(job.payload["alert_id"])`` once and returns ``None``.
- ``backup_db(backups)`` (Story 1.16): the ``backup_db`` job's handler. ``backups`` returns the
  ``ops`` service's ``Backups`` (``nextron.main`` passes ``lambda: backups``, which breaks the
  cycle registry, handler, ``Backups``, queue, registry). The kind is
  ``job.payload.get("kind", "daily")`` (a trigger's tick payload has none); an unknown kind, or
  ``pre_migrate`` (only ``serve`` takes those), raises ``InvalidJob``. Otherwise it calls
  ``backups().back_up(kind)`` once and returns ``None``.
- ``restore_test(tests)`` (Story 1.18): the ``restore_test`` job's handler. ``tests`` returns
  the ``ops`` service's ``RestoreTests`` (``nextron.main`` passes ``lambda: restore_tests``,
  which breaks the cycle registry, handler, ``RestoreTests``, queue, registry). The kind is
  ``job.payload.get("kind", "monthly")`` (a trigger's tick payload has none: the daily check of
  the monthly test); an unknown kind raises ``InvalidJob``. Otherwise it calls
  ``tests().run(kind)`` once and returns ``None`` (a monthly test that isn't due returns
  without a row).
- ``check_recovery(checker)`` (Story 1.19): the ``check_recovery`` job's handler. ``checker``
  returns the job layer's ``RecoveryRunner`` (``nextron.jobs.recovery``; ``nextron.main``
  passes ``lambda: recovery_runner``). It calls ``checker().check()`` once, which runs the
  pending recovery steps and ends recovery mode when they are all settled, and returns
  ``None``. The payload is a trigger's tick, ``{"v": 1}``.
- ``resolve_contact_markets(markets)`` (Story 3.2): the one-off ``resolve_contact_markets``
  job's handler. ``markets`` returns the ``research`` service's ``ContactMarkets``
  (``nextron.main`` passes ``lambda: contact_markets``, which breaks the cycle registry,
  handler, ``ContactMarkets``, queue, registry); the handler calls ``markets().backfill()``
  once and returns ``None``. The payload (``{"v": 1}``) holds nothing else.
- ``enrich_lead(crawls)`` (Story 2.5): calls ``crawls().enrich_lead(lead_id, may_retry=
  job.attempts <= ENRICH_LEAD_RETRIES)`` once (the research service's ``WebsiteCrawls``), so the
  job's last allowed attempt completes instead of failing on a flaky site, and returns
  ``DEFERRED`` when it returns ``False`` (the budget refused the crawl).
- ``canonicalize_lead(discovery)`` (Story 2.5): calls ``discovery().canonicalize_lead(lead_id,
  run_id)`` once.
- ``purge_page_text(research)`` (Story 2.5): calls ``research().purge_page_text()`` once.
- ``purge_place_coords(discovery)`` (Story 8.2): calls ``discovery().purge_place_coords()``
  once.
- ``render_pages(crawls)`` (Story 2.6): calls ``crawls().render_pages(lead_id, run_id)`` once
  and returns ``DEFERRED`` when it returns ``False`` (the budget refused the renders).
- ``extract_lead(extractions)`` (Story 2.9): calls ``extractions().extract_lead(lead_id,
  run_id)`` once (the research service's ``SignalExtractions``) and returns ``DEFERRED`` when it
  returns ``False`` (the budget refused the call). A retryable ``AdapterError`` it raises is
  retried by the runner; any other error fails the job.
- ``select_primary_contact(primary)`` (Story 2.10): calls
  ``primary().select_primary_contact(lead_id)`` once (the research service's
  ``PrimaryContacts``) and returns ``None``.
- ``run_ai_test_sets(runs)`` (Story 4.5): calls ``runs().run(tuple_id, test_set)`` once (the
  ``playbook`` service's ``AiTestRuns``; ``nextron.main`` passes ``lambda: test_runs``), and
  returns ``DEFERRED`` when it returns ``False`` (the budget refused the run). ``test_set`` must
  be the value of a ``nextron.domain.ai_test_gate.TestSet``, else ``InvalidJob``.
- ``check_domains(checks)`` (Story 6.5): ``checks`` returns the ``sending`` service's
  ``DomainChecks`` (``nextron.main`` passes ``lambda: domain_checks``). A payload holding
  ``sending_domain_id`` (a per-domain job: a Recheck, the daily fan-out or, from Story 6.2, a
  connect) calls ``checks().check_domain(sending_domain_id)`` once; a trigger's tick payload
  (``{"v": 1}``) calls ``checks().queue_daily()`` once, which queues one per-domain job per
  listed Sending Domain. A retryable ``AdapterError`` (a DNS timeout) is retried by the runner;
  any other error fails the job, and nothing is recorded either way.
- ``check_job_boards(job_boards)`` (Story 2.11): calls ``job_boards().check_job_boards(lead_id,
  run_id)`` once (the research service's ``JobBoards``) and returns ``DEFERRED`` when it
  returns ``False`` (the budget refused the check).
- ``recheck_event(job_boards)`` (Story 2.11): calls ``job_boards().recheck_event(signal_id)``
  once and returns ``None``; a retryable ``AdapterError`` it raises is retried by the runner.
- ``check_legal_form(legal_forms)`` (Story 2.12): calls
  ``legal_forms().check_legal_form(lead_id)`` once (the discovery service's
  ``LegalFormChecks``) and returns ``DEFERRED`` when it returns ``False`` (the budget refused
  the work). A retryable ``AdapterError`` it raises is retried by the runner.
- ``erase_external(stores)`` (Story 3.14): the ``erase_external`` job's handler. ``stores``
  returns the ``compliance`` service's ``ErasureStores`` (``nextron.main`` passes ``lambda:
  erasure_stores``). The payload's ``contact_id`` must be a ULID and its ``store`` one of
  ``EXTERNAL_STORES`` (else ``InvalidJob``); it calls ``stores().run(contact_id, store,
  last_attempt=job.attempts > ERASE_EXTERNAL_RETRIES)`` once, so the last allowed attempt
  settles the store ``failed`` instead of leaving it ``pending``, and returns ``None``.
- ``find_contact_email(finding)`` (Story 2.13): calls ``finding().find_contact_email(lead_id,
  contact_id)`` once (the research service's ``EmailFinding``) and returns ``DEFERRED`` when it
  returns ``quota_reached`` (a Hunter or Prospeo quota refused the paid step). A retryable
  ``AdapterError`` it raises is retried by the runner; any other error fails the job.
- ``verify_contact_email(verification)`` (Story 2.14): calls
  ``verification().verify_contact_email(lead_id, contact_id)`` once (the research service's
  ``EmailVerification``) and returns ``DEFERRED`` when it returns ``quota_reached`` (Hunter's
  quota refused the verification). A retryable ``AdapterError`` it raises (a DNS timeout,
  Hunter unavailable) is retried by the runner; any other error fails the job.
- ``score_lead(scores)`` (Story 2.15): calls ``scores().score_lead(lead_id)`` once (the
  ``scoring`` service's ``LeadScores``) and returns ``None``; a payload whose ``use_current``
  is ``true`` (Story 11.5) calls ``score_lead(lead_id, use_current=True)`` instead, and a
  ``use_current`` that isn't a ``bool`` raises ``InvalidJob``.
- ``recompute_tiers(scores)`` (Story 2.15): calls ``scores().recompute_tiers()`` once and returns
  ``None``; the payload is a trigger's tick, ``{"v": 1}``.
- ``poll_unsubscribes(polls)`` (Story 3.11): calls ``polls().poll()`` once (the ``compliance``
  service's ``UnsubscribePolls``; ``nextron.main`` passes ``lambda: unsubscribes``) and returns
  ``None``. The payload is a trigger's tick, ``{"v": 1}``. It is never budget-gated. A
  retryable ``AdapterError`` (the Worker unavailable) is retried by the runner; any other error
  fails the job, and nothing is applied either way.
- ``discover_places(place_discovery)`` (Story 8.3): calls
  ``place_discovery().run(campaign_id, location, country_code)`` once (the discovery service's
  ``PlaceDiscovery``; ``location`` is the location's 16-hex hash and ``country_code`` two
  lowercase letters, else ``InvalidJob``) and returns ``DEFERRED`` when it returns ``False``
  (the budget refused the run before its row was written, or a Places page: Story 8.1).
- ``process_signal_item(inbox)`` (Story 2.16): calls ``inbox().process_signal_item(item_id,
  last_attempt=job.attempts > PROCESS_SIGNAL_ITEM_RETRIES)`` once (the ``discovery`` service's
  ``SignalInbox``) and returns ``DEFERRED`` when it returns ``False`` (the budget refused the
  parse). A retryable ``AdapterError`` it raises is retried by the runner; on the last allowed
  attempt the use case flags the item ``parse_failed`` instead, so no item stays ``pending``.
- ``check_grounding(checks)`` (Story 4.14): ``checks`` returns the ``drafting`` service's
  ``GroundingChecks`` (``nextron.main`` passes ``lambda: grounding_checks``). The payload's
  ``draft_id`` must be a ULID and its ``rev`` a positive ``int`` (not a ``bool``), else
  ``InvalidJob``; it calls ``checks().check(draft_id, rev, urgency)`` once, with ``urgency``
  ``bulk`` for a job on the bulk lane and ``interactive`` otherwise, and returns ``None``. It is
  never budget-gated (the grounding routes are ``essential``), and a timeout or an unreachable
  API is recorded as a grounding ``fail`` by the use case, not retried.
- ``discover_registry(registry_discovery)`` (Story 8.4): calls
  ``registry_discovery().run(campaign_id)`` once (the discovery service's
  ``RegistryDiscovery``) and returns ``DEFERRED`` when it returns ``False`` (the budget refused
  the run before its row was written, or the budget or the register's rate limit deferred the
  run: Story 8.1).
- **Scheduled discovery (Story 8.10).** A ``discover_places`` or ``discover_registry`` payload
  holding ``scheduled_for`` (the weekday top-up's ``{v: 1, campaign_id, scheduled_for}``) must
  hold a ``YYYY-MM-DD`` date there (else ``InvalidJob``) and calls
  ``run_scheduled(campaign_id, <that date>)`` instead, once; ``False`` returns ``DEFERRED``.

**Budget classes (Story 8.1).** Every handler that may return ``DEFERRED`` belongs to a type
``nextron.main.build_job_types`` registers ``blockable`` (``enrich_lead``, ``render_pages``,
``extract_lead``, ``check_legal_form``, ``run_ai_test_sets``, ``check_job_boards``,
``discover_places``, ``discover_registry``; ``recheck_event`` is ``blockable`` too, as Re-check
work). Every other type is ``essential``: its handler never returns ``DEFERRED``.

**The chain's lane (Story 2.16).** ``enrich_lead``, ``extract_lead``, ``check_job_boards``,
``check_legal_form`` and ``select_primary_contact`` pass ``lane=job.lane`` to their use case,
which enqueues the chain's next stage on that lane (a Signal Inbox chain runs on the interactive
lane, a CSV import's on the bulk lane, as before). ``render_pages`` runs on the bulk lane only;
it passes its payload's optional ``chain_lane`` (``CHAIN_LANE_FIELD``; a lane name, else
``InvalidJob``) as ``chain_lane=``, so the ``extract_lead`` it enqueues goes back to that lane.

A payload field that is missing or isn't a ULID raises ``InvalidJob`` naming the field.
"""

from collections.abc import Callable
from datetime import date
from typing import Final, Protocol

from nextron.domain.ai import Urgency
from nextron.domain.ai_test_gate import TEST_SET_FIELD, TUPLE_ID_FIELD, set_of
from nextron.domain.backup import JOB_KINDS, KIND_FIELD, BackupKind
from nextron.domain.crawl import CHAIN_LANE_FIELD, LEAD_ID_FIELD, RUN_ID_FIELD
from nextron.domain.discovery_topup import SCHEDULED_FOR_FIELD
from nextron.domain.domain_auth import SENDING_DOMAIN_ID_FIELD
from nextron.domain.email_checks import CheckResult
from nextron.domain.email_finding import EmailSearchOutcome
from nextron.domain.erasure_receipt import CONTACT_ID_FIELD, EXTERNAL_STORES, STORE_FIELD
from nextron.domain.grounding_check import DRAFT_ID_FIELD, REV_FIELD
from nextron.domain.postings import SIGNAL_ID_FIELD
from nextron.domain.ids import is_ulid
from nextron.domain.job import DEFERRED, Handler, HandlerResult, InvalidJob, Job, Lane, is_iso_date
from nextron.domain.place_discovery import (
    CAMPAIGN_ID_FIELD,
    COUNTRY_CODE_FIELD,
    HASH_LENGTH,
    LOCATION_FIELD,
)
from nextron.domain.restore_test import RestoreTestKind
from nextron.domain.scoring import USE_CURRENT_FIELD
from nextron.domain.signal_inbox_item import ITEM_ID_FIELD

ALERT_ID_FIELD: Final = "alert_id"
ENRICH_LEAD_RETRIES: Final = 3  # the enrich_lead job type's retry limit (nextron.main)
ERASE_EXTERNAL_RETRIES: Final = 5  # the erase_external job type's retry limit (nextron.main)
PROCESS_SIGNAL_ITEM_RETRIES: Final = 3  # the process_signal_item job type's (nextron.main)


def _chain_lane(job: Job) -> Lane | None:
    """``render_pages``' optional ``chain_lane`` (Story 2.16): a lane name, else ``InvalidJob``."""
    value = job.payload.get(CHAIN_LANE_FIELD)
    if value is None:
        return None
    lane = next((lane for lane in Lane if lane.value == value), None)
    if lane is None:
        raise InvalidJob(f"{job.type}: the {CHAIN_LANE_FIELD} field must be a lane")
    return lane


def _ulid_field(job: Job, name: str) -> str:
    value = job.payload.get(name)
    if not isinstance(value, str) or not is_ulid(value):
        raise InvalidJob(f"{job.type}: the {name} field must be a ULID")
    return value


class AlertSending(Protocol):
    """What ``send_alert`` needs of the ``notify`` service."""

    def send(self, alert_id: str) -> None: ...


def send_alert(alerts: Callable[[], AlertSending]) -> Handler:
    """The ``send_alert`` job's handler: send the payload's alert through ``alerts()``."""

    def handle_send_alert(job: Job) -> None:
        alerts().send(str(job.payload[ALERT_ID_FIELD]))

    return handle_send_alert


class BackingUp(Protocol):
    """What ``backup_db`` needs of the ``ops`` service's ``Backups``."""

    def back_up(self, kind: BackupKind) -> str: ...


def backup_db(backups: Callable[[], BackingUp]) -> Handler:
    """The ``backup_db`` job's handler: take the payload's daily or manual backup through
    ``backups()``."""

    def handle_backup_db(job: Job) -> None:
        value = job.payload.get(KIND_FIELD, str(BackupKind.DAILY))
        kind = next((kind for kind in JOB_KINDS if kind.value == value), None)
        if kind is None:
            raise InvalidJob("backup_db: the kind field must be daily or manual")
        backups().back_up(kind)

    return handle_backup_db


class RestoreTesting(Protocol):
    """What ``restore_test`` needs of the ``ops`` service's ``RestoreTests``."""

    def run(self, kind: RestoreTestKind) -> str | None: ...


def restore_test(tests: Callable[[], RestoreTesting]) -> Handler:
    """The ``restore_test`` job's handler: run the payload's monthly or manual restore test
    through ``tests()``."""

    def handle_restore_test(job: Job) -> None:
        value = job.payload.get(KIND_FIELD, str(RestoreTestKind.MONTHLY))
        kind = next((kind for kind in RestoreTestKind if kind.value == value), None)
        if kind is None:
            raise InvalidJob("restore_test: the kind field must be monthly or manual")
        tests().run(kind)

    return handle_restore_test


class RecoveryChecking(Protocol):
    """What ``check_recovery`` needs of the ``RecoveryRunner``."""

    def check(self) -> bool: ...


def check_recovery(checker: Callable[[], RecoveryChecking]) -> Handler:
    """The ``check_recovery`` job's handler: run the pending recovery steps through
    ``checker()``."""

    def handle_check_recovery(job: Job) -> None:
        checker().check()

    return handle_check_recovery


class MarketBackfilling(Protocol):
    """What ``resolve_contact_markets`` needs of the ``research`` service's ``ContactMarkets``."""

    def backfill(self) -> int: ...


def resolve_contact_markets(markets: Callable[[], MarketBackfilling]) -> Handler:
    """The ``resolve_contact_markets`` job's handler: resolve the Contacts stored before Story
    3.2 through ``markets()``."""

    def handle_resolve_contact_markets(job: Job) -> None:
        del job  # the payload holds only its version
        markets().backfill()

    return handle_resolve_contact_markets


class Crawling(Protocol):
    """What ``enrich_lead`` and ``render_pages`` need of the ``research`` service's
    ``WebsiteCrawls``."""

    def enrich_lead(self, lead_id: str, *, may_retry: bool, lane: Lane | None = None) -> bool: ...

    def render_pages(self, lead_id: str, run_id: str, *, chain_lane: Lane | None = None) -> bool:
        ...


def enrich_lead(crawls: Callable[[], Crawling]) -> Handler:
    """The ``enrich_lead`` job's handler: crawl the payload's Lead through ``crawls()``."""

    def handle_enrich_lead(job: Job) -> HandlerResult | None:
        lead_id = _ulid_field(job, LEAD_ID_FIELD)
        may_retry = job.attempts <= ENRICH_LEAD_RETRIES
        if not crawls().enrich_lead(lead_id, may_retry=may_retry, lane=job.lane):
            return DEFERRED
        return None

    return handle_enrich_lead


def render_pages(crawls: Callable[[], Crawling]) -> Handler:
    """The ``render_pages`` job's handler: render the payload's run's near-empty pages through
    ``crawls()``."""

    def handle_render_pages(job: Job) -> HandlerResult | None:
        lead_id = _ulid_field(job, LEAD_ID_FIELD)
        run_id = _ulid_field(job, RUN_ID_FIELD)
        if not crawls().render_pages(lead_id, run_id, chain_lane=_chain_lane(job)):
            return DEFERRED
        return None

    return handle_render_pages


class Canonicalizing(Protocol):
    """What ``canonicalize_lead`` needs of the ``discovery`` service."""

    def canonicalize_lead(self, lead_id: str, run_id: str) -> str: ...


def canonicalize_lead(discovery: Callable[[], Canonicalizing]) -> Handler:
    """The ``canonicalize_lead`` job's handler: move the payload's Lead to its run's domain
    through ``discovery()``."""

    def handle_canonicalize_lead(job: Job) -> None:
        lead_id = _ulid_field(job, LEAD_ID_FIELD)
        run_id = _ulid_field(job, RUN_ID_FIELD)
        discovery().canonicalize_lead(lead_id, run_id)

    return handle_canonicalize_lead


class PurgingPageText(Protocol):
    """What ``purge_page_text`` needs of the ``research`` service."""

    def purge_page_text(self) -> int: ...


def purge_page_text(research: Callable[[], PurgingPageText]) -> Handler:
    """The ``purge_page_text`` job's handler: null old page text through ``research()``."""

    def handle_purge_page_text(job: Job) -> None:
        research().purge_page_text()

    return handle_purge_page_text


class PurgingPlaceCoords(Protocol):
    """What ``purge_place_coords`` needs of the ``discovery`` service."""

    def purge_place_coords(self) -> int: ...


class DiscoveryJobs(Canonicalizing, PurgingPlaceCoords, Protocol):
    """What the ``discovery`` service's jobs need of it."""


def purge_place_coords(discovery: Callable[[], PurgingPlaceCoords]) -> Handler:
    """The ``purge_place_coords`` job's handler: null old Places coordinates through
    ``discovery()``."""

    def handle_purge_place_coords(job: Job) -> None:
        discovery().purge_place_coords()

    return handle_purge_place_coords


class Extracting(Protocol):
    """What ``extract_lead`` needs of the ``research`` service's ``SignalExtractions``."""

    def extract_lead(self, lead_id: str, run_id: str, *, lane: Lane | None = None) -> bool: ...


class SelectingPrimary(Protocol):
    """What ``select_primary_contact`` needs of the ``research`` service's ``PrimaryContacts``."""

    def select_primary_contact(self, lead_id: str, *, lane: Lane | None = None) -> object: ...


def select_primary_contact(primary: Callable[[], SelectingPrimary]) -> Handler:
    """The ``select_primary_contact`` job's handler: pick the payload's Lead's Primary Contact
    through ``primary()``."""

    def handle_select_primary_contact(job: Job) -> None:
        lead_id = _ulid_field(job, LEAD_ID_FIELD)
        primary().select_primary_contact(lead_id, lane=job.lane)

    return handle_select_primary_contact


class CheckingLegalForm(Protocol):
    """What ``check_legal_form`` needs of the ``discovery`` service's ``LegalFormChecks``."""

    def check_legal_form(self, lead_id: str, *, lane: Lane | None = None) -> bool: ...


def check_legal_form(legal_forms: Callable[[], CheckingLegalForm]) -> Handler:
    """The ``check_legal_form`` job's handler: confirm the payload's Lead's legal form through
    ``legal_forms()``."""

    def handle_check_legal_form(job: Job) -> HandlerResult | None:
        lead_id = _ulid_field(job, LEAD_ID_FIELD)
        if not legal_forms().check_legal_form(lead_id, lane=job.lane):
            return DEFERRED
        return None

    return handle_check_legal_form


class FindingEmail(Protocol):
    """What ``find_contact_email`` needs of the ``research`` service's ``EmailFinding``."""

    def find_contact_email(self, lead_id: str, contact_id: str) -> object: ...


def find_contact_email(finding: Callable[[], FindingEmail]) -> Handler:
    """The ``find_contact_email`` job's handler: find the payload's Contact's email address
    through ``finding()``."""

    def handle_find_contact_email(job: Job) -> HandlerResult | None:
        lead_id = _ulid_field(job, LEAD_ID_FIELD)
        contact_id = _ulid_field(job, CONTACT_ID_FIELD)
        if finding().find_contact_email(lead_id, contact_id) == EmailSearchOutcome.QUOTA_REACHED:
            return DEFERRED
        return None

    return handle_find_contact_email


class VerifyingEmail(Protocol):
    """What ``verify_contact_email`` needs of the ``research`` service's
    ``EmailVerification``."""

    def verify_contact_email(self, lead_id: str, contact_id: str) -> object: ...


def verify_contact_email(verification: Callable[[], VerifyingEmail]) -> Handler:
    """The ``verify_contact_email`` job's handler: check the payload's Contact's address
    through ``verification()``."""

    def handle_verify_contact_email(job: Job) -> HandlerResult | None:
        lead_id = _ulid_field(job, LEAD_ID_FIELD)
        contact_id = _ulid_field(job, CONTACT_ID_FIELD)
        result = verification().verify_contact_email(lead_id, contact_id)
        if result == CheckResult.QUOTA_REACHED:
            return DEFERRED
        return None

    return handle_verify_contact_email


def extract_lead(extractions: Callable[[], Extracting]) -> Handler:
    """The ``extract_lead`` job's handler: extract the payload's Lead's Signals through
    ``extractions()``."""

    def handle_extract_lead(job: Job) -> HandlerResult | None:
        lead_id = _ulid_field(job, LEAD_ID_FIELD)
        run_id = _ulid_field(job, RUN_ID_FIELD)
        if not extractions().extract_lead(lead_id, run_id, lane=job.lane):
            return DEFERRED
        return None

    return handle_extract_lead


class AiTestRunning(Protocol):
    """What ``run_ai_test_sets`` needs of the ``playbook`` service's ``AiTestRuns``."""

    def run(self, tuple_id: str, test_set: str, *, activate: bool = True) -> bool: ...


def run_ai_test_sets(runs: Callable[[], AiTestRunning]) -> Handler:
    """The ``run_ai_test_sets`` job's handler: run the payload's tuple on its AI Test Set
    through ``runs()``."""

    def handle_run_ai_test_sets(job: Job) -> HandlerResult | None:
        tuple_id = _ulid_field(job, TUPLE_ID_FIELD)
        test_set = job.payload.get(TEST_SET_FIELD)
        if not isinstance(test_set, str) or set_of(test_set) is None:
            raise InvalidJob(f"{job.type}: the {TEST_SET_FIELD} field must be an AI Test Set")
        if not runs().run(tuple_id, test_set):
            return DEFERRED
        return None

    return handle_run_ai_test_sets


class DomainChecking(Protocol):
    """What ``check_domains`` needs of the ``sending`` service's ``DomainChecks``."""

    def check_domain(self, sending_domain_id: str) -> object: ...

    def queue_daily(self) -> int: ...


def check_domains(checks: Callable[[], DomainChecking]) -> Handler:
    """The ``check_domains`` job's handler: check the payload's Sending Domain, or queue the
    daily checks for a trigger's tick, through ``checks()``."""

    def handle_check_domains(job: Job) -> None:
        if SENDING_DOMAIN_ID_FIELD in job.payload:
            checks().check_domain(_ulid_field(job, SENDING_DOMAIN_ID_FIELD))
        else:
            checks().queue_daily()

    return handle_check_domains


class CheckingJobBoards(Protocol):
    """What ``check_job_boards`` and ``recheck_event`` need of the ``research`` service's
    ``JobBoards``."""

    def check_job_boards(self, lead_id: str, run_id: str, *, lane: Lane | None = None) -> bool:
        ...

    def recheck_event(self, signal_id: str) -> None: ...


def check_job_boards(job_boards: Callable[[], CheckingJobBoards]) -> Handler:
    """The ``check_job_boards`` job's handler: check the payload's Lead's ATS boards through
    ``job_boards()``."""

    def handle_check_job_boards(job: Job) -> HandlerResult | None:
        lead_id = _ulid_field(job, LEAD_ID_FIELD)
        run_id = _ulid_field(job, RUN_ID_FIELD)
        if not job_boards().check_job_boards(lead_id, run_id, lane=job.lane):
            return DEFERRED
        return None

    return handle_check_job_boards


def recheck_event(job_boards: Callable[[], CheckingJobBoards]) -> Handler:
    """The ``recheck_event`` job's handler: re-check the payload's Event's posting through
    ``job_boards()``."""

    def handle_recheck_event(job: Job) -> None:
        signal_id = _ulid_field(job, SIGNAL_ID_FIELD)
        job_boards().recheck_event(signal_id)

    return handle_recheck_event


class StoreErasing(Protocol):
    """What ``erase_external`` needs of the ``compliance`` service's ``ErasureStores``."""

    def run(self, contact_id: str, store: str, *, last_attempt: bool) -> object: ...


def erase_external(stores: Callable[[], StoreErasing]) -> Handler:
    """The ``erase_external`` job's handler: settle the payload's store of the payload's
    Contact through ``stores()``."""

    def handle_erase_external(job: Job) -> None:
        contact_id = _ulid_field(job, CONTACT_ID_FIELD)
        store = job.payload.get(STORE_FIELD)
        if not isinstance(store, str) or store not in {str(found) for found in EXTERNAL_STORES}:
            raise InvalidJob(f"{job.type}: the {STORE_FIELD} field must be an external store")
        stores().run(contact_id, store, last_attempt=job.attempts > ERASE_EXTERNAL_RETRIES)

    return handle_erase_external


class ScoringLeads(Protocol):
    """What ``score_lead`` and ``recompute_tiers`` need of the ``scoring`` service's
    ``LeadScores``."""

    def score_lead(self, lead_id: str, *, use_current: bool = False) -> object: ...

    def recompute_tiers(self) -> object: ...


def score_lead(scores: Callable[[], ScoringLeads]) -> Handler:
    """The ``score_lead`` job's handler: score the payload's Lead through ``scores()``, with
    the current Segment definition when the payload says ``use_current``."""

    def handle_score_lead(job: Job) -> None:
        lead_id = _ulid_field(job, LEAD_ID_FIELD)
        use_current = job.payload.get(USE_CURRENT_FIELD, False)
        if not isinstance(use_current, bool):
            raise InvalidJob(f"{job.type}: the {USE_CURRENT_FIELD} field must be true or false")
        if use_current:
            scores().score_lead(lead_id, use_current=True)
        else:
            scores().score_lead(lead_id)

    return handle_score_lead


def recompute_tiers(scores: Callable[[], ScoringLeads]) -> Handler:
    """The ``recompute_tiers`` job's handler: re-tier every scored Lead through ``scores()``."""

    def handle_recompute_tiers(job: Job) -> None:
        scores().recompute_tiers()

    return handle_recompute_tiers


class PollingUnsubscribes(Protocol):
    """What ``poll_unsubscribes`` needs of the ``compliance`` service's ``UnsubscribePolls``."""

    def poll(self) -> object: ...


def poll_unsubscribes(polls: Callable[[], PollingUnsubscribes]) -> Handler:
    """The ``poll_unsubscribes`` job's handler: poll the unsubscribe Worker through
    ``polls()``."""

    def handle_poll_unsubscribes(job: Job) -> None:
        polls().poll()

    return handle_poll_unsubscribes


class DiscoveringPlaces(Protocol):
    """What ``discover_places`` needs of the ``discovery`` service's ``PlaceDiscovery``."""

    def run(self, campaign_id: str, location_hash: str, country_code: str) -> bool: ...

    def run_scheduled(self, campaign_id: str, scheduled_for: date) -> bool: ...


_LOWER_HEX: Final = frozenset("0123456789abcdef")


def _scheduled_for(job: Job) -> date | None:
    """A scheduled discovery job's date (Story 8.10), ``None`` for a manual job's payload."""
    if SCHEDULED_FOR_FIELD not in job.payload:
        return None
    value = job.payload[SCHEDULED_FOR_FIELD]
    if not (isinstance(value, str) and is_iso_date(value)):
        raise InvalidJob(f"{job.type}: the {SCHEDULED_FOR_FIELD} field must be a YYYY-MM-DD date")
    return date.fromisoformat(value)


def discover_places(place_discovery: Callable[[], DiscoveringPlaces]) -> Handler:
    """The ``discover_places`` job's handler: run the payload's Campaign's Places discovery in
    its location through ``place_discovery()``."""

    def handle_discover_places(job: Job) -> HandlerResult | None:
        campaign_id = _ulid_field(job, CAMPAIGN_ID_FIELD)
        scheduled_for = _scheduled_for(job)
        if scheduled_for is not None:
            found = place_discovery().run_scheduled(campaign_id, scheduled_for)
            return None if found else DEFERRED
        location = job.payload.get(LOCATION_FIELD)
        if not (
            isinstance(location, str)
            and len(location) == HASH_LENGTH
            and set(location) <= _LOWER_HEX
        ):
            raise InvalidJob(f"{job.type}: the {LOCATION_FIELD} field must be a location hash")
        code = job.payload.get(COUNTRY_CODE_FIELD)
        if not (
            isinstance(code, str)
            and len(code) == 2
            and code.isascii()
            and code.isalpha()
            and code.islower()
        ):
            raise InvalidJob(f"{job.type}: the {COUNTRY_CODE_FIELD} field must be a country code")
        if not place_discovery().run(campaign_id, location, code):
            return DEFERRED
        return None

    return handle_discover_places


class ProcessingSignalItems(Protocol):
    """What ``process_signal_item`` needs of the ``discovery`` service's ``SignalInbox``."""

    def process_signal_item(self, item_id: str, *, last_attempt: bool = False) -> bool: ...


def process_signal_item(inbox: Callable[[], ProcessingSignalItems]) -> Handler:
    """The ``process_signal_item`` job's handler: parse and apply the payload's Signal Inbox item
    through ``inbox()``."""

    def handle_process_signal_item(job: Job) -> HandlerResult | None:
        item_id = _ulid_field(job, ITEM_ID_FIELD)
        last_attempt = job.attempts > PROCESS_SIGNAL_ITEM_RETRIES
        if not inbox().process_signal_item(item_id, last_attempt=last_attempt):
            return DEFERRED
        return None

    return handle_process_signal_item


class CheckingGrounding(Protocol):
    """What ``check_grounding`` needs of the ``drafting`` service's ``GroundingChecks``."""

    def check(self, draft_id: str, rev: int, urgency: Urgency) -> object: ...


def check_grounding(checks: Callable[[], CheckingGrounding]) -> Handler:
    """The ``check_grounding`` job's handler: run the AI grounding check of the payload's Draft
    revision through ``checks()``."""

    def handle_check_grounding(job: Job) -> None:
        draft_id = _ulid_field(job, DRAFT_ID_FIELD)
        rev = job.payload.get(REV_FIELD)
        if not isinstance(rev, int) or isinstance(rev, bool) or rev < 1:
            raise InvalidJob(f"{job.type}: the {REV_FIELD} field must be a positive int")
        urgency = Urgency.BULK if job.lane == Lane.BULK else Urgency.INTERACTIVE
        checks().check(draft_id, rev, urgency)

    return handle_check_grounding


class DiscoveringRegistry(Protocol):
    """What ``discover_registry`` needs of the ``discovery`` service's ``RegistryDiscovery``."""

    def run(self, campaign_id: str) -> bool: ...

    def run_scheduled(self, campaign_id: str, scheduled_for: date) -> bool: ...


def discover_registry(registry_discovery: Callable[[], DiscoveringRegistry]) -> Handler:
    """The ``discover_registry`` job's handler (Story 8.4): run the payload's Campaign's
    Companies House discovery through ``registry_discovery()``."""

    def handle_discover_registry(job: Job) -> HandlerResult | None:
        campaign_id = _ulid_field(job, CAMPAIGN_ID_FIELD)
        scheduled_for = _scheduled_for(job)
        if scheduled_for is not None:
            found = registry_discovery().run_scheduled(campaign_id, scheduled_for)
            return None if found else DEFERRED
        if not registry_discovery().run(campaign_id):
            return DEFERRED
        return None

    return handle_discover_registry
