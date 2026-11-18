"""The pure rules of erasure receipts (``nextron.domain.erasure_receipt``, Story 3.14): the
stores, their numbers and texts, the store machine, the receipt's state, ``StoreResult``'s
detail check, the name matchers and every detail builder of the spec's I/O matrix and Design
Notes."""

from datetime import UTC, date, datetime

import pytest

from nextron.domain import erasure_receipt as receipt
from nextron.domain.erasure_receipt import (
    AD17_STORES,
    DATABASE_STORES,
    EXTERNAL_STORES,
    FILE_STORES,
    FIXED_RESULTS,
    MAX_ACTION_LENGTH,
    STORE_NUMBERS,
    TRANSITIONS,
    ErasureStore,
    ReceiptOutcome,
    ReceiptState,
    StoreCause,
    StoreResult,
    StoreStatus,
    mentions_name,
    receipt_state,
    transition,
    url_mentions_name,
)
from nextron.domain.machines import IllegalTransition

ERASED_AT = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)


# The stores ----------------------------------------------------------------------------------


def test_the_stores_are_the_seventeen_in_display_order() -> None:
    assert [str(store) for store in ErasureStore] == [
        "database",
        "suppression",
        "evidence_quotes",
        "page_addresses",
        "other_contacts",
        "mailboxes",
        "crawled_pages",
        "telegram",
        "anthropic",
        "worker_kv",
        "backups",
        "ai_test_sets",
        "nextronai_inbox",
        "logs",
        "sink_files",
        "restore_work",
        "erasure_ledger",
    ]
    assert DATABASE_STORES + EXTERNAL_STORES == tuple(ErasureStore)
    assert EXTERNAL_STORES == AD17_STORES + FILE_STORES
    assert len(AD17_STORES) == 8 and len(FILE_STORES) == 4


def test_the_store_numbers_are_pinned_for_ever() -> None:
    # a number is a job key's discriminator: never renumber, never reuse one
    assert dict(STORE_NUMBERS) == {
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


@pytest.mark.parametrize("store", list(ErasureStore))
def test_every_store_has_a_label_and_a_short_action(store: ErasureStore) -> None:
    assert store.label and store.label == store.label.strip()
    assert 1 <= len(store.action) <= MAX_ACTION_LENGTH
    assert "@" not in store.label + store.action


def test_the_labels_name_the_stores() -> None:
    assert [store.label for store in AD17_STORES] == [
        "Sending Inbox mailboxes",
        "Crawled page text",
        "Telegram",
        "Anthropic",
        "Worker KV",
        "Backups",
        "AI Test Sets",
        "nextronai.com inbox",
    ]


# The store machine ---------------------------------------------------------------------------


def test_pending_settles_to_each_terminal_status() -> None:
    assert [str(status) for status in StoreStatus] == [
        "pending",
        "done",
        "residual",
        "none_held",
        "failed",
    ]
    assert [status.label for status in StoreStatus] == [
        "Pending",
        "Done",
        "Residual",
        "None held",
        "Failed",
    ]
    for status in receipt.TERMINAL_STATUSES:
        change = transition(StoreStatus.PENDING, status)
        assert (change.to, change.cause) == (status, StoreCause.SETTLED)
    assert len(TRANSITIONS) == 4


@pytest.mark.parametrize(
    ("current", "to"),
    [
        (StoreStatus.PENDING, StoreStatus.PENDING),
        (StoreStatus.DONE, StoreStatus.RESIDUAL),
        (StoreStatus.FAILED, StoreStatus.DONE),
        (StoreStatus.NONE_HELD, StoreStatus.PENDING),
        ([], StoreStatus.DONE),
    ],
)
def test_anything_else_is_an_illegal_transition(current: object, to: StoreStatus) -> None:
    with pytest.raises(IllegalTransition):
        transition(current, to)  # type: ignore[arg-type]


def test_the_outcomes() -> None:
    assert [str(outcome) for outcome in ReceiptOutcome] == ["erased", "already_erased"]


# The receipt's state -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "state", "label"),
    [
        (["done", "residual", "none_held"], ReceiptState.COMPLETE, "Complete"),
        ([], ReceiptState.COMPLETE, "Complete"),
        (["done", "pending"], ReceiptState.IN_PROGRESS, "In progress"),
        (["pending", "failed", "done"], ReceiptState.INCOMPLETE, "Incomplete: 1 failed"),
        ([StoreStatus.FAILED, StoreStatus.FAILED], ReceiptState.INCOMPLETE, "Incomplete: 2 failed"),
    ],
)
def test_the_receipt_state(statuses: list[object], state: ReceiptState, label: str) -> None:
    reading = receipt_state(statuses)  # type: ignore[arg-type]

    assert (reading.state, reading.label) == (state, label)


