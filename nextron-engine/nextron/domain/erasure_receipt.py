"""Erasure receipts (FR-52, AD-10, AD-15, AD-17, NFR-3, NFR-17, Story 3.14). Standard library
only, plus ``nextron.domain``.

An erasure (Story 3.13) tombstones a Contact inside the database. AD-17 says it must also reach
every store outside it that may hold the Contact's data, and produce a receipt that tells the
requester what was erased and what remains. A receipt is one row per erasure (or repeat erase
request) with one row per *store*: the in-database items worked out in the erasure's
transaction, and the external stores, each settled later by its own ``erase_external`` job
(``ERASE_EXTERNAL_JOB``).

**Stores** (``ErasureStore``, in display order), each with a ``label`` and an ``action`` (fixed
texts):

- in the database: ``database``, ``suppression``, ``evidence_quotes``, ``page_addresses`` and
  ``other_contacts`` (``DATABASE_STORES``);
- the AD-17 stores: ``mailboxes``, ``crawled_pages``, ``telegram``, ``anthropic``,
  ``worker_kv``, ``backups``, ``ai_test_sets`` and ``nextronai_inbox`` (``AD17_STORES``);
- the engine's own files: ``logs``, ``sink_files``, ``restore_work`` and ``erasure_ledger``
  (``FILE_STORES``).

``EXTERNAL_STORES`` is the twelve AD-17 and file stores, each settled by a job.
``STORE_NUMBERS`` gives each of them a fixed number from 1 to 12, the discriminator of its job's
idempotency key (``erase_external:<contact_id>:<n>``); a number is never reused.

**Statuses** (``StoreStatus``): ``pending`` (before a store's job runs), then one terminal
status: ``done``, ``residual``, ``none_held`` or ``failed``. ``transition(current, to)`` follows
``TRANSITIONS`` (read-only): ``pending`` goes to each terminal status with cause ``settled``;
anything else raises ``IllegalTransition``.

**Outcomes** (``ReceiptOutcome``): ``erased`` (this request erased the Contact) or
``already_erased`` (a repeat request on a Contact erased before).

**The receipt's state** (``receipt_state(statuses) -> ReceiptReading``): ``incomplete`` when any
status is ``failed`` ("Incomplete: N failed"), else ``in_progress`` when any is ``pending``
("In progress"), else ``complete`` ("Complete").

**``StoreResult(status, detail)``**: a store's status and its detail, a stripped text of 1 to
1000 characters with no ``@`` and no control character (``ValueError`` otherwise; the message
never echoes the value). Every detail is built here from fixed texts holding counts, dates
(``YYYY-MM-DD``), ULIDs and codes only.

**Matching a name.** ``mentions_name(name, text)``: every word of the folded name (NFKC,
casefold, every run of characters that aren't letters, digits or combining marks to one space,
NFKC again, as ``identity.normalize_name`` folds, with no length limit) is a whole word of the
folded text. ``url_mentions_name(name, url)``: the same test on the words of the URL's
percent-decoded path, or the name's words joined with nothing are one path word
(``/people/janedoe``). A name with no word never matches; a URL that can't be split never
matches.

**Time rules.** ``is_recent_message(sent_at, now)``: whether a Telegram message sent at
``sent_at`` is under 48 hours old at ``now`` (``deleteMessage`` refuses older ones).
``age_out(started_at, days)``: the date a backup copy taken at ``started_at`` ages out of a
``days``-day rotation.
"""

import unicodedata
import urllib.parse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.erasure import ErasureCause
from nextron.domain.machines import IllegalTransition, Transition

MACHINE: Final = "erasure_receipt_store"
ERASE_EXTERNAL_JOB: Final = "erase_external"
CONTACT_ID_FIELD: Final = "contact_id"
STORE_FIELD: Final = "store"
PAYLOAD_VERSION: Final = 1
MAX_DETAIL_LENGTH: Final = 1000
MAX_ACTION_LENGTH: Final = 300
ADDRESS_MARK: Final = "@"
DATE_FORMAT: Final = "%Y-%m-%d"
TELEGRAM_WINDOW_HOURS: Final = 48  # Telegram's deleteMessage refuses older bot messages
MAX_LISTED_IDS: Final = 30  # a detail lists at most this many Contact ids
PERSON_KIND: Final = "person"  # nextron.domain.contactability.KeyKind.PERSON


