"""``nextron.domain.postings`` and ``nextron.domain.signals.match_hiring`` (FR-7, FR-11, FR-49,
INT-10; Story 2.11): the boards a Lead's ATS links name (every token form), which sources the
``ats_hosts`` setting enables, the stored title, the description excerpts and what a careers
page really lists (``validate_postings``). ``match_hiring`` is checked on the shipped Segments:
the Law terms (intake, legal operations, knowledge management, innovation) and the Ops terms
(data entry, order entry, accounts payable, accounts receivable, AP/AR, dispatch)."""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from nextron.domain import seed_yaml
from nextron.domain.extraction import DiscardCause, SentPage
from nextron.domain.postings import (
    ATS_SOURCES,
    BOARD_HOSTS,
    MAX_EXCERPT,
    MAX_TITLE,
    Board,
    CareersPosting,
    PostingDiscard,
    PostingDiscardCause,
    PostingDiscarded,
    PostingSource,
    board_from_link,
    excerpt,
    posting_title,
    source_enabled,
    validate_postings,
)
from nextron.domain.segments import SegmentDef, validate_segments
from nextron.domain.signals import HiringMatch, has_phrase, match_hiring
from tests.contracts.support import ENGINE_ROOT

SEED_FILE = ENGINE_ROOT / "seed" / "segments.yaml"
HOSTS = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
)
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
GREENHOUSE, LEVER, ASHBY = PostingSource.GREENHOUSE, PostingSource.LEVER, PostingSource.ASHBY


def segments() -> dict[str, SegmentDef]:
    found = validate_segments(seed_yaml.parse(SEED_FILE.read_text(encoding="utf-8")))
    return {segment.key: segment for segment in found}


LAW = segments()["law_private_ai"]
OPS = segments()["ops_automation"]


# Boards ------------------------------------------------------------------------------------------


def test_the_board_hosts_are_the_three_providers_read_only() -> None:
    assert dict(BOARD_HOSTS) == {
        "boards.greenhouse.io": GREENHOUSE,
        "job-boards.greenhouse.io": GREENHOUSE,
        "jobs.lever.co": LEVER,
        "jobs.ashbyhq.com": ASHBY,
    }
    assert ATS_SOURCES == (GREENHOUSE, LEVER, ASHBY)
    with pytest.raises(TypeError):
        BOARD_HOSTS["jobs.example.com"] = GREENHOUSE  # type: ignore[index]


@pytest.mark.parametrize(
    ("url", "board"),
    [
        ("https://boards.greenhouse.io/acme/jobs/4012345", Board(GREENHOUSE, "acme")),
        ("https://boards.greenhouse.io/acme", Board(GREENHOUSE, "acme")),
        ("https://job-boards.greenhouse.io/acme-legal/jobs/1", Board(GREENHOUSE, "acme-legal")),
        ("https://boards.greenhouse.io/embed/job_board?for=acme", Board(GREENHOUSE, "acme")),
        ("https://boards.greenhouse.io/embed/job_board/js?for=acme", Board(GREENHOUSE, "acme")),
        (
            "https://boards.greenhouse.io/embed/job_app?for=acme_co&token=4012345",
            Board(GREENHOUSE, "acme_co"),
        ),
        ("http://boards.greenhouse.io/embed/job_board/?for=Acme9", Board(GREENHOUSE, "Acme9")),
        ("https://jobs.lever.co/acme", Board(LEVER, "acme")),
        ("https://jobs.lever.co/acme/5f1c2d3e-aaaa/apply", Board(LEVER, "acme")),
        ("https://jobs.ashbyhq.com/acme", Board(ASHBY, "acme")),
        ("https://jobs.ashbyhq.com/acme/0b1c2d3e?utm_source=site", Board(ASHBY, "acme")),
        ("https://JOBS.LEVER.CO/acme", Board(LEVER, "acme")),  # a host in any case
    ],
)
def test_each_token_form_names_its_board(url: str, board: Board) -> None:
    assert board_from_link(url, HOSTS) == board