def test_an_unknown_status_is_refused() -> None:
    with pytest.raises(ValueError, match="StoreStatus"):
        receipt_state(["done", "lost"])
    with pytest.raises(TypeError):
        receipt_state([3])  # type: ignore[list-item]


# StoreResult ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "detail",
    ["", " padded", "padded ", "x" * 1001, "mail jane@firm.co.uk", "two\nlines", "tab\there"],
)
def test_a_bad_detail_is_refused_without_echoing_it(detail: str) -> None:
    with pytest.raises(ValueError) as refused:
        StoreResult(StoreStatus.DONE, detail)
    assert detail.strip() == "" or detail.strip() not in str(refused.value)


def test_a_result_takes_a_status_and_a_text() -> None:
    assert StoreResult(StoreStatus.DONE, "x" * 1000).detail == "x" * 1000
    with pytest.raises(TypeError):
        StoreResult("done", "fine")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        StoreResult(StoreStatus.DONE, 3)  # type: ignore[arg-type]


# The name matchers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "text", "found"),
    [
        ("Jane Doe", "Jane Doe leads our litigation team.", True),
        ("Jane Doe", "Partners: Doe, Jane (litigation)", True),
        ("JANE DOE", "jane doe", True),
        ("Jane Doe", "Jane leads our team.", False),
        ("Jane Doe", "Janet Doering", False),
        ("Zoë Brontë", "Meet ZOË BRONTË.", True),
        ("Zoë Brontë", "Meet Zoe Bronte.", False),
        ("ﬁona Lee", "fiona lee", True),  # NFKC: the ligature folds
        ("", "Jane Doe", False),
        ("--", "anything", False),
    ],
)
def test_mentions_name_needs_every_word_whole(name: str, text: str, found: bool) -> None:
    assert mentions_name(name, text) is found


@pytest.mark.parametrize(
    ("url", "found"),
    [
        ("https://firm.co.uk/people/jane-doe", True),
        ("https://firm.co.uk/people/jane_doe/", True),
        ("https://firm.co.uk/people/janedoe", True),
        ("https://firm.co.uk/people/JaneDoe.html", True),
        ("https://firm.co.uk/people/jane%20doe", True),
        ("https://firm.co.uk/people/doe-jane", True),
        ("https://firm.co.uk/people/jane", False),
        ("https://firm.co.uk/team?name=jane-doe", False),  # the query isn't the path
        ("https://jane-doe.co.uk/", False),  # nor is the host
        ("http://[::1/people/jane-doe", False),  # can't be split
    ],
)
def test_url_mentions_name_reads_the_decoded_path(url: str, found: bool) -> None:
    assert url_mentions_name("Jane Doe", url) is found


def test_an_empty_name_never_matches_a_url() -> None:
    assert url_mentions_name("", "https://firm.co.uk/") is False
    assert url_mentions_name("-", "https://firm.co.uk/-") is False


# Detail builders -----------------------------------------------------------------------------


def test_the_database_and_pending_details() -> None:
    assert receipt.database_done().status is StoreStatus.DONE
    assert receipt.database_done().detail.endswith("no row was deleted")
    assert receipt.pending().status is StoreStatus.PENDING
    assert receipt.already_erased(ERASED_AT) == StoreResult(
        StoreStatus.NONE_HELD, "Already erased on 2026-11-18; see that erasure's receipt"
    )


@pytest.mark.parametrize(
    ("cause", "kinds", "has_name", "has_web", "status", "detail"),
    [
        (
            "dsr",
            ["address", "person", "profile"],
            True,
            True,
            StoreStatus.DONE,
            "3 keys (address, person, profile) stay suppressed",
        ),
        ("dsr", ["address"], False, True, StoreStatus.DONE, "1 key (address) stays suppressed"),
        (
            "dsr",
            ["address", "profile"],
            True,
            False,
            StoreStatus.RESIDUAL,
            "2 keys (address, profile) stay suppressed; no person key, because the Lead has no "
            "web domain: a later import of this person by name alone on this Lead isn't blocked",
        ),
        (
            "dsr",
            ["address", "profile"],
            True,
            True,
            StoreStatus.RESIDUAL,
            "2 keys (address, profile) stay suppressed; no person key, because the Contact's "
            "name and the Lead's web domain don't form one: a later import of this person by "
            "name alone on this Lead isn't blocked",
        ),  # a web domain, but no person key formed: the tail follows the keys
        ("dsr", [], True, True, StoreStatus.RESIDUAL, receipt.NO_KEY_DETAIL),
        (
            "retention",
            ["address"],
            True,
            True,
            StoreStatus.NONE_HELD,
            "Retention writes no suppression entry",
        ),
    ],
)
def test_the_suppression_detail(
    cause: str,
    kinds: list[str],
    has_name: bool,
    has_web: bool,
    status: StoreStatus,
    detail: str,
) -> None:
    assert receipt.suppression(
        cause, kinds, has_name=has_name, has_web_domain=has_web
    ) == StoreResult(status, detail)


