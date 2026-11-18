"""``AtsBoards`` (``nextron.adapters.ats``, Story 2.11) on a real ``HttpCrawler`` over
``httpx2.MockTransport``, with a fake resolver and a fake monotonic clock; nothing here reaches
the network.

It covers each provider's JSON (Greenhouse, Lever and Ashby) mapped to ``BoardPosting``s, the
entries skipped (no title, no absolute URL, an unlisted Ashby job), the dates (UTC, unreadable,
in the future) and the description cap, every error code (404 and 410, 429 and 5xx, any other
status, a body that isn't JSON or isn't the provider's shape, a media type that isn't JSON, a
board with no endpoint), one ``ats`` ``ApiCall`` per request, the fixed endpoint with its site
pin (a redirect off the provider's domain is refused) and the live fetcher the guard refuses."""

import html
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx2
import pytest

import nextron.adapters.ats as adapter
from nextron.adapters.ats import MAX_DESCRIPTION, AtsBoards
from nextron.adapters.crawler import HttpCrawler
from nextron.domain.budget import ApiCall
from nextron.domain.postings import Board, PostingSource
from nextron.ports.errors import AdapterError
from nextron.ports.job_board import BoardListing, BoardPosting, JobBoardPort
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import LiveAdapterInTests

PUBLIC = "93.184.216.34"
JSON = "application/json; charset=utf-8"
GREENHOUSE_HOST = "boards-api.greenhouse.io"
LEVER_HOST = "api.lever.co"
ASHBY_HOST = "api.ashbyhq.com"
ACME_GREENHOUSE = Board(PostingSource.GREENHOUSE, "acme")
ACME_LEVER = Board(PostingSource.LEVER, "acme")
ACME_ASHBY = Board(PostingSource.ASHBY, "acme")

type Answer = httpx2.Response | BaseException | Callable[[httpx2.Request], httpx2.Response]


@dataclass
class Web:
    """A fake web: answers by ``(Host header, path)``; records every request."""

    answers: dict[tuple[str, str], Answer] = field(default_factory=dict)
    requests: list[httpx2.Request] = field(default_factory=list)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        key = (request.headers["host"], request.url.raw_path.decode("ascii").split("?")[0])
        answer = self.answers.get(key)
        if answer is None:
            return httpx2.Response(404, headers={"content-type": JSON}, text="{}")
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return answer(request)
        return answer

    def on(self, host: str, path: str, answer: Answer) -> None:
        self.answers[(host, path)] = answer


@dataclass
class World:
    web: Web
    meter: InMemoryUsageMeter
    boards: AtsBoards
    slept: list[float]


@pytest.fixture
def world() -> World:
    web, meter, slept = Web(), InMemoryUsageMeter(), []
    now = [1000.0]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    crawler = HttpCrawler(
        meter=meter,
        http=httpx2.Client(transport=httpx2.MockTransport(web)),
        resolve=lambda host, port: (PUBLIC,),
        own_addresses=frozenset(),
        monotonic=lambda: now[0],
        sleep=sleep,
    )
    return World(web, meter, AtsBoards(meter=meter, fetcher=crawler), slept)


def answer(body: Any, status: int = 200, ctype: str = JSON, **headers: str) -> httpx2.Response:
    text = body if isinstance(body, str) else json.dumps(body)
    return httpx2.Response(status, headers={"content-type": ctype, **headers}, text=text)


def greenhouse(world: World, body: Any, **kwargs: Any) -> None:
    world.web.on(GREENHOUSE_HOST, "/v1/boards/acme/jobs", answer(body, **kwargs))


def lever(world: World, body: Any, **kwargs: Any) -> None:
    world.web.on(LEVER_HOST, "/v0/postings/acme", answer(body, **kwargs))


def ashby(world: World, body: Any, **kwargs: Any) -> None:
    world.web.on(ASHBY_HOST, "/posting-api/job-board/acme", answer(body, **kwargs))


def board_calls(world: World) -> list[ApiCall]:
    return [call for call in world.meter.calls if isinstance(call, ApiCall)]


def test_ats_boards_satisfy_the_port() -> None:
    assert isinstance(AtsBoards(meter=InMemoryUsageMeter()), JobBoardPort)


# Each provider -----------------------------------------------------------------------------------