@pytest.mark.parametrize(
    "url",
    [
        "https://boards.greenhouse.io/",  # no token
        "https://boards.greenhouse.io/embed/job_board",  # an embed without for=
        "https://boards.greenhouse.io/embed/job_board?for=a&for=b",  # two
        "https://boards.greenhouse.io/embed/job_board?for=",  # empty
        "https://boards.greenhouse.io/embed/other?for=acme",  # embed is no token
        "https://boards.greenhouse.io/embed",
        "https://boards.greenhouse.io/-acme/jobs/1",  # a token starts with a letter or digit
        "https://boards.greenhouse.io/ac.me/jobs/1",
        "https://boards.greenhouse.io/ac%20me/jobs/1",
        "https://boards.greenhouse.io/" + "a" * 101,  # over 100 characters
        "https://jobs.lever.co/",
        "https://jobs.ashbyhq.com",
        "ftp://jobs.lever.co/acme",  # not http(s)
        "https://jobs.eu.lever.co/acme",  # the EU boards aren't read (Decision 3)
        "https://job-boards.eu.greenhouse.io/acme",
        "https://www.indeed.com/cmp/acme/jobs",  # a job site: never a board
        "https://careers.acme.com/jobs",
        "not a url",
        "https://[::1/acme",  # unparsable
    ],
)
def test_anything_else_names_no_board(url: str) -> None:
    assert board_from_link(url, HOSTS) is None


def test_a_token_of_100_characters_is_kept() -> None:
    token = "a" * 100

    assert board_from_link(f"https://jobs.lever.co/{token}", HOSTS) == Board(LEVER, token)


def test_a_host_must_be_in_ats_hosts_and_in_the_board_hosts() -> None:
    url = "https://jobs.lever.co/acme"

    assert board_from_link(url, ("jobs.lever.co",)) == Board(LEVER, "acme")
    assert board_from_link(url, ("boards.greenhouse.io",)) is None  # removed from the setting
    assert board_from_link(url, ()) is None
    # a configured host with no endpoint in code is never read (INT-10)
    assert board_from_link("https://apply.workable.com/acme", ("apply.workable.com",)) is None
    assert board_from_link(url, (" JOBS.LEVER.CO ",)) == Board(LEVER, "acme")
    with pytest.raises(TypeError):
        board_from_link(url, "jobs.lever.co")
    with pytest.raises(TypeError):
        board_from_link(None, HOSTS)  # type: ignore[arg-type]


def test_a_source_is_enabled_by_any_of_its_hosts() -> None:
    assert [source_enabled(source, HOSTS) for source in PostingSource] == [
        False,  # a careers page has no host
        True,
        True,
        True,
    ]
    assert source_enabled(GREENHOUSE, ("job-boards.greenhouse.io",))
    assert not source_enabled(GREENHOUSE, ("jobs.lever.co", "jobs.ashbyhq.com"))
    assert not source_enabled(ASHBY, ("apply.workable.com",))
    with pytest.raises(TypeError):
        source_enabled("lever", HOSTS)  # type: ignore[arg-type]


def test_a_board_is_an_ats_source_and_a_token_and_sorts_by_both() -> None:
    boards = [Board(LEVER, "b"), Board(GREENHOUSE, "z"), Board(LEVER, "a")]

    assert sorted(boards) == [Board(GREENHOUSE, "z"), Board(LEVER, "a"), Board(LEVER, "b")]
    with pytest.raises(TypeError):
        Board(PostingSource.CAREERS_PAGE, "acme")
    with pytest.raises(ValueError):
        Board(LEVER, "a/b")


# Text --------------------------------------------------------------------------------------------


