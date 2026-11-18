"""Story 8.1: only supply work is blockable (AD-9, FR-57, NFR-11). The budget and the quotas may
stop discovery, enrichment and re-check work, never sending, replies, unsubscribe polling, daily
selection, re-contacts or alerts.

- **(a) Groups.** Every ``blockable`` type registered by ``nextron.main.build_job_types`` is in
  group Discovery, Enrichment or Re-check, or is a named ``ALLOWED_EXCEPTIONS`` entry with its
  reason.
- **(b) Names.** ``EXPECTED_BLOCKABLE`` is the whole list the epics name (some types only exist
  in later stories or on other tracks): every one that is registered is ``blockable``, no other
  type is, and ``MUST_BE_ESSENTIAL`` types are ``essential`` when present.
- **(c) The essential run at 150%.** ``build_app`` on a temporary database at head with a fake
  for every port; the month's AI spend at 150% of the budget and every quota used up. Each
  ``essential`` handler runs with its ``ESSENTIAL_SAMPLES`` payload while ``allow`` is spied on
  in ``nextron.domain.budget`` and in every loaded ``nextron.*`` module bound to it: none returns
  ``DEFERRED`` and none asks ``allow`` about ``blockable`` work. An essential type without a
  sample fails the test by name, so each new type is added on purpose.

Self-tests prove that the group and name check and the spy catch a planted violation.
"""

import sys
import types
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import pytest
from fastapi import FastAPI

from nextron import main
from nextron.app.queries import setup as setup_queries
from nextron.bootstrap import load_bootstrap
from nextron.db.metering import UsageMeter
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import budget
from nextron.domain.budget import QUOTAS, AiCall, ApiCall, BudgetClass
from nextron.domain.job import (
    DEFERRED,
    Job,
    JobGroup,
    JobType,
    JobTypes,
    Lane,
    decode_payload,
    encode_payload,
)
from tests.fakes.clock import FrozenClock
from tests.fakes.crawler import ScriptedCrawler
from tests.fakes.dns import FakeDns
from tests.fakes.google_oauth import FakeGoogleOAuth
from tests.fakes.job_board import ScriptedJobBoards
from tests.fakes.llm import ScriptedLlm
from tests.fakes.places import ScriptedPlaces
from tests.fakes.registry import ScriptedRegistry
from tests.fakes.renderer import ScriptedRenderer
from tests.fakes.secrets import InMemorySecrets

T0: Final = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
BLOCKABLE_GROUPS: Final = frozenset({JobGroup.DISCOVERY, JobGroup.ENRICHMENT, JobGroup.RECHECK})
ALLOWED_EXCEPTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "run_ai_test_sets": (
            "Story 4.5 decision 3: an AI Test Set run is deferrable AI spend, so it must not "
            "spend past the AI budget"
        ),
    }
)
EXPECTED_BLOCKABLE: Final = frozenset(
    {
        "discover_places",
        "discover_registry",
        "discover_aggregator",
        "recheck_lead",
        "process_signal_item",
        "enrich_lead",
        "render_pages",
        "extract_lead",
        "check_job_boards",
        "check_legal_form",
        "find_contact_email",
        "verify_contact_email",
        "recheck_event",
        "run_ai_test_sets",
    }
)
MUST_BE_ESSENTIAL: Final = frozenset(
    {"poll_unsubscribes", "daily_selection", "recontact_due", "send_alert"}
)
LEAD: Final = "01JD0000000000000000000010"
RUN: Final = "01JD0000000000000000000011"
ALERT: Final = "01JD0000000000000000000012"
# One payload per essential type: the handler runs it at 150% of the AI budget. A type without
# one fails ``test_essential_handlers_never_defer_or_ask_about_blockable_work`` by name.
ESSENTIAL_SAMPLES: Final[Mapping[str, Mapping[str, object]]] = MappingProxyType(
    {
        "send_alert": {"v": 1, "alert_id": ALERT},
        "backup_db": {"v": 1, "kind": "manual"},
        "restore_test": {"v": 1, "kind": "monthly"},
        "check_recovery": {"v": 1},
        "resolve_contact_markets": {"v": 1},
        "canonicalize_lead": {"v": 1, "lead_id": LEAD, "run_id": RUN},
        "purge_page_text": {"v": 1},
        "purge_place_coords": {"v": 1},
        "select_primary_contact": {"v": 1, "lead_id": LEAD},
        "check_domains": {"v": 1},
    }
)