def test_the_quote_and_page_address_details() -> None:
    assert receipt.evidence_quotes(2, has_name=True) == StoreResult(
        StoreStatus.RESIDUAL,
        "2 Evidence rows name the person and were kept (the firm's public pages)",
    )
    assert receipt.evidence_quotes(1, has_name=True).detail == (
        "1 Evidence row names the person and was kept (the firm's public pages)"
    )
    assert receipt.evidence_quotes(0, has_name=True) == StoreResult(
        StoreStatus.NONE_HELD, "None contains the person's full name"
    )
    assert receipt.page_addresses(1, has_name=True).detail == (
        "1 page address names the person and was kept (the firm's public pages)"
    )
    assert receipt.page_addresses(0, has_name=True).status is StoreStatus.NONE_HELD
    for build in (receipt.evidence_quotes, receipt.page_addresses):
        assert build(3, has_name=False).status is StoreStatus.NONE_HELD
    with pytest.raises(ValueError):
        receipt.evidence_quotes(-1, has_name=True)
    with pytest.raises(TypeError):
        receipt.page_addresses(True, has_name=True)


def test_the_other_contacts_detail_lists_the_ids_sorted() -> None:
    ids = ["01JD0000000000000000000002", "01JD0000000000000000000001"]

    assert receipt.other_contacts(ids) == StoreResult(
        StoreStatus.RESIDUAL,
        "2 other Contacts share this address or LinkedIn URL and were not erased: "
        "01JD0000000000000000000001, 01JD0000000000000000000002",
    )
    assert receipt.other_contacts(ids[:1]).detail.startswith(
        "1 other Contact shares this address or LinkedIn URL and was not erased"
    )
    assert receipt.other_contacts([]).status is StoreStatus.NONE_HELD
    many = receipt.other_contacts([f"01JD{n:022d}" for n in range(40)])
    assert many.detail.endswith(" and 10 more") and len(many.detail) <= 1000


def test_the_crawled_pages_and_mailbox_details() -> None:
    assert receipt.crawled_pages(3) == StoreResult(
        StoreStatus.DONE,
        "Text of 3 pages nulled, content hash kept; a later crawl stores public page text again "
        "for at most 30 days",
    )
    assert receipt.crawled_pages(0) == StoreResult(
        StoreStatus.NONE_HELD, "The Lead had no stored pages"
    )
    assert receipt.crawled_pages(2, 1) == StoreResult(
        StoreStatus.RESIDUAL,
        "2 pages nulled, content hash kept; 1 page crawled since holds public page text again, "
        "kept at most 30 days",
    )
    assert receipt.crawled_pages(0, 2).detail == (
        "0 pages nulled, content hash kept; 2 pages crawled since hold public page text again, "
        "kept at most 30 days"
    )
    assert receipt.mailboxes_none() == StoreResult(
        StoreStatus.NONE_HELD, "The Contact has no Campaign Threads"
    )


@pytest.mark.parametrize(
    ("counts", "status", "detail"),
    [
        ((0, 0, 0, 0), StoreStatus.NONE_HELD, "No Telegram alert about the Contact was sent"),
        ((2, 0, 0, 0), StoreStatus.DONE, "2 deleted"),
        (
            (1, 0, 1, 0),
            StoreStatus.RESIDUAL,
            "1 deleted; 1 older than 48 hours remains (Telegram message older than 48 hours)",
        ),
        (
            (0, 1, 2, 1),
            StoreStatus.RESIDUAL,
            "0 deleted; 1 that Telegram would not delete remains; 2 older than 48 hours remain "
            "(Telegram message older than 48 hours); 1 that may have been delivered without an "
            "id remains",
        ),
    ],
)
def test_the_telegram_detail(
    counts: tuple[int, int, int, int], status: StoreStatus, detail: str
) -> None:
    deleted, refused, older, unconfirmed = counts

    assert receipt.telegram(
        deleted=deleted, refused=refused, older=older, unconfirmed=unconfirmed
    ) == StoreResult(status, detail)