def test_a_title_is_normalised_and_at_most_300_characters() -> None:
    assert posting_title("  Accounts Payable\n Clerk ") == "Accounts Payable Clerk"
    assert posting_title("x" * MAX_TITLE) == "x" * MAX_TITLE
    assert posting_title("x" * (MAX_TITLE + 1)) is None
    assert posting_title(" \n ") is None
    with pytest.raises(TypeError):
        posting_title(None)  # type: ignore[arg-type]


def test_a_short_description_is_its_own_excerpt() -> None:
    assert excerpt(None) is None
    assert excerpt("  \n ") is None
    assert excerpt("We need help with\nclient intake.") == "We need help with client intake."
    assert excerpt("We need help with client intake.", "intake") == (
        "We need help with client intake."
    )
    assert excerpt("x" * MAX_EXCERPT) == "x" * MAX_EXCERPT


def test_a_long_description_is_cut_on_whitespace_at_its_start() -> None:
    words = " ".join(f"word{index:03d}" for index in range(100))  # 799 characters

    found = excerpt(words)

    assert found is not None and len(found) <= MAX_EXCERPT
    assert words.startswith(found)
    assert words[len(found)] == " "  # no word is cut
    assert found.endswith("word036")


def test_a_term_gives_a_window_holding_its_first_whole_phrase_occurrence() -> None:
    words = " ".join(f"word{index:03d}" for index in range(60))
    text = f"{words} The Coordinator runs client intake for new matters. {words} intake again."

    found = excerpt(text, "Client Intake")

    assert found is not None and len(found) <= MAX_EXCERPT
    assert "client intake" in found
    assert found in text
    start = text.index(found)
    assert start == 0 or text[start - 1] == " "  # cut on whitespace at both ends
    end = start + len(found)
    assert end == len(text) or text[end] == " "


def test_a_term_inside_a_longer_word_is_no_occurrence() -> None:
    words = " ".join(f"word{index:03d}" for index in range(60))
    text = f"{words} we automated dispatching last year. {words} We are hiring for dispatch."

    found = excerpt(text, "dispatch")

    assert found is not None and "for dispatch." in found


def test_a_term_that_isnt_found_gives_the_start() -> None:
    words = " ".join(f"word{index:03d}" for index in range(100))

    assert excerpt(words, "intake") == excerpt(words)


def test_a_window_with_no_whitespace_to_cut_on_is_cut_at_its_edge() -> None:
    assert excerpt("x" * 500) == "x" * MAX_EXCERPT
    # a word the window's edge would cut is left out
    assert excerpt(f"{'y' * 400} intake", "intake") == "intake"


# match_hiring ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Intake Specialist",
        "Legal Operations Manager",
        "Knowledge Management Lawyer",
        "Head of Innovation",
        "LEGAL OPERATIONS analyst",  # any case
        "Legal Operations Lead",  # normalised whitespace
    ],
)
def test_the_law_terms_match_in_a_title(title: str) -> None:
    match = match_hiring(title, None, LAW)

    assert match is not None
    assert (match.signal_type.key, match.in_title) == ("law_hiring_legal_ops", True)


@pytest.mark.parametrize(
    ("title", "term"),
    [
        ("Data Entry Clerk", "data entry"),
        ("Order Entry Coordinator", "order entry"),
        ("Accounts Payable Clerk", "accounts payable"),
        ("Accounts Receivable Specialist", "accounts receivable"),
        ("AP/AR Specialist", "AP/AR"),
        ("Dispatch Supervisor", "dispatch"),
        ("Night dispatch lead (ap/ar backup)", "AP/AR"),  # the Segment's order of terms
    ],
)
def test_the_ops_terms_match_in_a_title(title: str, term: str) -> None:
    match = match_hiring(title, None, OPS)

    assert match is not None
    assert (match.signal_type.key, match.in_title, match.term) == (
        "ops_hiring_manual_entry",
        True,
        term,
    )