def test_a_greenhouse_board_maps_each_job(world: World) -> None:
    content = html.escape(
        "<h2>About the role</h2><p>You will own <strong>accounts payable</strong>.</p>"
        '<p style="display:none">hidden</p>'
    )
    greenhouse(
        world,
        {
            "jobs": [
                {
                    "title": "Accounts Payable Clerk",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                    "first_published": "2026-11-10T22:30:00-05:00",  # 03:30 UTC on the 11th
                    "content": content,
                },
                {
                    "title": "Sales Rep",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
                    "first_published": None,
                },
            ],
            "meta": {"total": 2},
        },
    )

    listing = world.boards.postings(ACME_GREENHOUSE)

    assert listing == BoardListing(
        (
            BoardPosting(
                "Accounts Payable Clerk",
                "https://boards.greenhouse.io/acme/jobs/1",
                date(2026, 11, 11),
                "About the role\nYou will own accounts payable.",
            ),
            BoardPosting("Sales Rep", "https://boards.greenhouse.io/acme/jobs/2", None, None),
        )
    )
    [request] = world.web.requests
    assert request.headers["host"] == GREENHOUSE_HOST
    assert request.url.query == b"content=true"
    assert request.headers["accept"] == "application/json"
    assert board_calls(world) == [ApiCall("ats", "board", ok=True)]


def test_a_lever_board_maps_each_posting(world: World) -> None:
    created = int(datetime(2026, 11, 10, 23, 59, tzinfo=UTC).timestamp() * 1000)
    lever(
        world,
        [
            {
                "text": "Dispatch Supervisor",
                "hostedUrl": "https://jobs.lever.co/acme/5f1c",
                "createdAt": created,
                "descriptionPlain": "Run our dispatch desk.",
                "additionalPlain": "Nights and weekends.",
            },
            {"text": "Driver", "hostedUrl": "https://jobs.lever.co/acme/6a2d", "createdAt": "x"},
        ],
    )

    listing = world.boards.postings(ACME_LEVER)

    assert listing.postings == (
        BoardPosting(
            "Dispatch Supervisor",
            "https://jobs.lever.co/acme/5f1c",
            date(2026, 11, 10),
            "Run our dispatch desk.\nNights and weekends.",
        ),
        BoardPosting("Driver", "https://jobs.lever.co/acme/6a2d", None, None),
    )
    [request] = world.web.requests
    assert request.headers["host"] == LEVER_HOST
    assert request.url.query == b"mode=json"
    assert board_calls(world) == [ApiCall("ats", "board", ok=True)]


def test_an_ashby_board_maps_each_listed_job(world: World) -> None:
    ashby(
        world,
        {
            "apiVersion": "1",
            "jobs": [
                {
                    "title": "Data Entry Specialist",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/0b1c",
                    "publishedAt": "2026-11-09T08:00:00.000+00:00",
                    "descriptionPlain": "Key orders into our ERP.",
                    "isListed": True,
                },
                {
                    "title": "Secret Role",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/9z9z",
                    "isListed": False,  # unlisted: skipped
                },
                {"title": "Office Manager", "jobUrl": "https://jobs.ashbyhq.com/acme/1c2d"},
            ],
        },
    )

    listing = world.boards.postings(ACME_ASHBY)

    assert listing.postings == (
        BoardPosting(
            "Data Entry Specialist",
            "https://jobs.ashbyhq.com/acme/0b1c",
            date(2026, 11, 9),
            "Key orders into our ERP.",
        ),
        BoardPosting("Office Manager", "https://jobs.ashbyhq.com/acme/1c2d", None, None),
    )
    assert world.web.requests[0].headers["host"] == ASHBY_HOST


