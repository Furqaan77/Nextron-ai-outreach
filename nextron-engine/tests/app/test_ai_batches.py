"""Bulk research AI work through Message Batches (Story 4.17, AD-8, AD-18, FR-57): the I/O
matrix end to end on Story 2.9's research world (a scripted crawler, the shipped prompts and a
temporary database), with ``ScriptedLlm`` for the port; and once through the real
``ClaudeGateway`` on a mock transport, for the batch-rate metering. Nothing reaches Claude."""

import json
import logging
from datetime import time, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.adapters.ai import ClaudeGateway
from nextron.app.compliance.api import Compliance
from nextron.app.research.batches import BulkAiSchedule, ResearchBatches
from nextron.app.research.extraction import SignalExtractions
from nextron.app.research.people import PeopleExtractions
from nextron.app.setup.api import Setup
from nextron.db.metering import UsageMeter
from nextron.domain.ai import Urgency
from nextron.domain.ai_batch import Fingerprint, custom_id
from nextron.domain.contactability import DomainSubject, SuppressionScope
from nextron.domain.job import Lane
from nextron.domain.suppression_entry import SuppressionSource
from nextron.jobs.triggers import DailyRun
from nextron.ports.errors import AdapterError
from nextron.ports.llm import (
    BatchLost,
    BatchLostKind,
    BatchSucceeded,
    LlmInvalid,
    LlmOk,
    LlmRefused,
    LlmTruncated,
    LlmUsage,
)
from tests.app.test_people_extraction import LAW_HIRING, law_lead, law_people, people_runs
from tests.app.test_signal_extraction import (
    ABOUT,
    HAIKU,
    NO_PEOPLE,
    T0,
    TEAM,
    World,
    build_world,
    ok,
    signal,
    spend_the_budget,
    utc,
)
from tests.fakes.llm import ScriptedLlm
from tests.fakes.secrets import InMemorySecrets
from tests.unit.test_ai_batch_gateway import message_json

SIGNALS = "extract_signals"
PEOPLE = "extract_people"
USAGE = LlmUsage(requests=1)


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


class Services:
    """The research steps and their batches on one port."""

    def __init__(self, world: World, llm: Any, *, meter: Any = None) -> None:
        self.world = world
        self.llm = llm
        meter = world.meter if meter is None else meter
        self.people = PeopleExtractions(world.clock, meter, llm, world.research, world.queue)
        self.extractions = SignalExtractions(world.clock, meter, llm, world.research, self.people)
        self.batches = ResearchBatches(world.clock, meter, llm, self.extractions, self.people)

    def extract(self, lead: str, run_id: str, lane: Lane | None = Lane.BULK) -> bool:
        with self.world.units.open():
            return self.extractions.extract_lead(lead, run_id, lane=lane)

    def submit(self) -> int:
        with self.world.units.open():
            return self.batches.submit_bulk()

    def poll(self) -> int:
        with self.world.units.open():
            return self.batches.poll()

    def schedule(self) -> BulkAiSchedule:
        with self.world.units.open(read_only=True):
            return self.batches.schedule()


def items(world: World) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT owner, route, custom_id, target_id, rev, context_id, status, batch_row_id "
        "FROM ai_batch_items ORDER BY rowid"
    )


def statuses(world: World) -> dict[str, str]:
    return {row[1]: row[6] for row in items(world)}


def batches(world: World) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT owner, provider_batch_id, route, route_tuple_key, status, submitted_at, ended_at, "
        "deleted_at FROM ai_batches ORDER BY rowid"
    )


def signal_runs(world: World, lead: str) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT outcome, started_at FROM extraction_runs WHERE lead_id = ? "
        "AND route = 'extract_signals' ORDER BY rowid",
        (lead,),
    )


def queued(world: World, services: Services) -> tuple[str, str, dict[str, str]]:
    """A Law Lead crawled at ``T0`` whose two steps queued their items on the bulk lane."""
    lead, run_id, pages = law_lead(world)
    assert services.extract(lead, run_id) is True
    return lead, run_id, pages


def submitted(world: World, services: Services) -> tuple[str, str, dict[str, str]]:
    """``queued``, then the daily submit an hour later: one batch per route."""
    lead, run_id, pages = queued(world, services)
    world.clock.advance(timedelta(hours=1))
    assert services.submit() == 2
    return lead, run_id, pages


def law_signals(pages: dict[str, str]) -> LlmOk:
    """The Signals answer that validates on the Law Lead's pages: one hiring quote."""
    return ok(signals=[signal(pages[ABOUT], LAW_HIRING)])


def ids(lead: str) -> tuple[str, str]:
    return custom_id(SIGNALS, lead, 1), custom_id(PEOPLE, lead, 1)


# Row: Queue and Direct --------------------------------------------------------------------------