@pytest.mark.parametrize(
    "title",
    [
        "Dispatcher",  # a term inside a longer word
        "Re-intaker",
        "Innovations Director",
        "Sales Representative",
        "Payable clerk",  # half a phrase
    ],
)
def test_no_whole_phrase_is_no_match(title: str) -> None:
    assert match_hiring(title, None, LAW) is None
    assert match_hiring(title, None, OPS) is None


def test_the_description_is_searched_after_every_title() -> None:
    match = match_hiring("Coordinator", "You will own client intake for new matters.", LAW)

    assert match == HiringMatch(
        next(t for t in LAW.signal_types if t.key == "law_hiring_legal_ops"), False, "intake"
    )
    assert match_hiring("Coordinator", "", LAW) is None
    assert match_hiring("Coordinator", None, LAW) is None
    # the title wins even when the description holds an earlier term
    title_match = match_hiring("Innovation Lead", "intake", LAW)
    assert title_match is not None and (title_match.in_title, title_match.term) == (
        True,
        "innovation",
    )


def test_only_the_segments_hiring_types_are_candidates() -> None:
    # "dispatch" is an Ops term, never a Law one
    assert match_hiring("Dispatch Supervisor", None, LAW) is None
    assert match_hiring("Intake Specialist", None, OPS) is None


def test_match_hiring_refuses_the_wrong_types() -> None:
    with pytest.raises(TypeError):
        match_hiring(None, None, LAW)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        match_hiring("Clerk", 7, LAW)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        match_hiring("Clerk", None, "law_private_ai")  # type: ignore[arg-type]


def test_has_phrase_needs_no_letter_or_digit_around_it() -> None:
    assert has_phrase("an ap/ar specialist", "ap/ar")
    assert not has_phrase("xap/ar", "ap/ar")
    assert not has_phrase("dispatcher", "dispatch")
    assert has_phrase("dispatch.", "dispatch")
    assert not has_phrase("anything", "")


# validate_postings -------------------------------------------------------------------------------

CAREERS, TEAM = "01KA0000000000000000000C01", "01KA0000000000000000000C02"
CAREERS_URL, TEAM_URL = "https://smith-law.com/careers", "https://smith-law.com/team"
CAREERS_TEXT = (
    "Join Smith Law\n"
    "Legal Operations Manager\n"
    "Posted 10 November 2026. You will run our client intake.\n"
    "Paralegal (Leeds)"
)
PAGES = {
    CAREERS: SentPage(CAREERS_URL, NOW, CAREERS_TEXT),
    TEAM: SentPage(TEAM_URL, NOW, "Our team. Legal Operations Manager: Jane Roe."),
}