@pytest.mark.parametrize(
    "job",
    [
        {"absolute_url": "https://boards.greenhouse.io/acme/jobs/1"},  # no title
        {"title": "  ", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1"},
        {"title": 7, "absolute_url": "https://boards.greenhouse.io/acme/jobs/1"},
        {"title": "Clerk"},  # no URL
        {"title": "Clerk", "absolute_url": "/acme/jobs/1"},  # not absolute
        {"title": "Clerk", "absolute_url": "mailto:jobs@acme.com"},
        {"title": "Clerk", "absolute_url": "https://boards.greenhouse.io/a b"},
        {"title": "Clerk", "absolute_url": "https://boards.greenhouse.io/" + "x" * 2048},
        "not an object",
    ],
)
def test_an_entry_without_a_title_or_an_absolute_url_is_skipped(world: World, job: Any) -> None:
    greenhouse(world, {"jobs": [job]})

    assert world.boards.postings(ACME_GREENHOUSE) == BoardListing(())


@pytest.mark.parametrize(
    "published",
    [
        "2999-01-01T00:00:00Z",
        "yesterday",
        "2026-13-01T00:00:00Z",
        1731196800000,
        "",
        "0001-01-01T00:00:00+05:00",  # before year 1 in UTC: the conversion overflows
    ],
)
def test_a_future_or_unreadable_date_is_none(world: World, published: Any) -> None:
    greenhouse(
        world,
        {
            "jobs": [
                {
                    "title": "Clerk",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                    "first_published": published,
                }
            ]
        },
    )

    [found] = world.boards.postings(ACME_GREENHOUSE).postings
    assert found.posted_date is None


def test_a_naive_or_plain_date_reads_as_that_utc_date(world: World) -> None:
    ashby(
        world,
        {
            "jobs": [
                {
                    "title": "A",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                    "publishedAt": "2026-11-09",
                },
                {
                    "title": "B",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/2",
                    "publishedAt": "2026-11-09T23:00:00",
                },
            ]
        },
    )

    assert [p.posted_date for p in world.boards.postings(ACME_ASHBY).postings] == [
        date(2026, 11, 9),
        date(2026, 11, 9),
    ]


@pytest.mark.parametrize("created", [10**20, -(10**20), float("nan"), True])
def test_a_lever_date_out_of_range_is_none(world: World, created: Any) -> None:
    body = [{"text": "Driver", "hostedUrl": "https://jobs.lever.co/acme/1", "createdAt": 0}]
    text = json.dumps(body).replace('"createdAt": 0', f'"createdAt": {json.dumps(created)}')
    lever(world, text)

    [found] = world.boards.postings(ACME_LEVER).postings
    assert found.posted_date is None


def test_a_description_is_cut_to_20000_characters(world: World) -> None:
    long = "word " * 5000  # 25,000 characters
    ashby(
        world,
        {
            "jobs": [
                {
                    "title": "Clerk",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                    "descriptionPlain": long,
                }
            ]
        },
    )

    [found] = world.boards.postings(ACME_ASHBY).postings
    assert found.description is not None and len(found.description) == MAX_DESCRIPTION


# Errors ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (404, "board_not_found", False),
        (410, "board_not_found", False),
        (429, "board_unavailable", True),
        (500, "board_unavailable", True),
        (503, "board_unavailable", True),
        (403, "board_failed", False),
        (400, "board_failed", False),
    ],
)
def test_each_status_is_its_code(world: World, status: int, code: str, retryable: bool) -> None:
    greenhouse(world, {"jobs": []}, status=status)

    with pytest.raises(AdapterError) as raised:
        world.boards.postings(ACME_GREENHOUSE)

    assert (raised.value.code, raised.value.retryable) == (code, retryable)
    # a non-2xx answer is still one request, metered as it ended (the request returned)
    assert board_calls(world) == [ApiCall("ats", "board", ok=True)]


@pytest.mark.parametrize(
    ("board", "body"),
    [
        (ACME_GREENHOUSE, "not json {"),
        (ACME_GREENHOUSE, "[]"),  # a list, not an object
        (ACME_GREENHOUSE, '{"jobs": {}}'),
        (ACME_GREENHOUSE, '{"meta": {}}'),
        (ACME_LEVER, '{"postings": []}'),  # an object, not a list
        (ACME_ASHBY, "[]"),
        (ACME_ASHBY, '{"jobs": null}'),
        (ACME_ASHBY, "[" * 100_000),  # nested too deep
    ],
)
def test_an_answer_that_isnt_the_providers_shape_is_invalid(
    world: World, board: Board, body: str
) -> None:
    for each in (greenhouse, lever, ashby):
        each(world, body)

    with pytest.raises(AdapterError) as raised:
        world.boards.postings(board)

    assert (raised.value.code, raised.value.retryable) == ("board_invalid", False)
    assert body not in str(raised.value)