class ErasureStore(StrEnum):
    DATABASE = "database"
    SUPPRESSION = "suppression"
    EVIDENCE_QUOTES = "evidence_quotes"
    PAGE_ADDRESSES = "page_addresses"
    OTHER_CONTACTS = "other_contacts"
    MAILBOXES = "mailboxes"
    CRAWLED_PAGES = "crawled_pages"
    TELEGRAM = "telegram"
    ANTHROPIC = "anthropic"
    WORKER_KV = "worker_kv"
    BACKUPS = "backups"
    AI_TEST_SETS = "ai_test_sets"
    NEXTRONAI_INBOX = "nextronai_inbox"
    LOGS = "logs"
    SINK_FILES = "sink_files"
    RESTORE_WORK = "restore_work"
    ERASURE_LEDGER = "erasure_ledger"

    @property
    def label(self) -> str:
        """The store's name on a receipt."""
        return STORE_LABELS[self]

    @property
    def action(self) -> str:
        """What an erasure does to the store (AD-17)."""
        return STORE_ACTIONS[self]


STORE_LABELS: Final[Mapping[ErasureStore, str]] = MappingProxyType(
    {
        ErasureStore.DATABASE: "Database",
        ErasureStore.SUPPRESSION: "Suppression List",
        ErasureStore.EVIDENCE_QUOTES: "Evidence quotes",
        ErasureStore.PAGE_ADDRESSES: "Page addresses",
        ErasureStore.OTHER_CONTACTS: "Other Contacts",
        ErasureStore.MAILBOXES: "Sending Inbox mailboxes",
        ErasureStore.CRAWLED_PAGES: "Crawled page text",
        ErasureStore.TELEGRAM: "Telegram",
        ErasureStore.ANTHROPIC: "Anthropic",
        ErasureStore.WORKER_KV: "Worker KV",
        ErasureStore.BACKUPS: "Backups",
        ErasureStore.AI_TEST_SETS: "AI Test Sets",
        ErasureStore.NEXTRONAI_INBOX: "nextronai.com inbox",
        ErasureStore.LOGS: "Logs",
        ErasureStore.SINK_FILES: "Dev and test sink files",
        ErasureStore.RESTORE_WORK: "Restore work copies",
        ErasureStore.ERASURE_LEDGER: "Erasure ledger",
    }
)

STORE_ACTIONS: Final[Mapping[ErasureStore, str]] = MappingProxyType(
    {
        ErasureStore.DATABASE: (
            "Replace every personal-data column of the Contact with its tombstone; delete no row"
        ),
        ErasureStore.SUPPRESSION: (
            "Keep the person's hashed keys on the Suppression List, so they are never contacted "
            "again"
        ),
        ErasureStore.EVIDENCE_QUOTES: (
            "List the Evidence of the Lead that names the person but isn't linked to them"
        ),
        ErasureStore.PAGE_ADDRESSES: (
            "List the stored page addresses of the Lead that name the person"
        ),
        ErasureStore.OTHER_CONTACTS: (
            "List the other Contacts that share the person's address or LinkedIn URL"
        ),
        ErasureStore.MAILBOXES: (
            "Move the Contact's Campaign Threads to Trash (gmail.modify; Gmail purges after 30 "
            "days)"
        ),
        ErasureStore.CRAWLED_PAGES: (
            "Null the text of the Lead's crawled pages, keeping the content hash; page text is "
            "kept at most 30 days"
        ),
        ErasureStore.TELEGRAM: (
            "Delete the bot's messages about the Contact with deleteMessage, best effort; "
            "messages over 48 hours old remain"
        ),
        ErasureStore.ANTHROPIC: (
            "Nothing to delete: request and response bodies are never stored and batches are "
            "deleted after ingest; the provider's retention window remains"
        ),
        ErasureStore.WORKER_KV: (
            "Nothing to delete: tokens and times only, expiring by the 30-day TTL"
        ),
        ErasureStore.BACKUPS: (
            "Let the copies age out within the 35-day rotation; erase the Contact again after "
            "any restore"
        ),
        ErasureStore.AI_TEST_SETS: (
            "Count the Reply override cases made from the Contact, which playbook's hook erased "
            "and retired; the other cases are synthetic or pseudonymised"
        ),
        ErasureStore.NEXTRONAI_INBOX: "List the alert and Call Prep emails as a residual",
        ErasureStore.LOGS: "Nothing to erase: logs hold ids and codes only",
        ErasureStore.SINK_FILES: (
            "Replace the Contact's recorded messages in the dev and test sink files with "
            "tombstones"
        ),
        ErasureStore.RESTORE_WORK: (
            "Nothing to erase: decrypted work copies are deleted after each restore test or "
            "restore"
        ),
        ErasureStore.ERASURE_LEDGER: (
            "Nothing to erase: the ledger holds ids, key hashes and firm domains only"
        ),
    }
)