def test_the_backups_detail_gives_the_counts_the_age_out_window_and_the_bound() -> None:
    held = receipt.backups(
        local=2, offpc=1, first=date(2026, 12, 1), last=date(2026, 12, 20), erased_at=ERASED_AT
    )

    assert held.status is StoreStatus.RESIDUAL
    assert held.detail == (
        "2 local and 1 off-PC copies taken before the erasure hold the Contact; they age out "
        "between 2026-12-01 and 2026-12-20, and every copy taken before the erasure ages out by "
        "2026-12-23, while daily backups keep running. Copies saved by `nextron restore` or left "
        "by a failed migration are kept until deleted by hand. A restore brings the Contact back "
        "until the erasure ledger exists: erase it again after a restore."
    )
    # no recorded copy (a backup still running isn't recorded yet): residual all the same
    none = receipt.backups(local=0, offpc=0, first=None, last=None, erased_at=ERASED_AT)
    assert none == StoreResult(
        StoreStatus.RESIDUAL,
        "No recorded backup copy holds the Contact; every copy taken before the erasure ages out "
        "by 2026-12-23, while daily backups keep running. " + receipt.BACKUPS_TAIL,
    )
    with pytest.raises(ValueError):
        receipt.backups(local=1, offpc=0, first=None, last=None, erased_at=ERASED_AT)


def test_a_repeat_requests_copy_of_an_item_names_the_erasure() -> None:
    assert receipt.as_at_erasure(ERASED_AT, "residual", "2 Evidence rows") == StoreResult(
        StoreStatus.RESIDUAL, "As at the erasure on 2026-11-18: 2 Evidence rows"
    )
    long = "x" * 990
    assert receipt.as_at_erasure(ERASED_AT, StoreStatus.RESIDUAL, long) == StoreResult(
        StoreStatus.RESIDUAL,
        "As at the erasure on 2026-11-18: Residual; see that erasure's receipt",
    )
    assert receipt.unrecorded(ERASED_AT) == StoreResult(
        StoreStatus.RESIDUAL,
        "Erased on 2026-11-18, before receipts were kept: what remains here wasn't recorded",
    )
    with pytest.raises(ValueError):
        receipt.as_at_erasure(ERASED_AT, "lost", "x")


def test_the_sink_details() -> None:
    assert receipt.sink_files(files=1, lines=2) == StoreResult(
        StoreStatus.DONE, "2 messages in 1 file"
    )
    assert receipt.sink_files(files=0, lines=0).status is StoreStatus.NONE_HELD
    assert receipt.sink_in_prod().status is StoreStatus.NONE_HELD
    assert receipt.sink_no_address().status is StoreStatus.NONE_HELD


def test_the_fixed_results_are_the_ad17_texts() -> None:
    assert {store: result.status for store, result in FIXED_RESULTS.items()} == {
        ErasureStore.ANTHROPIC: StoreStatus.RESIDUAL,
        ErasureStore.WORKER_KV: StoreStatus.RESIDUAL,
        ErasureStore.AI_TEST_SETS: StoreStatus.RESIDUAL,
        ErasureStore.NEXTRONAI_INBOX: StoreStatus.RESIDUAL,
        ErasureStore.LOGS: StoreStatus.NONE_HELD,
        ErasureStore.RESTORE_WORK: StoreStatus.NONE_HELD,
        ErasureStore.ERASURE_LEDGER: StoreStatus.NONE_HELD,
    }
    assert FIXED_RESULTS[ErasureStore.WORKER_KV].detail == (
        "Worker KV: token and time only; expires within 30 days"
    )
    assert FIXED_RESULTS[ErasureStore.LOGS].detail == (
        "Logs hold ids and codes only; email addresses are masked when written"
    )
    assert FIXED_RESULTS[ErasureStore.ERASURE_LEDGER].detail == (
        "The ledger holds ids and hashes only"
    )


@pytest.mark.parametrize(
    ("code", "detail"),
    [
        ("telegram_unavailable", "Failed: telegram_unavailable"),
        ("RuntimeError", "Failed: RuntimeError"),
        ("bad code with spaces", "Failed: error"),
        ("x@y", "Failed: error"),
        ("", "Failed: error"),
    ],
)
def test_a_failed_detail_holds_only_a_code(code: str, detail: str) -> None:
    assert receipt.failed(code) == StoreResult(StoreStatus.FAILED, detail)