def test_the_bulk_lane_queues_one_pending_item_per_step_and_calls_nothing(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.research")
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, run_id, _ = queued(world, services)

    assert llm.calls == [] and llm.batches_submitted == []
    assert items(world) == [
        ("research", SIGNALS, f"{SIGNALS}_{lead}_1", lead, 1, run_id, "pending", None),
        ("research", PEOPLE, f"{PEOPLE}_{lead}_1", lead, 1, run_id, "pending", None),
    ]
    assert world.runs(lead) == [] and people_runs(world, lead) == []
    assert world.jobs("check_job_boards") == []  # the chain goes on after the item's run
    logged = [r for r in caplog.records if r.message == "extraction_queued"]
    assert [(r.lead_id, r.route, r.queued) for r in logged] == [  # type: ignore[attr-defined]
        (lead, SIGNALS, True),
        (lead, PEOPLE, True),
    ]
    # the same crawl queued again adds nothing
    assert services.extract(lead, run_id) is True
    assert len(items(world)) == 2


def test_a_budget_refusal_defers_the_job_and_queues_nothing(world: World) -> None:
    services = Services(world, ScriptedLlm())
    lead, run_id, _ = law_lead(world)
    spend_the_budget(world)

    assert services.extract(lead, run_id) is False
    assert items(world) == []


def test_the_interactive_lane_calls_directly_at_soon_and_queues_nothing(world: World) -> None:
    lead, run_id, pages = law_lead(world)
    llm = ScriptedLlm(law_signals(pages), law_people(pages))
    services = Services(world, llm)

    assert services.extract(lead, run_id, lane=Lane.INTERACTIVE) is True

    assert [(t.route, u) for t, _, u in llm.calls] == [
        (SIGNALS, Urgency.SOON),
        (PEOPLE, Urgency.SOON),
    ]
    assert items(world) == [] and llm.batches_submitted == []
    assert world.runs(lead)[0][0] == "ok"


# Row: Submit ------------------------------------------------------------------------------------


def test_with_nothing_pending_the_submit_sends_no_batch(world: World) -> None:
    llm = ScriptedLlm()

    assert Services(world, llm).submit() == 0
    assert llm.batches_submitted == [] and batches(world) == []


def test_the_submit_sends_one_batch_per_route_with_the_requests_rebuilt_now(world: World) -> None:
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, run_id, pages = submitted(world, services)
    signals_id, people_id = ids(lead)

    (signals_tuple, signals_items), (people_tuple, people_items) = llm.batches_submitted
    assert (signals_tuple.route, people_tuple.route) == (SIGNALS, PEOPLE)
    [signals_item] = signals_items
    [people_item] = people_items
    assert (signals_item.custom_id, people_item.custom_id) == (signals_id, people_id)
    assert {block.subject for block in signals_item.inputs} == {f"lead:{lead}"}
    assert [block.block_id for block in people_item.inputs] == [pages[TEAM], pages[ABOUT]]
    at = utc(T0 + timedelta(hours=1))
    assert batches(world) == [
        ("research", "msgbatch_fake_1", SIGNALS, signals_tuple.key, "submitted", at, None, None),
        ("research", "msgbatch_fake_2", PEOPLE, people_tuple.key, "submitted", at, None, None),
    ]
    assert statuses(world) == {SIGNALS: "submitted", PEOPLE: "submitted"}
    [(fingerprint,)] = world.sql(
        "SELECT fingerprint FROM ai_batch_items WHERE route = 'extract_people'"
    )
    hashes = dict(
        world.sql("SELECT id, content_hash FROM crawled_pages WHERE lead_id = ?", (lead,))
    )
    document = json.loads(fingerprint)
    assert document["route_tuple"] == people_tuple.key
    assert document["inputs"] == sorted(
        [[pages[url], hashes[pages[url]]] for url in (TEAM, ABOUT)]
    )
    assert world.runs(lead) == []  # nothing recorded until the results come back
    assert run_id


def test_a_spent_budget_keeps_every_item_pending_and_sends_nothing(world: World) -> None:
    llm = ScriptedLlm()
    services = Services(world, llm)
    queued(world, services)
    spend_the_budget(world)

    assert services.submit() == 0
    assert llm.batches_submitted == []
    assert statuses(world) == {SIGNALS: "pending", PEOPLE: "pending"}


def test_an_older_pending_item_of_the_same_lead_goes_stale_and_only_the_newest_is_sent(
    world: World,
) -> None:
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, _, _ = queued(world, services)
    world.clock.advance(timedelta(days=2))
    second = world.crawl(lead)
    assert services.extract(lead, second) is True

    assert services.submit() == 2

    rows = items(world)
    assert [(r[1], r[4], r[6]) for r in rows] == [
        (SIGNALS, 1, "stale"),
        (PEOPLE, 1, "stale"),
        (SIGNALS, 2, "submitted"),
        (PEOPLE, 2, "submitted"),
    ]
    assert [i.custom_id for _, sent in llm.batches_submitted for i in sent] == [
        custom_id(SIGNALS, lead, 2),
        custom_id(PEOPLE, lead, 2),
    ]


def test_a_lead_with_nothing_new_records_its_no_pages_run_and_its_item_goes_stale(
    world: World,
) -> None:
    lead, run_id, pages = law_lead(world)
    direct = Services(world, ScriptedLlm(law_signals(pages), law_people(pages)))
    assert direct.extract(lead, run_id, lane=Lane.INTERACTIVE) is True
    world.clock.advance(timedelta(hours=1))
    llm = ScriptedLlm()
    services = Services(world, llm)
    second = world.crawl(lead)  # the same site: no page changed
    assert services.extract(lead, second) is True

    assert services.submit() == 0

    assert llm.batches_submitted == []
    assert statuses(world) == {SIGNALS: "stale", PEOPLE: "stale"}
    assert [row[0] for row in world.runs(lead)] == ["ok", "no_pages"]
    assert [row[0] for row in people_runs(world, lead)] == ["ok", "no_pages"]


# Row: Applied, Re-poll and Settled --------------------------------------------------------------


def test_an_ended_batch_applies_each_result_then_deletes_the_batch_and_a_re_poll_changes_nothing(
    world: World,
) -> None:
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, _, pages = submitted(world, services)
    signals_id, people_id = ids(lead)
    assert services.poll() == 0  # still in progress: nothing settled, nothing deleted
    llm.end_batch("msgbatch_fake_1", {signals_id: BatchSucceeded(law_signals(pages))})
    llm.end_batch("msgbatch_fake_2", {people_id: BatchSucceeded(law_people(pages))})
    world.clock.advance(timedelta(hours=1))

    assert services.poll() == 2

    assert statuses(world) == {SIGNALS: "applied", PEOPLE: "applied"}
    submitted_at = utc(T0 + timedelta(hours=1))
    assert signal_runs(world, lead) == [("ok", submitted_at)]
    assert [row[0] for row in people_runs(world, lead)] == ["ok"]
    extracted = [row["extracted_by"] for row in world.evidence(lead)]
    assert "ai:extract_signals@v1" in extracted and "ai:extract_people@v1" in extracted
    assert world.sql("SELECT COUNT(*) FROM contacts WHERE lead_id = ?", (lead,)) == [(1,)]
    assert len(world.jobs("check_job_boards")) == 1  # the people run continues the chain
    done = utc(T0 + timedelta(hours=2))
    assert [(b[4], b[6], b[7]) for b in batches(world)] == [("deleted", done, done)] * 2
    assert llm.deleted == ["msgbatch_fake_1", "msgbatch_fake_2"]
    assert llm.calls == []
    fetched = list(llm.fetched)

    assert services.poll() == 0  # a deleted batch is never fetched again

    assert llm.fetched == fetched and llm.deleted == ["msgbatch_fake_1", "msgbatch_fake_2"]
    assert statuses(world) == {SIGNALS: "applied", PEOPLE: "applied"}
    assert signal_runs(world, lead) == [("ok", submitted_at)]


def test_an_ended_batch_whose_delete_failed_is_deleted_on_the_next_poll_without_a_fetch(
    world: World,
) -> None:
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, _, pages = submitted(world, services)
    signals_id, people_id = ids(lead)
    llm.end_batch("msgbatch_fake_1", {signals_id: BatchSucceeded(law_signals(pages))})
    llm.end_batch("msgbatch_fake_2", {people_id: BatchSucceeded(NO_PEOPLE)})
    llm.delete_errors.append(AdapterError(True, "llm_unavailable", "down"))

    with pytest.raises(AdapterError):
        services.poll()
    assert statuses(world)[SIGNALS] == "applied"
    assert batches(world)[0][4] == "ended"
    fetched = len(llm.fetched)

    services.poll()

    assert statuses(world) == {SIGNALS: "applied", PEOPLE: "applied"}
    assert [b[4] for b in batches(world)] == ["deleted", "deleted"]
    assert len(llm.fetched) == fetched + 1  # only the people batch, which hadn't been fetched
    assert signal_runs(world, lead)[0][0] == "ok" and len(signal_runs(world, lead)) == 1


# Row: Stale -------------------------------------------------------------------------------------


def changed_page(world: World, page_id: str) -> None:
    world.sql("UPDATE crawled_pages SET content_hash = ? WHERE id = ?", ("f" * 64, page_id))


@pytest.mark.parametrize("why", ["page_changed", "merged", "suppressed", "newer_run"])
def test_a_result_that_no_longer_fits_is_stale_and_writes_nothing(world: World, why: str) -> None:
    older = world.lead(web_domain="smith-law-llp.com")  # a merge's survivor is the older Lead
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, run_id, pages = submitted(world, services)
    signals_id, people_id = ids(lead)
    if why == "page_changed":
        for page_id in pages.values():
            changed_page(world, page_id)
    elif why == "merged":
        world.transact(lambda: world.discovery.merge(older, lead))
    elif why == "suppressed":
        world.transact(
            lambda: Compliance(world.clock).suppress(
                DomainSubject("smith-law.com"),
                SuppressionScope.FIRM,
                SuppressionSource.OPERATOR,
                None,
            )
        )
    else:  # an interactive run of both steps after the batch went out
        world.clock.advance(timedelta(minutes=5))
        direct = Services(world, ScriptedLlm(ok(), NO_PEOPLE))
        assert direct.extract(lead, run_id, lane=Lane.INTERACTIVE) is True
    evidence_before = len(world.evidence(lead))
    runs_before = (len(world.runs(lead)), len(people_runs(world, lead)))
    llm.end_batch("msgbatch_fake_1", {signals_id: BatchSucceeded(law_signals(pages))})
    llm.end_batch("msgbatch_fake_2", {people_id: BatchSucceeded(law_people(pages))})

    assert services.poll() == 2

    assert statuses(world) == {SIGNALS: "stale", PEOPLE: "stale"}
    assert len(world.evidence(lead)) == evidence_before
    assert (len(world.runs(lead)), len(people_runs(world, lead))) == runs_before
    assert world.sql("SELECT COUNT(*) FROM contacts WHERE lead_id = ?", (lead,)) == [(0,)]
    assert [b[4] for b in batches(world)] == ["deleted", "deleted"]
    assert llm.calls == []


def test_a_changed_tuple_makes_the_result_stale(world: World) -> None:
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, _, pages = submitted(world, services)
    signals_id, _ = ids(lead)
    # the active tuple no longer matches: the stored key's prompt hash differs
    [(fingerprint,)] = world.sql(
        "SELECT fingerprint FROM ai_batch_items WHERE route = 'extract_signals'"
    )
    document = json.loads(fingerprint)
    document["route_tuple"] = document["route_tuple"][:-12] + "0" * 12
    world.sql(
        "UPDATE ai_batch_items SET fingerprint = ? WHERE route = 'extract_signals'",
        (json.dumps(document, sort_keys=True, separators=(",", ":")),),
    )
    llm.end_batch("msgbatch_fake_1", {signals_id: BatchSucceeded(law_signals(pages))})
    llm.end_batch("msgbatch_fake_2", {})  # no result: the people item re-runs directly
    llm.script.append(law_people(pages))

    assert services.poll() == 2

    assert statuses(world) == {SIGNALS: "stale", PEOPLE: "rerun"}
    assert world.runs(lead) == []
    assert [t.route for t, _, _ in llm.calls] == [PEOPLE]


# Row: Lost, invalid -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        BatchLost(BatchLostKind.ERRORED),
        BatchLost(BatchLostKind.CANCELED),
        BatchLost(BatchLostKind.EXPIRED),
        BatchSucceeded(LlmRefused(None, model=HAIKU, usage=USAGE)),
        BatchSucceeded(LlmTruncated(model=HAIKU, usage=USAGE)),
        None,  # no result for the item
    ],
)
def test_a_lost_refused_truncated_or_missing_item_is_re_run_directly_at_soon(
    world: World, result: Any
) -> None:
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, _, pages = submitted(world, services)
    signals_id, people_id = ids(lead)
    llm.end_batch("msgbatch_fake_1", {} if result is None else {signals_id: result})
    llm.end_batch("msgbatch_fake_2", {people_id: BatchSucceeded(law_people(pages))})
    llm.script.append(law_signals(pages))

    assert services.poll() == 2

    assert [(t.route, u) for t, _, u in llm.calls] == [(SIGNALS, Urgency.SOON)]
    assert statuses(world) == {SIGNALS: "rerun", PEOPLE: "applied"}
    assert [row[0] for row in world.runs(lead)] == ["ok"]
    assert [b[4] for b in batches(world)] == ["deleted", "deleted"]