DATABASE_STORES: Final = (
    ErasureStore.DATABASE,
    ErasureStore.SUPPRESSION,
    ErasureStore.EVIDENCE_QUOTES,
    ErasureStore.PAGE_ADDRESSES,
    ErasureStore.OTHER_CONTACTS,
)
AD17_STORES: Final = (
    ErasureStore.MAILBOXES,
    ErasureStore.CRAWLED_PAGES,
    ErasureStore.TELEGRAM,
    ErasureStore.ANTHROPIC,
    ErasureStore.WORKER_KV,
    ErasureStore.BACKUPS,
    ErasureStore.AI_TEST_SETS,
    ErasureStore.NEXTRONAI_INBOX,
)
FILE_STORES: Final = (
    ErasureStore.LOGS,
    ErasureStore.SINK_FILES,
    ErasureStore.RESTORE_WORK,
    ErasureStore.ERASURE_LEDGER,
)
EXTERNAL_STORES: Final = AD17_STORES + FILE_STORES

# Fixed for ever: each number is a job key's discriminator, and a number is never reused.
STORE_NUMBERS: Final[Mapping[ErasureStore, int]] = MappingProxyType(
    {
        ErasureStore.MAILBOXES: 1,
        ErasureStore.CRAWLED_PAGES: 2,
        ErasureStore.TELEGRAM: 3,
        ErasureStore.ANTHROPIC: 4,
        ErasureStore.WORKER_KV: 5,
        ErasureStore.BACKUPS: 6,
        ErasureStore.AI_TEST_SETS: 7,
        ErasureStore.NEXTRONAI_INBOX: 8,
        ErasureStore.LOGS: 9,
        ErasureStore.SINK_FILES: 10,
        ErasureStore.RESTORE_WORK: 11,
        ErasureStore.ERASURE_LEDGER: 12,
    }
)


class StoreStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    RESIDUAL = "residual"
    NONE_HELD = "none_held"
    FAILED = "failed"

    @property
    def label(self) -> str:
        """The status as a receipt shows it."""
        return STATUS_LABELS[self]


STATUS_LABELS: Final[Mapping[StoreStatus, str]] = MappingProxyType(
    {
        StoreStatus.PENDING: "Pending",
        StoreStatus.DONE: "Done",
        StoreStatus.RESIDUAL: "Residual",
        StoreStatus.NONE_HELD: "None held",
        StoreStatus.FAILED: "Failed",
    }
)
TERMINAL_STATUSES: Final = (
    StoreStatus.DONE,
    StoreStatus.RESIDUAL,
    StoreStatus.NONE_HELD,
    StoreStatus.FAILED,
)


class StoreCause(StrEnum):
    SETTLED = "settled"


TRANSITIONS: Final[
    Mapping[tuple[StoreStatus, StoreStatus], Transition[StoreStatus, StoreCause]]
] = MappingProxyType(
    {
        (StoreStatus.PENDING, status): Transition(status, StoreCause.SETTLED)
        for status in TERMINAL_STATUSES
    }
)


def transition(current: StoreStatus, to: StoreStatus) -> Transition[StoreStatus, StoreCause]:
    """Where settling a store from ``current`` to ``to`` leads; raises ``IllegalTransition``
    when the table has no such row."""
    try:
        found = TRANSITIONS.get((current, to))
    except TypeError:  # an unhashable argument is no status
        found = None
    if found is None:
        raise IllegalTransition(MACHINE, to, current)
    return found