def posting(
    title: str,
    block_id: str = CAREERS,
    quote: str = "Legal Operations Manager",
    posted: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    return {"title": title, "url": url, "posted_date": posted, "block_id": block_id, "quote": quote}


def run(*items: dict[str, Any]) -> Any:
    return validate_postings({"job_postings": list(items)}, PAGES, (CAREERS,), NOW)


def test_a_title_on_a_careers_page_is_kept_with_the_pages_url() -> None:
    found = run(
        posting(
            " Legal  Operations Manager ",
            quote="Posted 10 November 2026. You will run our client intake.",
            posted="2026-11-10",
            url="https://elsewhere.example/jobs/1",  # the model's url is ignored
        )
    )

    assert found.postings == (
        CareersPosting(
            CAREERS,
            CAREERS_URL,
            "Legal Operations Manager",
            "Posted 10 November 2026. You will run our client intake.",
            date(2026, 11, 10),
        ),
    )
    assert (found.discards, found.dropped_dates) == ((), ())


POSTING, DATE = PostingDiscarded.POSTING, PostingDiscarded.DATE


@pytest.mark.parametrize(
    ("item", "cause", "cited"),
    [
        (posting("Legal Operations Manager", block_id="signal_types"), "unknown_block", None),
        (posting("Legal Operations Manager", block_id=TEAM), "not_careers_page", TEAM),
        (posting(" \n "), "title_empty", CAREERS),
        (posting("x" * 301), "title_too_long", CAREERS),
        (posting("Chief Innovation Officer"), "title_not_in_page", CAREERS),  # not in the text
        (posting("legal operations manager"), "title_not_in_page", CAREERS),  # case counts
    ],
)
def test_each_refusal_is_one_discard_with_its_cause(
    item: dict[str, Any], cause: str, cited: str | None
) -> None:
    found = run(item)

    assert found.postings == ()
    assert found.discards == (PostingDiscard(POSTING, PostingDiscardCause(cause), cited),)


def test_a_repeat_of_the_same_page_and_title_is_dropped_silently() -> None:
    found = run(
        posting("Legal Operations Manager"),
        posting("Legal Operations\nManager", quote="not on the page", posted="2026-11-10"),
        posting("Paralegal (Leeds)", quote="Paralegal (Leeds)"),
    )

    assert [p.title for p in found.postings] == ["Legal Operations Manager", "Paralegal (Leeds)"]
    assert (found.discards, found.dropped_dates) == ((), ())


def test_a_quote_not_in_the_page_or_too_long_leaves_no_description() -> None:
    found = run(
        posting("Legal Operations Manager", quote="We are hiring someone great."),
        posting("Paralegal (Leeds)", quote="Paralegal (Leeds) " + "x" * 300),
    )

    assert [(p.title, p.description) for p in found.postings] == [
        ("Legal Operations Manager", None),
        ("Paralegal (Leeds)", None),
    ]


@pytest.mark.parametrize(
    ("quote", "posted", "cause"),
    [
        ("Legal Operations Manager", "2026-11-10", DiscardCause.DATE_NOT_IN_QUOTE),
        ("not on the page", "2026-11-10", DiscardCause.DATE_NOT_IN_QUOTE),  # no description
        (
            "Posted 10 November 2026. You will run our client intake.",
            "2026-11-11",
            DiscardCause.DATE_NOT_IN_QUOTE,
        ),
    ],
)
def test_a_posted_date_the_description_doesnt_hold_is_dropped(
    quote: str, posted: str, cause: DiscardCause
) -> None:
    found = run(posting("Legal Operations Manager", quote=quote, posted=posted))

    [kept] = found.postings
    assert kept.posted_date is None
    assert found.dropped_dates == (PostingDiscard(DATE, cause, CAREERS),)
    assert found.discards == ()


def test_a_posted_date_in_the_future_is_dropped() -> None:
    text = "Legal Operations Manager\nStarts 1 December 2026."
    pages = {CAREERS: SentPage(CAREERS_URL, NOW, text)}
    item = posting("Legal Operations Manager", quote="Starts 1 December 2026.", posted="2026-12-01")

    found = validate_postings({"job_postings": [item]}, pages, (CAREERS,), NOW)

    assert found.postings[0].posted_date is None
    assert found.dropped_dates == (PostingDiscard(DATE, DiscardCause.DATE_IN_FUTURE, CAREERS),)


def test_an_answer_without_the_schemas_shape_raises() -> None:
    with pytest.raises(ValueError):
        validate_postings({}, PAGES, (CAREERS,), NOW)
    with pytest.raises(ValueError):
        validate_postings({"job_postings": [{"title": 1}]}, PAGES, (CAREERS,), NOW)
    with pytest.raises(ValueError):
        run(posting("Legal Operations Manager", posted="10 November"))
    with pytest.raises(TypeError):
        validate_postings({"job_postings": []}, PAGES, CAREERS, NOW)
    with pytest.raises(TypeError):
        validate_postings({"job_postings": []}, PAGES, (CAREERS,), NOW.replace(tzinfo=None))


def test_no_text_shows_in_a_repr() -> None:
    [kept] = run(posting("Legal Operations Manager")).postings

    assert "Legal Operations" not in repr(kept)