def test_a_re_run_the_api_refuses_keeps_the_item_submitted_for_the_retry(world: World) -> None:
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, _, pages = submitted(world, services)
    signals_id, people_id = ids(lead)
    llm.end_batch("msgbatch_fake_1", {signals_id: BatchLost(BatchLostKind.EXPIRED)})
    llm.end_batch("msgbatch_fake_2", {people_id: BatchSucceeded(law_people(pages))})
    llm.script.append(AdapterError(True, "llm_unavailable", "down"))

    with pytest.raises(AdapterError):
        services.poll()

    assert statuses(world) == {SIGNALS: "submitted", PEOPLE: "submitted"}
    assert batches(world)[0][4] == "ended"
    llm.script.append(law_signals(pages))

    assert services.poll() == 2

    assert statuses(world) == {SIGNALS: "rerun", PEOPLE: "applied"}
    assert [row[0] for row in world.runs(lead)] == ["failed", "ok"]
    # the ended signals batch is re-fetched unmetered; the people batch's first fetch is
    # metered
    assert llm.fetched_metered == [True, False, True]


def test_a_re_run_the_budget_refuses_fails_the_item_so_the_batch_can_go(world: World) -> None:
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, _, pages = submitted(world, services)
    signals_id, people_id = ids(lead)
    llm.end_batch("msgbatch_fake_1", {signals_id: BatchLost(BatchLostKind.ERRORED)})
    llm.end_batch("msgbatch_fake_2", {people_id: BatchSucceeded(law_people(pages))})
    spend_the_budget(world)

    services.poll()

    assert statuses(world) == {SIGNALS: "failed", PEOPLE: "applied"}
    assert llm.calls == []
    assert [b[4] for b in batches(world)] == ["deleted", "deleted"]