# (a) and (b): groups and names ----------------------------------------------------------------


def class_problems(types: Iterable[JobType]) -> list[str]:
    """Each way ``types`` break the budget-class rules, naming the type."""
    problems: list[str] = []
    for job_type in types:
        name = job_type.name
        blockable = job_type.budget_class is BudgetClass.BLOCKABLE
        if blockable and job_type.group not in BLOCKABLE_GROUPS and name not in ALLOWED_EXCEPTIONS:
            problems.append(f"{name}: blockable outside Discovery, Enrichment and Re-check")
        if blockable and name not in EXPECTED_BLOCKABLE:
            problems.append(f"{name}: blockable but not a listed supply type")
        if not blockable and name in EXPECTED_BLOCKABLE:
            problems.append(f"{name}: a supply type registered essential")
        if blockable and name in MUST_BE_ESSENTIAL:
            problems.append(f"{name}: must never be blocked by the budget")
    return problems


def registered(job_types: JobTypes) -> tuple[JobType, ...]:
    return tuple(job_types.get(name) for name in job_types.names())


def no_service() -> Any:
    raise AssertionError("no job runs in the registry checks")


def registry() -> JobTypes:
    return main.build_job_types(
        alerts=no_service,
        backups=no_service,
        restore_tests=no_service,
        recovery=no_service,
        contact_markets=no_service,
        crawls=no_service,
        discovery=no_service,
        research=no_service,
        extractions=no_service,
        primary=no_service,
        test_runs=no_service,
        job_boards=no_service,
        legal_forms=no_service,
    )


def test_only_supply_work_is_blockable() -> None:
    types = registered(registry())

    assert class_problems(types) == []
    blockable = {t.name for t in types if t.budget_class is BudgetClass.BLOCKABLE}
    assert blockable == {
        "enrich_lead",
        "render_pages",
        "extract_lead",
        "check_legal_form",
        "check_job_boards",
        "recheck_event",
        "discover_places",
        "discover_registry",
        "run_ai_test_sets",
    }
    for name in MUST_BE_ESSENTIAL & {t.name for t in types}:
        assert registry().get(name).budget_class is BudgetClass.ESSENTIAL


def test_the_allowed_exceptions_are_registered_and_explained() -> None:
    names = set(registry().names())
    for name, reason in ALLOWED_EXCEPTIONS.items():
        assert name in names and name in EXPECTED_BLOCKABLE and reason.strip()


def _planted(name: str, group: JobGroup, budget_class: BudgetClass) -> JobType:
    return JobType(
        name=name,
        default_lane=Lane.BULK,
        lanes=frozenset({Lane.BULK}),
        priority=10,
        max_runtime=timedelta(minutes=1),
        retry_limit=0,
        handler=lambda job: None,
        group=group,
        budget_class=budget_class,
    )


@pytest.mark.parametrize(
    ("planted", "problem"),
    [
        (
            _planted("send_reports", JobGroup.SENDING, BudgetClass.BLOCKABLE),
            "send_reports: blockable outside Discovery, Enrichment and Re-check",
        ),
        (
            _planted("enrich_lead", JobGroup.ENRICHMENT, BudgetClass.ESSENTIAL),
            "enrich_lead: a supply type registered essential",
        ),
        (
            _planted("send_alert", JobGroup.OTHER, BudgetClass.BLOCKABLE),
            "send_alert: must never be blocked by the budget",
        ),
        (
            _planted("enrich_more", JobGroup.ENRICHMENT, BudgetClass.BLOCKABLE),
            "enrich_more: blockable but not a listed supply type",
        ),
    ],
)
def test_the_class_check_catches_a_planted_violation(planted: JobType, problem: str) -> None:
    assert problem in class_problems((planted,))


# (c): the essential handlers at 150% ------------------------------------------------------------


class AllowSpy:
    """``allow`` bound in ``nextron.domain.budget`` and every loaded ``nextron.*`` module that
    holds it under any name (an aliased import included), recording each call's budget class and answering as the real one."""

    def __init__(self, real: Callable[..., bool]) -> None:
        self.real = real
        self.classes: list[object] = []

    def __call__(self, budget_class: object, *args: Any, **kwargs: Any) -> bool:
        self.classes.append(budget_class)
        return self.real(budget_class, *args, **kwargs)