def test_a_byte_order_mark_is_dropped(world: World) -> None:
    lever(world, "﻿[]")

    assert world.boards.postings(ACME_LEVER) == BoardListing(())


def test_a_media_type_that_isnt_json_is_refused(world: World) -> None:
    greenhouse(world, "<html></html>", ctype="text/html")
    lever(world, "[]", ctype="application/vnd.lever+json")

    with pytest.raises(AdapterError) as raised:
        world.boards.postings(ACME_GREENHOUSE)

    assert raised.value.code == "fetch_unsupported_type"
    assert world.boards.postings(ACME_LEVER) == BoardListing(())  # a +json type is JSON


def test_a_timeout_passes_through_retryable(world: World) -> None:
    greenhouse(world, {})
    world.web.on(GREENHOUSE_HOST, "/v1/boards/acme/jobs", httpx2.ReadTimeout("slow"))

    with pytest.raises(AdapterError) as raised:
        world.boards.postings(ACME_GREENHOUSE)

    assert (raised.value.code, raised.value.retryable) == ("fetch_timeout", True)
    assert board_calls(world) == [ApiCall("ats", "board", ok=False, error_code="fetch_timeout")]


def test_a_redirect_off_the_providers_domain_is_refused_after_one_request(world: World) -> None:
    world.web.on(
        GREENHOUSE_HOST,
        "/v1/boards/acme/jobs",
        httpx2.Response(302, headers={"location": "https://evil.example/jobs.json"}),
    )

    with pytest.raises(AdapterError) as raised:
        world.boards.postings(ACME_GREENHOUSE)

    assert raised.value.code == "fetch_refused" and "off_site" in raised.value.message
    assert len(world.web.requests) == 1


def test_a_redirect_within_the_providers_domain_is_followed_and_each_hop_metered(
    world: World,
) -> None:
    world.web.on(
        GREENHOUSE_HOST,
        "/v1/boards/acme/jobs",
        httpx2.Response(301, headers={"location": "https://api.greenhouse.io/v1/boards/acme"}),
    )
    world.web.on("api.greenhouse.io", "/v1/boards/acme", answer({"jobs": []}))

    assert world.boards.postings(ACME_GREENHOUSE) == BoardListing(())
    assert board_calls(world) == [ApiCall("ats", "board", ok=True)] * 2
    assert world.slept  # paced at 1 s per domain


def test_a_board_with_no_endpoint_is_refused_before_any_request(world: World) -> None:
    with pytest.raises(AdapterError) as raised:
        world.boards.postings("acme")  # type: ignore[arg-type]

    assert raised.value.code == "board_failed"
    assert world.web.requests == [] and board_calls(world) == []


def test_no_token_or_posting_text_reaches_an_error(world: World) -> None:
    secret_board = Board(PostingSource.LEVER, "Qz7Lm2Vx9Kp4Tw8Nr3Hs")
    world.web.on(LEVER_HOST, "/v0/postings/Qz7Lm2Vx9Kp4Tw8Nr3Hs", answer("{oops", status=200))

    with pytest.raises(AdapterError) as raised:
        world.boards.postings(secret_board)

    assert "Qz7Lm2Vx9Kp4Tw8Nr3Hs" not in str(raised.value)
    assert "oops" not in str(raised.value)


def test_fetch_json_refuses_a_denylisted_url_before_any_request(world: World) -> None:
    with pytest.raises(AdapterError) as raised:
        world.boards.fetch_json("https://www.indeed.com/jobs.json", site=None)

    assert raised.value.code == "fetch_refused"
    assert world.web.requests == [] and board_calls(world) == []


def test_no_text_shows_in_a_postings_repr() -> None:
    found = BoardPosting("Legal Ops", "https://jobs.lever.co/acme/1", None, "Run intake.")

    assert "Legal Ops" not in repr(found) and "Run intake" not in repr(found)
    assert "jobs.lever.co" not in repr(found)


# The live fetcher ---------------------------------------------------------------------------------


def test_with_no_fetcher_the_live_one_is_looked_up_on_the_first_call() -> None:
    boards = AtsBoards(meter=InMemoryUsageMeter())  # builds nothing

    with pytest.raises(LiveAdapterInTests):
        boards.postings(ACME_LEVER)
    with pytest.raises(LiveAdapterInTests):
        adapter.default_fetcher(InMemoryUsageMeter())