def test_an_invalid_result_records_the_invalid_run_and_fails_the_item(world: World) -> None:
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, _, _ = submitted(world, services)
    signals_id, people_id = ids(lead)
    invalid = BatchSucceeded(LlmInvalid(model=HAIKU, usage=USAGE))
    llm.end_batch("msgbatch_fake_1", {signals_id: invalid})
    llm.end_batch("msgbatch_fake_2", {people_id: invalid})

    assert services.poll() == 2

    assert statuses(world) == {SIGNALS: "failed", PEOPLE: "failed"}
    assert [row[0] for row in world.runs(lead)] == ["invalid"]
    assert [row[0] for row in people_runs(world, lead)] == ["invalid"]
    assert len(world.jobs("check_job_boards")) == 1  # the people run continues the chain
    assert world.evidence(lead) == [] and llm.calls == []


# The schedule -----------------------------------------------------------------------------------


def test_the_schedule_is_the_setting_in_the_senders_timezone_and_catches_up_a_missed_item(
    world: World,
) -> None:
    services = Services(world, ScriptedLlm())

    assert services.schedule() == BulkAiSchedule(time(14, 0), "UTC", False)
    queued(world, services)  # queued at 09:00 on T0's day
    assert services.schedule().missed is False  # today's 14:00 hasn't come
    world.clock.advance(timedelta(hours=6))  # 15:00: the 14:00 submit didn't take them
    assert services.schedule().missed is True
    with world.units.open():
        Setup(world.clock).save({"bulk_ai_submit_at": "16:30", "sender_timezone": "Europe/London"})
    assert services.schedule() == BulkAiSchedule(time(16, 30), "Europe/London", False)