class ReceiptOutcome(StrEnum):
    ERASED = "erased"
    ALREADY_ERASED = "already_erased"


class ReceiptState(StrEnum):
    COMPLETE = "complete"
    IN_PROGRESS = "in_progress"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class ReceiptReading:
    """A receipt's state, and how many of its stores failed."""

    state: ReceiptState
    failed: int = 0

    @property
    def label(self) -> str:
        if self.state is ReceiptState.INCOMPLETE:
            return f"Incomplete: {self.failed} failed"
        if self.state is ReceiptState.IN_PROGRESS:
            return "In progress"
        return "Complete"


def _status(value: object) -> StoreStatus:
    if isinstance(value, StoreStatus):
        return value
    if not isinstance(value, str):
        raise TypeError(f"a store status must be a str, not {type(value).__name__}")
    try:
        return StoreStatus(value)
    except ValueError:
        raise ValueError("a store status must be one of StoreStatus") from None


def receipt_state(statuses: Iterable[StoreStatus | str]) -> ReceiptReading:
    """The receipt's state from its stores' statuses; see the module docstring."""
    found = [_status(status) for status in statuses]
    failed = sum(1 for status in found if status is StoreStatus.FAILED)
    if failed:
        return ReceiptReading(ReceiptState.INCOMPLETE, failed)
    if StoreStatus.PENDING in found:
        return ReceiptReading(ReceiptState.IN_PROGRESS)
    return ReceiptReading(ReceiptState.COMPLETE)


def check_detail(detail: object) -> str:
    """``detail`` when it is a valid store detail; ``ValueError`` (``TypeError`` for a non-
    ``str``) otherwise, never echoing it."""
    if not isinstance(detail, str):
        raise TypeError(f"a store detail must be a str, not {type(detail).__name__}")
    if not 1 <= len(detail) <= MAX_DETAIL_LENGTH or detail != detail.strip():
        raise ValueError(
            f"a store detail must be 1 to {MAX_DETAIL_LENGTH} characters, with no space at "
            "either end"
        )
    if ADDRESS_MARK in detail:
        raise ValueError("a store detail must not hold an @")
    if any(unicodedata.category(character) == "Cc" for character in detail):
        raise ValueError("a store detail must not hold a control character")
    return detail


@dataclass(frozen=True, slots=True)
class StoreResult:
    """A store's status and its detail; see the module docstring."""

    status: StoreStatus
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, StoreStatus):
            raise TypeError(
                f"StoreResult.status must be a StoreStatus, not {type(self.status).__name__}"
            )
        check_detail(self.detail)


# Matching a name -----------------------------------------------------------------------------


def _is_word_character(character: str) -> bool:
    return character.isalnum() or unicodedata.category(character).startswith("M")


def folded_words(text: str) -> tuple[str, ...]:
    """The words of ``text`` folded as ``normalize_name`` folds them, with no length limit."""
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, not {type(text).__name__}")
    folded = unicodedata.normalize("NFKC", text).casefold()
    spaced = "".join(ch if _is_word_character(ch) else " " for ch in folded)
    return tuple(unicodedata.normalize("NFKC", " ".join(spaced.split())).split())


def mentions_name(name: str, text: str) -> bool:
    """Whether every word of ``name`` is a whole word of ``text``; see the module docstring."""
    named = folded_words(name)
    if not named:
        return False
    words = frozenset(folded_words(text))
    return all(word in words for word in named)


def url_mentions_name(name: str, url: str) -> bool:
    """Whether the path of ``url`` names ``name``; see the module docstring."""
    named = folded_words(name)
    if not isinstance(url, str):
        raise TypeError(f"url must be a str, not {type(url).__name__}")
    if not named:
        return False
    try:
        path = urllib.parse.urlsplit(url).path
    except ValueError:
        return False
    words = frozenset(folded_words(urllib.parse.unquote(path, errors="replace")))
    return all(word in words for word in named) or "".join(named) in words


# Time rules ----------------------------------------------------------------------------------


def is_recent_message(sent_at: datetime, now: datetime) -> bool:
    """Whether a message sent at ``sent_at`` is under 48 hours old at ``now``."""
    return now - sent_at < timedelta(hours=TELEGRAM_WINDOW_HOURS)