def spy_on_allow(monkeypatch: pytest.MonkeyPatch) -> AllowSpy:
    real = budget.allow
    spy = AllowSpy(real)
    for module in list(sys.modules.values()):
        name = getattr(module, "__name__", "")
        if name != "nextron" and not name.startswith("nextron."):
            continue
        for attribute, value in list(vars(module).items()):
            if value is real:  # whatever the name it was imported under
                monkeypatch.setattr(module, attribute, spy)
    return spy


def app_on(units: UnitOfWorkFactory, clock: FrozenClock) -> FastAPI:
    return main.build_app(
        load_bootstrap(),
        units=units,
        secrets=InMemorySecrets("test"),
        clock=clock,
        google_oauth=FakeGoogleOAuth(),
        crawler=ScriptedCrawler(),
        llm=ScriptedLlm(),
        renderer=ScriptedRenderer(),
        dns=FakeDns(),
        job_board=ScriptedJobBoards(),
        registry=ScriptedRegistry(),
        places=ScriptedPlaces(),
    )


def exhaust_the_month(units: UnitOfWorkFactory, clock: FrozenClock) -> None:
    """The month's AI spend at 150% of the default $20 budget and every quota used up."""
    meter = UsageMeter(units, clock)
    meter.record(
        AiCall(
            "draft_email",
            "draft_email:v1:m",
            "m",
            input_tokens=1,
            output_tokens=1,
            cost_micros=30_000_000,
        )
    )
    for spec in QUOTAS:
        meter.record(ApiCall(spec.provider, "planted", ok=True, sku=spec.sku, count=1_000_000))


def run_essential(app: FastAPI, units: UnitOfWorkFactory) -> Iterator[tuple[str, object]]:
    job_types: JobTypes = app.state.job_types
    for name in job_types.names():
        job_type = job_types.get(name)
        if job_type.budget_class is not BudgetClass.ESSENTIAL:
            continue
        sample = ESSENTIAL_SAMPLES.get(name)
        assert sample is not None, f"essential job type {name} has no ESSENTIAL_SAMPLES entry"
        job = Job(
            "01JD0000000000000000000099",
            name,
            job_type.default_lane,
            decode_payload(encode_payload(sample)),
            1,
        )
        with units.open():
            yield name, job_type.handler(job)


def test_essential_handlers_never_defer_or_ask_about_blockable_work(
    units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    clock = FrozenClock(T0)
    app = app_on(units, clock)
    exhaust_the_month(units, clock)
    with units.open(read_only=True):
        assessment = budget.assess(
            UsageMeter(units, clock).snapshot(T0), setup_queries.budget_limits()
        )
    assert assessment.ai.state is budget.AiState.REACHED
    assert {line.state for line in assessment.quotas} <= {
        budget.QuotaState.REACHED,
        budget.QuotaState.OFF,
    }
    spy = spy_on_allow(monkeypatch)

    results = dict(run_essential(app, units))

    assert set(results) == set(ESSENTIAL_SAMPLES)
    assert [name for name, result in results.items() if result is DEFERRED] == []
    assert BudgetClass.BLOCKABLE not in spy.classes


def test_the_spy_catches_a_blockable_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = spy_on_allow(monkeypatch)
    from nextron.app.discovery import places  # noqa: PLC0415 - bound to the spy above

    usage = budget.Usage("2026-11", (), 0, 0)
    limits = budget.Limits({spec.name: 1 for spec in QUOTAS}, 1, 80)
    assert places.allow(BudgetClass.BLOCKABLE, usage, limits) is True

    assert spy.classes == [BudgetClass.BLOCKABLE]


def test_the_spy_catches_an_aliased_import(monkeypatch: pytest.MonkeyPatch) -> None:
    planted = types.ModuleType("nextron._planted_alias")
    planted.budget_allows = budget.allow  # type: ignore[attr-defined]  # an aliased import
    monkeypatch.setitem(sys.modules, planted.__name__, planted)
    spy = spy_on_allow(monkeypatch)

    usage = budget.Usage("2026-11", (), 0, 0)
    limits = budget.Limits({spec.name: 1 for spec in QUOTAS}, 1, 80)
    aliased: Callable[..., bool] = planted.budget_allows  # type: ignore[attr-defined]
    assert aliased(BudgetClass.BLOCKABLE, usage, limits) is True

    assert spy.classes == [BudgetClass.BLOCKABLE]