# The acceptance criterion through the real gateway -----------------------------------------------


class BatchesApi:
    """A ``MockTransport`` of the Batches API that ends each batch at once, answering each item
    with ``answer(custom_id)``, and of the Messages API (``direct``)."""

    def __init__(self, answer: Any, *, direct: dict[str, Any], direct_failures: int = 0) -> None:
        self.answer = answer
        self.direct = direct
        self.direct_failures = direct_failures  # how many direct calls answer HTTP 503 first
        self.submitted: dict[str, list[str]] = {}
        self.deleted: list[str] = []
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        base = "https://api.anthropic.com/v1/messages/batches"
        url = str(request.url).split("?")[0]
        if request.method == "POST" and url == "https://api.anthropic.com/v1/messages":
            if self.direct_failures:
                self.direct_failures -= 1
                return httpx2.Response(503, json={"type": "error", "error": {"type": "down"}})
            return httpx2.Response(200, json=self.direct)
        if request.method == "POST" and url == base:
            body = json.loads(request.content)
            self.bodies.append(body)
            batch_id = f"msgbatch_{len(self.submitted) + 1:04d}"
            self.submitted[batch_id] = [entry["custom_id"] for entry in body["requests"]]
            return httpx2.Response(200, json=self._batch(batch_id, "in_progress"))
        batch_id = url.removeprefix(base + "/").removesuffix("/results")
        if request.method == "DELETE":
            self.deleted.append(batch_id)
            return httpx2.Response(200, json={"id": batch_id, "type": "message_batch_deleted"})
        if url.endswith("/results"):
            lines = [self.answer(custom) for custom in self.submitted[batch_id]]
            return httpx2.Response(200, content="\n".join(map(json.dumps, lines)).encode())
        return httpx2.Response(200, json=self._batch(batch_id, "ended"))

    @staticmethod
    def _batch(batch_id: str, status: str) -> dict[str, Any]:
        base = "https://api.anthropic.com/v1/messages/batches"
        return {
            "id": batch_id,
            "type": "message_batch",
            "processing_status": status,
            "request_counts": {
                "processing": 0,
                "succeeded": 1,
                "errored": 0,
                "canceled": 0,
                "expired": 0,
            },
            "created_at": "2026-11-18T10:00:00Z",
            "expires_at": "2026-11-19T10:00:00Z",
            "ended_at": None,
            "archived_at": None,
            "cancel_initiated_at": None,
            "results_url": f"{base}/{batch_id}/results" if status == "ended" else None,
        }