def age_out(started_at: datetime, days: int) -> date:
    """The date a copy taken at ``started_at`` ages out of a ``days``-day rotation."""
    return (started_at + timedelta(days=days)).date()


# Detail builders -----------------------------------------------------------------------------


def day(at: date | datetime) -> str:
    """``at`` as ``YYYY-MM-DD``."""
    return f"{at:{DATE_FORMAT}}"


def _count(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _check_count(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


PENDING_DETAIL: Final = "Waiting for its erase_external job"
DATABASE_DETAIL: Final = (
    "The Contact's name, role, address, LinkedIn URL and office, its Provenance Records, the "
    "Evidence linked to it, the void reasons on its keys, the titles and descriptions of its "
    "Lead's job postings and its Reply override cases were replaced by tombstones; no row was "
    "deleted"
)
RETENTION_SUPPRESSION_DETAIL: Final = "Retention writes no suppression entry"
NO_KEY_DETAIL: Final = (
    "No address, person or profile key is known for this person, so none stays suppressed: a "
    "later import of this person isn't blocked"
)
NO_KEYED_NAME_TAIL: Final = (
    "; no person key, because the Contact's name and the Lead's web domain don't form one: a "
    "later import of this person by name alone on this Lead isn't blocked"
)
NO_PERSON_KEY_TAIL: Final = (
    "; no person key, because the Lead has no web domain: a later import of this person by "
    "name alone on this Lead isn't blocked"
)
NO_NAME_DETAIL: Final = "The Contact had no name to look for"
NO_QUOTE_DETAIL: Final = "None contains the person's full name"
NO_ADDRESS_NAMES_DETAIL: Final = "None contains the person's full name"
KEPT_TAIL: Final = "the person and {kept} (the firm's public pages)"
NO_OTHER_CONTACT_DETAIL: Final = "No other Contact shares this address or LinkedIn URL"
MAILBOX_PLACEHOLDER_DETAIL: Final = "The Contact has no Campaign Threads"
NO_PAGES_DETAIL: Final = "The Lead had no stored pages"
PAGES_TAIL: Final = (
    "nulled, content hash kept; a later crawl stores public page text again for at most 30 days"
)
NO_ALERT_DETAIL: Final = "No Telegram alert about the Contact was sent"
TELEGRAM_OLDER: Final = "(Telegram message older than 48 hours)"
BACKUPS_TAIL: Final = (
    "Copies saved by `nextron restore` or left by a failed migration are kept until deleted by "
    "hand. Erasure re-applied after any restore."
)
BACKUP_WINDOW_DAYS: Final = 35  # nextron.domain.backup.MAX_ROTATION_DAYS, AD-17's rotation
ANTHROPIC_DETAIL: Final = (
    "Anthropic: request and response bodies are never stored, and batches are deleted after "
    "ingest; the provider's retention window remains"
)
WORKER_KV_DETAIL: Final = "Worker KV: token and time only; expires within 30 days"
AI_TEST_SETS_TAIL: Final = "the other cases are synthetic or pseudonymised"
NO_OVERRIDE_CASES_DETAIL: Final = (
    f"AI Test Sets: no case was made from the Contact's Replies; {AI_TEST_SETS_TAIL}"
)
NEXTRONAI_INBOX_DETAIL: Final = (
    "nextronai.com inbox: alert and Call Prep emails about the Contact remain until deleted by "
    "hand"
)
LOGS_DETAIL: Final = "Logs hold ids and codes only; email addresses are masked when written"
RESTORE_WORK_DETAIL: Final = (
    "Decrypted work copies are deleted when each restore test or restore ends, and a crash's "
    "leftover at the next daily check"
)
ERASURE_LEDGER_DETAIL: Final = "The ledger holds ids, key hashes and firm domains only"
NO_SINK_IN_PROD_DETAIL: Final = "This environment (prod) records no sink files"
NO_SINK_ADDRESS_DETAIL: Final = (
    "The Contact had no address, so no sink message was addressed to it"
)
NO_SINK_MESSAGE_DETAIL: Final = "No dev or test sink message was addressed to the Contact"
FAILED_LEAD: Final = "Failed: "
UNKNOWN_CODE: Final = "error"


def pending() -> StoreResult:
    """An external store before its job runs."""
    return StoreResult(StoreStatus.PENDING, PENDING_DETAIL)


def database_done() -> StoreResult:
    """The database item of an erasure."""
    return StoreResult(StoreStatus.DONE, DATABASE_DETAIL)


def already_erased(erased_at: date | datetime) -> StoreResult:
    """An in-database item of a repeat request on a Contact erased at ``erased_at``."""
    return StoreResult(
        StoreStatus.NONE_HELD, f"Already erased on {day(erased_at)}; see that erasure's receipt"
    )


def as_at_erasure(
    erased_on: date | datetime, status: StoreStatus | str, detail: str
) -> StoreResult:
    """A repeat request's copy of an in-database item of the erasure's receipt (written on
    ``erased_on``): its status, with "As at the erasure on <date>: <detail>", or, when that
    wouldn't fit, "As at the erasure on <date>: <status label>; see that erasure's receipt"."""
    found = _status(status)
    lead = f"As at the erasure on {day(erased_on)}: "
    text = lead + detail
    if len(text) > MAX_DETAIL_LENGTH:
        text = f"{lead}{found.label}; see that erasure's receipt"
    return StoreResult(found, text)


def unrecorded(erased_at: date | datetime) -> StoreResult:
    """A repeat request's in-database item when the Contact was erased before receipts were
    kept (no earlier ``erased`` receipt)."""
    return StoreResult(
        StoreStatus.RESIDUAL,
        f"Erased on {day(erased_at)}, before receipts were kept: what remains here wasn't "
        "recorded",
    )


def suppression(
    cause: ErasureCause | str,
    kinds: Sequence[str],
    *,
    has_name: bool,
    has_web_domain: bool,
) -> StoreResult:
    """The Suppression List item: ``kinds`` are the kinds of the person's keys (in order). A
    Contact with a name whose keys hold no ``person`` key gets the "no person key" residual,
    whatever the reason; ``has_web_domain`` only picks which reason it gives."""
    found = ErasureCause(cause)
    if found is ErasureCause.RETENTION:
        return StoreResult(StoreStatus.NONE_HELD, RETENTION_SUPPRESSION_DETAIL)
    names = [str(kind) for kind in kinds]
    if not names:
        return StoreResult(StoreStatus.RESIDUAL, NO_KEY_DETAIL)
    verb = "stays" if len(names) == 1 else "stay"
    text = f"{_count(len(names), 'key', 'keys')} ({', '.join(names)}) {verb} suppressed"
    if has_name and PERSON_KIND not in names:
        tail = NO_KEYED_NAME_TAIL if has_web_domain else NO_PERSON_KEY_TAIL
        return StoreResult(StoreStatus.RESIDUAL, text + tail)
    return StoreResult(StoreStatus.DONE, text)


def _kept(count: int, one: str, many: str) -> str:
    """"N <things> name the person and were kept (the firm's public pages)"."""
    names, kept = ("names", "was kept") if count == 1 else ("name", "were kept")
    return f"{_count(count, one, many)} {names} " + KEPT_TAIL.format(kept=kept)


def evidence_quotes(count: int, *, has_name: bool) -> StoreResult:
    """The Evidence quotes item: ``count`` Evidence rows not linked to the person name them."""
    found = _check_count("count", count)
    if not has_name:
        return StoreResult(StoreStatus.NONE_HELD, NO_NAME_DETAIL)
    if found == 0:
        return StoreResult(StoreStatus.NONE_HELD, NO_QUOTE_DETAIL)
    return StoreResult(StoreStatus.RESIDUAL, _kept(found, "Evidence row", "Evidence rows"))


def page_addresses(count: int, *, has_name: bool) -> StoreResult:
    """The page addresses item: ``count`` distinct stored page addresses name the person."""
    found = _check_count("count", count)
    if not has_name:
        return StoreResult(StoreStatus.NONE_HELD, NO_NAME_DETAIL)
    if found == 0:
        return StoreResult(StoreStatus.NONE_HELD, NO_ADDRESS_NAMES_DETAIL)
    return StoreResult(StoreStatus.RESIDUAL, _kept(found, "page address", "page addresses"))


def other_contacts(contact_ids: Sequence[str]) -> StoreResult:
    """The other Contacts item: the ids of the other Contacts sharing a key, sorted."""
    ids = sorted(contact_ids)
    if not ids:
        return StoreResult(StoreStatus.NONE_HELD, NO_OTHER_CONTACT_DETAIL)
    verbs = ("shares", "was") if len(ids) == 1 else ("share", "were")
    listed = ", ".join(ids[:MAX_LISTED_IDS])
    if len(ids) > MAX_LISTED_IDS:
        listed += f" and {len(ids) - MAX_LISTED_IDS} more"
    return StoreResult(
        StoreStatus.RESIDUAL,
        f"{_count(len(ids), 'other Contact', 'other Contacts')} {verbs[0]} this address or "
        f"LinkedIn URL and {verbs[1]} not erased: {listed}",
    )


def mailboxes_none() -> StoreResult:
    """The Sending Inbox mailboxes while the Contact has no Campaign Threads (Story 6.7)."""
    return StoreResult(StoreStatus.NONE_HELD, MAILBOX_PLACEHOLDER_DETAIL)


def crawled_pages(nulled: int, kept: int = 0) -> StoreResult:
    """The crawled page text of the Contact's Lead: ``nulled`` stored pages whose text is NULL,
    and ``kept`` crawled since the erasure that hold public page text again."""
    gone = _check_count("nulled", nulled)
    held = _check_count("kept", kept)
    if gone + held == 0:
        return StoreResult(StoreStatus.NONE_HELD, NO_PAGES_DETAIL)
    if held == 0:
        return StoreResult(
            StoreStatus.DONE, f"Text of {_count(gone, 'page', 'pages')} {PAGES_TAIL}"
        )
    holds = "holds" if held == 1 else "hold"
    return StoreResult(
        StoreStatus.RESIDUAL,
        f"{_count(gone, 'page', 'pages')} nulled, content hash kept; "
        f"{_count(held, 'page', 'pages')} crawled since {holds} public page text again, kept at "
        "most 30 days",
    )


def telegram(*, deleted: int, refused: int, older: int, unconfirmed: int) -> StoreResult:
    """Telegram: ``deleted`` messages deleted, ``refused`` that Telegram wouldn't delete,
    ``older`` sent over 48 hours ago (or with no message id), and ``unconfirmed`` that may have
    been delivered without an id."""
    counts = [
        _check_count(name, value)
        for name, value in (
            ("deleted", deleted),
            ("refused", refused),
            ("older", older),
            ("unconfirmed", unconfirmed),
        )
    ]
    gone, kept, old, maybe = counts
    if not any(counts):
        return StoreResult(StoreStatus.NONE_HELD, NO_ALERT_DETAIL)
    if not (kept or old or maybe):
        return StoreResult(StoreStatus.DONE, f"{gone} deleted")
    parts = [f"{gone} deleted"]
    if kept:
        parts.append(f"{kept} that Telegram would not delete {_remain(kept)}")
    if old:
        parts.append(f"{old} older than 48 hours {_remain(old)} {TELEGRAM_OLDER}")
    if maybe:
        parts.append(f"{maybe} that may have been delivered without an id {_remain(maybe)}")
    return StoreResult(StoreStatus.RESIDUAL, "; ".join(parts))


def _remain(count: int) -> str:
    return "remains" if count == 1 else "remain"


def backups(
    *, local: int, offpc: int, first: date | None, last: date | None, erased_at: datetime
) -> StoreResult:
    """Backups, always ``residual``: ``local`` local and ``offpc`` off-PC recorded copies taken
    while the database held the Contact, ageing out between ``first`` and ``last``; and the
    bound every copy taken before the erasure (a backup still running, or one not yet recorded,
    included) ages out by: ``erased_at`` plus 35 days."""
    near = _check_count("local", local)
    far = _check_count("offpc", offpc)
    bound = (
        "every copy taken before the erasure ages out by "
        f"{day(age_out(erased_at, BACKUP_WINDOW_DAYS))}, "
        "while daily backups keep running"
    )
    if near + far == 0:
        return StoreResult(
            StoreStatus.RESIDUAL,
            f"No recorded backup copy holds the Contact; {bound}. {BACKUPS_TAIL}",
        )
    if first is None or last is None:
        raise ValueError("a backup residual with copies needs their age-out dates")
    return StoreResult(
        StoreStatus.RESIDUAL,
        f"{near} local and {far} off-PC {'copy' if near + far == 1 else 'copies'} taken before "
        f"the erasure hold the Contact; they age out between {day(first)} and {day(last)}, and "
        f"{bound}. {BACKUPS_TAIL}",
    )


def ai_test_sets(*, cases: int, erased: int) -> StoreResult:
    """The AI Test Sets (merge of Stories 3.14 and 4.6): ``cases`` Reply override cases were
    made from the Contact, ``erased`` of them erased (input tombstoned, retired) by playbook's
    hook. ``none_held`` with none, ``done`` when every one is erased, else ``residual``."""
    total = _check_count("cases", cases)
    gone = _check_count("erased", erased)
    if gone > total:
        raise ValueError("erased must not exceed cases")
    if total == 0:
        return StoreResult(StoreStatus.NONE_HELD, NO_OVERRIDE_CASES_DETAIL)
    made = _count(total, "override case", "override cases")
    if gone == total:
        return StoreResult(
            StoreStatus.DONE,
            f"AI Test Sets: {made} made from the Contact's Replies erased and retired; "
            f"{AI_TEST_SETS_TAIL}",
        )
    return StoreResult(
        StoreStatus.RESIDUAL,
        f"AI Test Sets: {total - gone} of {made} made from the Contact's Replies still "
        f"{'holds' if total - gone == 1 else 'hold'} text; {AI_TEST_SETS_TAIL}",
    )


def sink_files(*, files: int, lines: int) -> StoreResult:
    """The dev and test sink: ``lines`` recorded messages in ``files`` files were tombstoned."""
    found_files = _check_count("files", files)
    found_lines = _check_count("lines", lines)
    if found_lines == 0:
        return StoreResult(StoreStatus.NONE_HELD, NO_SINK_MESSAGE_DETAIL)
    return StoreResult(
        StoreStatus.DONE,
        f"{_count(found_lines, 'message', 'messages')} in {_count(found_files, 'file', 'files')}",
    )


def sink_in_prod() -> StoreResult:
    return StoreResult(StoreStatus.NONE_HELD, NO_SINK_IN_PROD_DETAIL)


def sink_no_address() -> StoreResult:
    return StoreResult(StoreStatus.NONE_HELD, NO_SINK_ADDRESS_DETAIL)


FIXED_RESULTS: Final[Mapping[ErasureStore, StoreResult]] = MappingProxyType(
    {
        ErasureStore.ANTHROPIC: StoreResult(StoreStatus.RESIDUAL, ANTHROPIC_DETAIL),
        ErasureStore.WORKER_KV: StoreResult(StoreStatus.RESIDUAL, WORKER_KV_DETAIL),
        ErasureStore.NEXTRONAI_INBOX: StoreResult(StoreStatus.RESIDUAL, NEXTRONAI_INBOX_DETAIL),
        ErasureStore.LOGS: StoreResult(StoreStatus.NONE_HELD, LOGS_DETAIL),
        ErasureStore.RESTORE_WORK: StoreResult(StoreStatus.NONE_HELD, RESTORE_WORK_DETAIL),
        ErasureStore.ERASURE_LEDGER: StoreResult(StoreStatus.NONE_HELD, ERASURE_LEDGER_DETAIL),
    }
)


def _is_code(code: object) -> bool:
    return (
        isinstance(code, str)
        and 1 <= len(code) <= 64
        and all(ch.isascii() and (ch.isalnum() or ch in "_.:-") for ch in code)
    )


def failed(code: str) -> StoreResult:
    """A store whose handler failed with the error code ``code`` ("Failed: <code>"); a code
    that isn't 1 to 64 ASCII letters, digits, ``_``, ``.``, ``:`` or ``-`` shows as
    ``error``."""
    shown = code if _is_code(code) else UNKNOWN_CODE
    return StoreResult(StoreStatus.FAILED, FAILED_LEAD + shown)