def test_mixed_results_settle_once_meter_at_half_price_and_delete_through_the_gateway(
    world: World,
) -> None:
    """The acceptance criterion: an ended batch with mixed results, polled twice, settles each
    item exactly once; ``ai_usage`` holds the batch-flagged rows at half price; both batches
    are deleted. The usage goes through the real meter into ``ai_usage``."""
    lead, run_id, pages = law_lead(world)

    def answer(custom: str) -> dict[str, Any]:
        if custom.startswith(SIGNALS):
            message = message_json(document=law_signals(pages).parsed)
            return {"custom_id": custom, "result": {"type": "succeeded", "message": message}}
        return {"custom_id": custom, "result": {"type": "errored", "error": {"type": "error"}}}

    api = BatchesApi(answer, direct=message_json(document=law_people(pages).parsed))
    secrets = InMemorySecrets()
    secrets.write("anthropic/api_key", "Kq4Zx8Wn2Lr6Tb9Hm3Vc")
    meter = UsageMeter(world.units, world.clock)
    http = httpx2.Client(transport=httpx2.MockTransport(api))
    gateway = ClaudeGateway(secrets, meter=meter, http=http)
    services = Services(world, gateway, meter=meter)
    assert services.extract(lead, run_id) is True
    assert services.submit() == 2
    for body in api.bodies:  # every batch request body
        for entry in body["requests"]:
            assert "fallbacks" not in json.dumps(entry)
            assert entry["params"]["system"][-1]["cache_control"]["ttl"] == "1h"
            assert entry["params"]["output_config"]["format"]["type"] == "json_schema"

    assert services.poll() == 2
    assert services.poll() == 0

    assert statuses(world) == {SIGNALS: "applied", PEOPLE: "rerun"}
    assert [row[0] for row in world.runs(lead)] == ["ok"]
    assert [row[0] for row in people_runs(world, lead)] == ["ok"]  # the direct re-run's
    assert sorted(api.deleted) == sorted(api.submitted)
    usage = world.sql(
        "SELECT route, batch, error_code, input_tokens, output_tokens, cost_micros "
        "FROM ai_usage ORDER BY rowid"
    )
    half = -(-(1_000 * 100_000 + 200 * 500_000) // 2_000_000)
    full = -(-(1_000 * 100_000 + 200 * 500_000) // 1_000_000)
    assert usage == [
        (SIGNALS, 1, None, 1_000, 200, half),
        (PEOPLE, 1, "llm_batch_errored", 0, 0, 0),
        (PEOPLE, 0, None, 1_000, 200, full),
    ]


# Review patches (pass 1) ------------------------------------------------------------------------


def test_a_re_poll_after_a_retryable_re_run_error_meters_each_batch_result_once(
    world: World,
) -> None:
    """#1: the people batch ends with an errored item whose direct re-run first answers 503;
    the second poll re-fetches the ended batch through the real gateway and meters nothing."""
    lead, run_id, pages = law_lead(world)

    def answer(custom: str) -> dict[str, Any]:
        if custom.startswith(SIGNALS):
            message = message_json(document=law_signals(pages).parsed)
            return {"custom_id": custom, "result": {"type": "succeeded", "message": message}}
        return {"custom_id": custom, "result": {"type": "errored", "error": {"type": "error"}}}

    api = BatchesApi(
        answer, direct=message_json(document=law_people(pages).parsed), direct_failures=1
    )
    secrets = InMemorySecrets()
    secrets.write("anthropic/api_key", "Kq4Zx8Wn2Lr6Tb9Hm3Vc")
    meter = UsageMeter(world.units, world.clock)
    http = httpx2.Client(transport=httpx2.MockTransport(api))
    services = Services(world, ClaudeGateway(secrets, meter=meter, http=http), meter=meter)
    assert services.extract(lead, run_id) is True
    assert services.submit() == 2

    with pytest.raises(AdapterError) as raised:
        services.poll()
    assert raised.value.retryable is True
    assert statuses(world) == {SIGNALS: "applied", PEOPLE: "submitted"}

    assert services.poll() == 1

    assert statuses(world) == {SIGNALS: "applied", PEOPLE: "rerun"}
    batch_rows = world.sql("SELECT route, error_code FROM ai_usage WHERE batch = 1 ORDER BY rowid")
    assert batch_rows == [(SIGNALS, None), (PEOPLE, "llm_batch_errored")]  # once each
    assert sorted(api.deleted) == sorted(api.submitted)


def test_a_re_run_the_api_rejects_fails_the_item_so_the_batch_can_go(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """#2: a non-retryable re-run error settles the item ``failed`` instead of leaving it
    ``submitted`` (and re-paid) on every poll."""
    caplog.set_level(logging.INFO, logger="nextron.app.research")
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, _, pages = submitted(world, services)
    signals_id, people_id = ids(lead)
    llm.end_batch("msgbatch_fake_1", {signals_id: BatchLost(BatchLostKind.EXPIRED)})
    llm.end_batch("msgbatch_fake_2", {people_id: BatchSucceeded(law_people(pages))})
    llm.script.append(AdapterError(False, "llm_rejected", "no"))

    assert services.poll() == 2

    assert statuses(world) == {SIGNALS: "failed", PEOPLE: "applied"}
    assert [row[0] for row in world.runs(lead)] == ["failed"]
    assert [b[4] for b in batches(world)] == ["deleted", "deleted"]
    [logged] = [r for r in caplog.records if r.message == "ai_batch_rerun_failed"]
    found = (logged.route, logged.error_code)  # type: ignore[attr-defined]
    assert found == (SIGNALS, "llm_rejected")


def test_a_batch_whose_tuple_is_no_longer_active_is_read_with_its_stored_tuple(
    world: World,
) -> None:
    """#3a: another tuple is active for the route by the time the batch ends: the batch is
    still fetched (``tuple_by_key`` falls back to the stored tuple) and its result is stale."""
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, _, pages = submitted(world, services)
    signals_id, people_id = ids(lead)
    [(effort,)] = world.sql(
        "SELECT effort FROM ai_route_activations "
        "WHERE route = 'extract_signals' AND deactivated_at IS NULL"
    )
    other = "high" if effort != "high" else "medium"
    world.sql(
        "UPDATE ai_route_activations SET effort = ? "
        "WHERE route = 'extract_signals' AND deactivated_at IS NULL",
        (other,),
    )
    llm.end_batch("msgbatch_fake_1", {signals_id: BatchSucceeded(law_signals(pages))})
    llm.end_batch("msgbatch_fake_2", {people_id: BatchSucceeded(law_people(pages))})

    assert services.poll() == 2

    assert llm.fetched == ["msgbatch_fake_1", "msgbatch_fake_2"]
    assert statuses(world) == {SIGNALS: "stale", PEOPLE: "applied"}
    assert world.runs(lead) == []
    assert [b[4] for b in batches(world)] == ["deleted", "deleted"]


def test_a_batch_whose_tuple_is_gone_fails_its_items_with_no_provider_call(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """#3b: no tuple builds with the batch's key: its items fail and its row is deleted
    locally, with no fetch or delete sent (the batch may still be processing)."""
    caplog.set_level(logging.INFO, logger="nextron.app.research")
    llm = ScriptedLlm()
    services = Services(world, llm)
    submitted(world, services)
    [(key,)] = world.sql(
        "SELECT route_tuple_key FROM ai_batches WHERE provider_batch_id = 'msgbatch_fake_1'"
    )
    world.sql(
        "UPDATE ai_batches SET route_tuple_key = ? WHERE provider_batch_id = 'msgbatch_fake_1'",
        (key[:-12] + "0" * 12,),
    )

    assert services.poll() == 1  # the people batch is still in progress

    assert statuses(world) == {SIGNALS: "failed", PEOPLE: "submitted"}
    assert [b[4] for b in batches(world)] == ["deleted", "submitted"]
    assert llm.fetched == ["msgbatch_fake_2"] and llm.deleted == []
    [logged] = [r for r in caplog.records if r.message == "ai_batch_tuple_missing"]
    assert logged.batch_row_id  # type: ignore[attr-defined]


def test_one_failing_batch_is_logged_and_the_newer_batches_are_still_polled(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """#4: a non-retryable fetch error on the oldest batch doesn't block the next one."""
    caplog.set_level(logging.INFO, logger="nextron.app.research")
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, _, pages = submitted(world, services)
    signals_id, people_id = ids(lead)
    llm.end_batch("msgbatch_fake_1", {signals_id: BatchSucceeded(law_signals(pages))})
    llm.end_batch("msgbatch_fake_2", {people_id: BatchSucceeded(law_people(pages))})
    llm.fetch_errors.append(AdapterError(False, "llm_rejected", "no"))

    assert services.poll() == 1

    assert statuses(world) == {SIGNALS: "submitted", PEOPLE: "applied"}
    assert [b[4] for b in batches(world)] == ["submitted", "deleted"]
    [(signals_row,)] = world.sql(
        "SELECT id FROM ai_batches WHERE provider_batch_id = 'msgbatch_fake_1'"
    )
    [logged] = [r for r in caplog.records if r.message == "ai_batch_poll_failed"]
    found = (logged.batch_row_id, logged.error_code)  # type: ignore[attr-defined]
    assert found == (signals_row, "llm_rejected")

    assert services.poll() == 1  # the next poll settles it

    assert statuses(world) == {SIGNALS: "applied", PEOPLE: "applied"}


def test_a_retryable_fetch_error_still_fails_the_poll(world: World) -> None:
    llm = ScriptedLlm()
    services = Services(world, llm)
    submitted(world, services)
    llm.fetch_errors.append(AdapterError(True, "llm_unavailable", "down"))

    with pytest.raises(AdapterError):
        services.poll()


def test_two_crawl_runs_whose_jobs_run_late_queue_one_item_each_by_the_runs_own_ordinal(
    world: World,
) -> None:
    """#15: both crawl runs exist before either extraction job runs: each run's item gets its
    own revision and context, and the submit sends only the newest."""
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, first, _ = law_lead(world)
    world.clock.advance(timedelta(days=1))
    second = world.crawl(lead)
    assert second != first

    assert services.extract(lead, first) is True
    assert services.extract(lead, second) is True

    assert [(row[1], row[4], row[5], row[6]) for row in items(world)] == [
        (SIGNALS, 1, first, "pending"),
        (PEOPLE, 1, first, "pending"),
        (SIGNALS, 2, second, "pending"),
        (PEOPLE, 2, second, "pending"),
    ]
    world.clock.advance(timedelta(hours=1))
    assert services.submit() == 2
    assert [(row[1], row[4], row[6]) for row in items(world)] == [
        (SIGNALS, 1, "stale"),
        (PEOPLE, 1, "stale"),
        (SIGNALS, 2, "submitted"),
        (PEOPLE, 2, "submitted"),
    ]


def test_an_invalid_result_after_a_page_changed_records_what_was_sent(world: World) -> None:
    """#18: the invalid run of an item whose pages changed records the fingerprint's actor and
    page count, and the people item still continues the chain."""
    llm = ScriptedLlm()
    services = Services(world, llm)
    lead, _, pages = submitted(world, services)
    signals_id, people_id = ids(lead)
    fingerprints = dict(world.sql("SELECT route, fingerprint FROM ai_batch_items"))
    for page_id in pages.values():
        changed_page(world, page_id)
    invalid = BatchSucceeded(LlmInvalid(model=HAIKU, usage=USAGE))
    llm.end_batch("msgbatch_fake_1", {signals_id: invalid})
    llm.end_batch("msgbatch_fake_2", {people_id: invalid})

    assert services.poll() == 2

    assert statuses(world) == {SIGNALS: "failed", PEOPLE: "failed"}
    runs = world.sql(
        "SELECT route, outcome, actor, pages_sent FROM extraction_runs WHERE lead_id = ? "
        "ORDER BY rowid",
        (lead,),
    )
    assert runs == [
        (name, "invalid", f"ai:{name}@v1", len(Fingerprint.decode(fingerprints[name]).inputs))
        for name in (SIGNALS, PEOPLE)
    ]
    assert all(run[3] > 0 for run in runs)
    assert len(world.jobs("check_job_boards")) == 1
    assert world.evidence(lead) == [] and llm.calls == []


class StubOwner:
    """A batch owner whose ``schedule()`` is fixed."""

    def __init__(self, schedule: BulkAiSchedule) -> None:
        self.found = schedule

    def schedule(self) -> BulkAiSchedule:
        return self.found


def test_read_bulk_ai_schedule_is_the_first_owners_time_and_catches_up_on_any_missed(
    world: World,
) -> None:
    """#19: the setting and timezone come from the owners' schedule; one owner that missed
    the latest daily time makes the run catch up at start."""
    on_time = StubOwner(BulkAiSchedule(time(16, 30), "Europe/London", False))
    missed = StubOwner(BulkAiSchedule(time(16, 30), "Europe/London", True))

    assert main.read_bulk_ai_schedule(world.units, [on_time]) == DailyRun(
        time(16, 30), "Europe/London", catch_up=False
    )
    assert main.read_bulk_ai_schedule(world.units, [on_time, missed]) == DailyRun(
        time(16, 30), "Europe/London", catch_up=True
    )
    with pytest.raises(RuntimeError):
        main.read_bulk_ai_schedule(world.units, [])
