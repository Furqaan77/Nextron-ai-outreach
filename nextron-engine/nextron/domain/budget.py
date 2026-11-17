"""Metering, quotas and the AI budget (AD-8, AD-9, FR-57, NFR-10, NFR-11, NFR-12). Standard
library only; it never reads a setting itself (``nextron.app.queries.setup.budget_limits``
builds the ``Limits``).

**Budget classes.** ``BudgetClass`` is ``blockable`` (discovery, enrichment, re-checks and their
AI routes) or ``essential`` (everything else: sending, replies, unsubscribe polling). The budget
never blocks ``essential`` work.

**Call records.** What an adapter hands ``UsageMeterPort.record``; each is frozen and checked
when it is built, raising ``TypeError`` or ``ValueError`` that names the field, never its value.
No field can hold ``@`` or a space, so no free text or address fits.

- ``ApiCall(provider, operation, *, ok, error_code=None, sku=None, count=1)``: ``provider``,
  ``operation`` and a non-``None`` ``sku`` fullmatch ``[a-z][a-z0-9_]{0,31}``; ``ok`` is a
  ``bool``; ``error_code`` is ``None`` when ``ok`` is true and otherwise fullmatches
  ``[a-z][a-z0-9_]{0,63}``; ``count`` is an ``int`` (not a ``bool``) from 0 to 1,000,000. Its
  ``units`` are ``count × UNIT_WEIGHTS.get((provider, operation), 1)``: Hunter's credits are
  stored doubled, so a find or a Domain Search is 2 and a verification 1. An adapter passes
  ``count=0`` for a call the provider doesn't bill.
- ``AiCall(route, route_tuple, served_model, *, input_tokens, output_tokens,
  cache_read_tokens=0, cache_write_tokens=0, iterations=1, batch=False, cost_micros,
  refused=False, error_code=None)``: ``route`` fullmatches ``[a-z][a-z0-9_]{0,63}``;
  ``route_tuple`` and ``served_model`` fullmatch ``[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}``; the
  four token counts, ``iterations`` and ``cost_micros`` (micro-dollars) are ``int``s (not
  ``bool``s) from 0 to 10^12; ``batch`` and ``refused`` are ``bool``s; ``error_code`` is
  ``None`` or a code.

**Months.** Usage counts the UTC calendar month. ``month_of(now)`` is ``"YYYY-MM"`` and
``month_bounds(now)`` the month's ``[start, end)``, both of an aware ``now`` (a naive one raises
``ValueError``).

**Usage.** ``Usage(month, api, ai_calls, ai_cost_micros)`` is what ``UsageMeterPort.snapshot``
returns: one ``ApiUse(provider, operation, sku, calls, failed, units)`` line per provider,
operation and SKU, kept sorted by those three (no SKU first), plus the month's AI call count and
spend. ``usage.units(provider, sku=None)`` sums the provider's units, or only those of one SKU.

**Quotas.** ``QUOTAS`` lists each ``QuotaSpec(name, label, provider, sku, setting, scale, unit,
stops)``: the setting holds the monthly quota in the provider's own unit (Hunter credits,
Places requests), and ``scale`` turns it into stored units (Hunter's 2). A spec without a SKU
counts every SKU of its provider. ``Limits(quotas, ai_budget_micros, ai_alert_pct)`` holds
every quota in stored units, by quota name.

**allow(budget_class, usage, limits, *, quota=None)**: ``essential`` is always ``True``.
``blockable`` is ``False`` when the month's AI spend is at least the budget, or when ``quota``
is named and its usage is at least its limit; a limit of 0 therefore always refuses. An unknown
quota name raises ``UnknownQuota``.

**assess(usage, limits)** returns the ``Assessment(month, ai, quotas, api, stopped)`` the Quotas
and cost panel shows: each quota's ``QuotaLine(spec, used, limit, state)`` (``off`` at limit 0,
``reached`` at or past it, else ``ok``), the AI spend's ``AiLine(spent, budget, alert_pct,
calls, state)`` (``off`` at budget 0, ``reached`` at or past it, ``warning`` at or past the
alert percentage, else ``ok``), the API lines, and ``stopped``: "AI budget reached" first, then
"<label> quota reached" for each reached quota.

**thresholds(assessment)** lists the notices to raise as ``Threshold(kind, subject, text)``,
one per kind and subject; the subject names the month, so each comes once a month:

- AI ``warning`` or ``reached``: ``ai_budget_warning``, ``ai_budget:<month>``, "AI spend at
  <pct>% of <budget>: <spent> spent in <month>";
- AI ``reached``: ``ai_budget_reached``, ``ai_budget:<month>``, "AI budget reached: discovery,
  enrichment and re-checks stopped (<spent> of <budget> spent in <month>)";
- a quota ``reached``: ``quota_reached``, ``<quota name>:<month>``, "<label> quota reached:
  <stops> stopped (<used> of <limit> <unit> used in <month>)".

**budget_issues(assessment)** is the health panel's share of the same list: each threshold as
a ``HealthIssue(kind, subject, text)`` (``nextron.domain.health``), without the AI warning
when the AI budget is reached. A limit of 0 (off) is never an issue.

**Formatting.** ``dollars(micros)`` is ``$20`` for whole dollars, else dollars and cents with
the cents truncated (``$16.05``). ``quantity(units, scale)`` is ``units ÷ scale`` with
thousands separators; a half shows as ``.5``. ``display_scale(provider)`` is the scale the
panel shows a provider's units in: its quota's (Hunter's 2), else 1.
"""

import re
from collections.abc import Mapping
from dataclasses import KW_ONLY, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.errors import DomainError
from nextron.domain.health import HealthIssue

NAME_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")  # provider, operation, sku
CODE_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")  # error codes and AI routes
MODEL_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")  # tuples, models
MONTH_PATTERN: Final = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
MAX_COUNT: Final = 1_000_000
MAX_AI_NUMBER: Final = 10**12
MAX_PERCENT: Final = 100
MICROS_PER_DOLLAR: Final = 1_000_000
MICROS_PER_CENT: Final = 10_000

# Units per call, by (provider, operation); anything else is 1 per call. Hunter's credits are
# stored doubled: a find or a Domain Search costs 1 credit, a verification half of one.
UNIT_WEIGHTS: Final[Mapping[tuple[str, str], int]] = MappingProxyType(
    {
        ("hunter", "find"): 2,
        ("hunter", "domain_search"): 2,
        ("hunter", "verify"): 1,
    }
)

AI_BUDGET_SUBJECT: Final = "ai_budget"
AI_BUDGET_WARNING: Final = "ai_budget_warning"
AI_BUDGET_REACHED: Final = "ai_budget_reached"
QUOTA_REACHED: Final = "quota_reached"
AI_BUDGET_STOPPED: Final = "AI budget reached"
AI_STOPS: Final = "discovery, enrichment and re-checks"
NEVER_BLOCKED: Final = "Sending, reply handling and unsubscribe polling are never blocked."


class BudgetClass(StrEnum):
    """Whether the budget may stop a piece of work."""

    BLOCKABLE = "blockable"  # discovery, enrichment, re-checks and their AI routes
    ESSENTIAL = "essential"  # sending, replies, unsubscribe polling: never blocked


class UnknownQuota(DomainError):
    """A quota name ``QUOTAS`` doesn't hold."""

    code = "unknown_quota"

    def __init__(self, name: object) -> None:
        named = isinstance(name, str) and NAME_PATTERN.fullmatch(name) is not None
        super().__init__(f"unknown quota: {name if named else '(not a quota name)'}")
        self.name = str(name) if named else None


# Checks ---------------------------------------------------------------------------------------


def _text(name: str, value: object, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    if pattern.fullmatch(value) is None:
        raise ValueError(f"{name} must match {pattern.pattern}")


def _flag(name: str, value: object) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool")


def _number(name: str, value: object, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    if not 0 <= value <= high:
        raise ValueError(f"{name} must be from 0 to {high:,}")


# Call records ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApiCall:
    """One HTTP request an adapter attempted, whatever its outcome."""

    provider: str
    operation: str
    _: KW_ONLY
    ok: bool
    error_code: str | None = None
    sku: str | None = None
    count: int = 1

    def __post_init__(self) -> None:
        _text("provider", self.provider, NAME_PATTERN)
        _text("operation", self.operation, NAME_PATTERN)
        if self.sku is not None:
            _text("sku", self.sku, NAME_PATTERN)
        _flag("ok", self.ok)
        if self.ok and self.error_code is not None:
            raise ValueError("error_code must be None when ok is true")
        if not self.ok:
            if self.error_code is None:
                raise ValueError("error_code must be set when ok is false")
            _text("error_code", self.error_code, CODE_PATTERN)
        _number("count", self.count, MAX_COUNT)

    @property
    def units(self) -> int:
        """The units this call stores: ``count`` times its weight."""
        return self.count * UNIT_WEIGHTS.get((self.provider, self.operation), 1)


@dataclass(frozen=True, slots=True)
class AiCall:
    """One AI call: its route, route tuple, served model, usage and cost."""

    route: str
    route_tuple: str
    served_model: str
    _: KW_ONLY
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    iterations: int = 1
    batch: bool = False
    cost_micros: int
    refused: bool = False
    error_code: str | None = None

    def __post_init__(self) -> None:
        _text("route", self.route, CODE_PATTERN)
        _text("route_tuple", self.route_tuple, MODEL_PATTERN)
        _text("served_model", self.served_model, MODEL_PATTERN)
        for name in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "iterations",
            "cost_micros",
        ):
            _number(name, getattr(self, name), MAX_AI_NUMBER)
        _flag("batch", self.batch)
        _flag("refused", self.refused)
        if self.error_code is not None:
            _text("error_code", self.error_code, CODE_PATTERN)


# Months ---------------------------------------------------------------------------------------


def _utc(now: object) -> datetime:
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime")
    if now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    return now.astimezone(UTC)


def month_of(now: datetime) -> str:
    """The UTC calendar month of ``now`` as ``YYYY-MM``."""
    utc = _utc(now)
    return f"{utc.year:04d}-{utc.month:02d}"


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """The UTC calendar month of ``now``: its first instant and the next month's."""
    utc = _utc(now)
    start = datetime(utc.year, utc.month, 1, tzinfo=UTC)
    if utc.month == 12:
        return start, datetime(utc.year + 1, 1, 1, tzinfo=UTC)
    return start, datetime(utc.year, utc.month + 1, 1, tzinfo=UTC)


# Usage ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApiUse:
    """One provider, operation and SKU's calls in a month: how many, how many failed, and the
    units they stored."""

    provider: str
    operation: str
    sku: str | None
    calls: int
    failed: int
    units: int


def _line_order(line: ApiUse) -> tuple[str, str, str]:
    return line.provider, line.operation, line.sku or ""


@dataclass(frozen=True, slots=True)
class Usage:
    """A month's usage: its API lines, sorted by provider, operation and SKU, and its AI calls
    and spend."""

    month: str
    api: tuple[ApiUse, ...]
    ai_calls: int
    ai_cost_micros: int

    def __post_init__(self) -> None:
        if not (isinstance(self.month, str) and MONTH_PATTERN.fullmatch(self.month)):
            raise ValueError("month must be YYYY-MM")
        lines = tuple(self.api)
        if not all(isinstance(line, ApiUse) for line in lines):
            raise TypeError("api must hold ApiUse lines")
        object.__setattr__(self, "api", tuple(sorted(lines, key=_line_order)))
        _number("ai_calls", self.ai_calls, 2**63 - 1)
        _number("ai_cost_micros", self.ai_cost_micros, 2**63 - 1)

    def units(self, provider: str, sku: str | None = None) -> int:
        """The units of ``provider``'s lines, or of only its lines with ``sku``."""
        return sum(
            line.units
            for line in self.api
            if line.provider == provider and (sku is None or line.sku == sku)
        )


# Quotas and limits ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QuotaSpec:
    """One provider quota: its setting holds the monthly quota in ``unit``s, and ``scale``
    turns that into stored units. ``stops`` names the work it stops when reached."""

    name: str
    label: str
    provider: str
    sku: str | None
    setting: str
    scale: int
    unit: str
    stops: str


QUOTAS: Final[tuple[QuotaSpec, ...]] = (
    QuotaSpec(
        "hunter", "Hunter", "hunter", None, "quota_hunter_credits", 2, "credits", "enrichment"
    ),
    QuotaSpec(
        "prospeo", "Prospeo", "prospeo", None, "quota_prospeo_credits", 1, "credits", "enrichment"
    ),
    QuotaSpec(
        "places_enterprise",
        "Places Enterprise",
        "places",
        "enterprise",
        "quota_places_enterprise",
        1,
        "requests",
        "discovery",
    ),
    QuotaSpec(
        "places_pro", "Places Pro", "places", "pro", "quota_places_pro", 1, "requests", "discovery"
    ),
    QuotaSpec(
        "job_aggregator",
        "Job aggregator",
        "job_aggregator",
        None,
        "quota_job_aggregator",
        1,
        "requests",
        "discovery",
    ),
)


def quota_spec(name: str) -> QuotaSpec:
    """The quota named ``name``; raises ``UnknownQuota``."""
    for spec in QUOTAS:
        if spec.name == name:
            return spec
    raise UnknownQuota(name)


def display_scale(provider: str) -> int:
    """The scale a provider's units are shown in: its quota's (Hunter's 2), else 1."""
    for spec in QUOTAS:
        if spec.provider == provider:
            return spec.scale
    return 1


@dataclass(frozen=True, slots=True)
class Limits:
    """Every quota in stored units (the setting × ``scale``), by quota name; the monthly AI
    budget in micro-dollars; and the AI spend alert percentage."""

    quotas: Mapping[str, int]
    ai_budget_micros: int
    ai_alert_pct: int

    def __post_init__(self) -> None:
        if not isinstance(self.quotas, Mapping):
            raise TypeError("quotas must be a mapping of quota name to stored units")
        names = {spec.name for spec in QUOTAS}
        if set(self.quotas) != names:
            raise ValueError("quotas must hold exactly the QUOTAS names")
        for name, limit in self.quotas.items():
            _number(f"quota {name}", limit, 2**63 - 1)
        object.__setattr__(self, "quotas", MappingProxyType(dict(self.quotas)))
        _number("ai_budget_micros", self.ai_budget_micros, 2**63 - 1)
        _number("ai_alert_pct", self.ai_alert_pct, MAX_PERCENT)


def _quota_used(usage: Usage, spec: QuotaSpec) -> int:
    return usage.units(spec.provider, spec.sku)


def allow(
    budget_class: BudgetClass, usage: Usage, limits: Limits, *, quota: str | None = None
) -> bool:
    """Whether work of ``budget_class`` may run now; see the module docstring."""
    if budget_class == BudgetClass.ESSENTIAL:
        return True  # the budget never blocks essential work
    if budget_class != BudgetClass.BLOCKABLE:
        raise ValueError("budget_class must be a BudgetClass")
    spec = quota_spec(quota) if quota is not None else None
    if usage.ai_cost_micros >= limits.ai_budget_micros:
        return False
    return spec is None or _quota_used(usage, spec) < limits.quotas[spec.name]


# Assessment -----------------------------------------------------------------------------------


class QuotaState(StrEnum):
    OK = "ok"
    REACHED = "reached"
    OFF = "off"  # a limit of 0: blockable work on it is always refused, quietly


class AiState(StrEnum):
    OK = "ok"
    WARNING = "warning"
    REACHED = "reached"
    OFF = "off"  # a budget of 0


@dataclass(frozen=True, slots=True)
class QuotaLine:
    """A quota's month: used and limit in stored units, and its state."""

    spec: QuotaSpec
    used: int
    limit: int
    state: QuotaState


@dataclass(frozen=True, slots=True)
class AiLine:
    """The month's AI spend against the budget, both in micro-dollars."""

    spent: int
    budget: int
    alert_pct: int
    calls: int
    state: AiState


@dataclass(frozen=True, slots=True)
class Assessment:
    """What the Quotas and cost panel shows, and what ``thresholds`` reads."""

    month: str
    ai: AiLine
    quotas: tuple[QuotaLine, ...]
    api: tuple[ApiUse, ...]
    stopped: tuple[str, ...]


def _quota_state(used: int, limit: int) -> QuotaState:
    if limit == 0:
        return QuotaState.OFF
    if used >= limit:
        return QuotaState.REACHED
    return QuotaState.OK


def _ai_state(spent: int, budget: int, alert_pct: int) -> AiState:
    if budget == 0:
        return AiState.OFF
    if spent >= budget:
        return AiState.REACHED
    if spent * MAX_PERCENT >= budget * alert_pct:
        return AiState.WARNING
    return AiState.OK


def assess(usage: Usage, limits: Limits) -> Assessment:
    """The month's assessment; see the module docstring."""
    quotas = tuple(
        QuotaLine(spec, used, limit, _quota_state(used, limit))
        for spec in QUOTAS
        for used, limit in ((_quota_used(usage, spec), limits.quotas[spec.name]),)
    )
    ai = AiLine(
        spent=usage.ai_cost_micros,
        budget=limits.ai_budget_micros,
        alert_pct=limits.ai_alert_pct,
        calls=usage.ai_calls,
        state=_ai_state(usage.ai_cost_micros, limits.ai_budget_micros, limits.ai_alert_pct),
    )
    stopped = [AI_BUDGET_STOPPED] if ai.state is AiState.REACHED else []
    stopped += [
        f"{line.spec.label} quota reached" for line in quotas if line.state is QuotaState.REACHED
    ]
    return Assessment(usage.month, ai, quotas, usage.api, tuple(stopped))


# Threshold notices ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Threshold:
    """One dashboard notice: its kind, its subject (which names the month) and its text."""

    kind: str
    subject: str
    text: str


def thresholds(assessment: Assessment) -> tuple[Threshold, ...]:
    """The notices ``assessment`` calls for; see the module docstring."""
    month = assessment.month
    ai = assessment.ai
    found: list[Threshold] = []
    ai_subject = f"{AI_BUDGET_SUBJECT}:{month}"
    spent, budget = dollars(ai.spent), dollars(ai.budget)
    if ai.state in (AiState.WARNING, AiState.REACHED):
        found.append(
            Threshold(
                AI_BUDGET_WARNING,
                ai_subject,
                f"AI spend at {ai.alert_pct}% of {budget}: {spent} spent in {month}",
            )
        )
    if ai.state is AiState.REACHED:
        found.append(
            Threshold(
                AI_BUDGET_REACHED,
                ai_subject,
                f"AI budget reached: {AI_STOPS} stopped ({spent} of {budget} spent in {month})",
            )
        )
    for line in assessment.quotas:
        if line.state is not QuotaState.REACHED:
            continue
        spec = line.spec
        used, limit = quantity(line.used, spec.scale), quantity(line.limit, spec.scale)
        found.append(
            Threshold(
                QUOTA_REACHED,
                f"{spec.name}:{month}",
                f"{spec.label} quota reached: {spec.stops} stopped ({used} of {limit} "
                f"{spec.unit} used in {month})",
            )
        )
    return tuple(found)


def budget_issues(assessment: Assessment) -> tuple[HealthIssue, ...]:
    """The health panel's budget issues: each of ``thresholds(assessment)`` as a
    ``HealthIssue(kind, subject, text)``, except that the AI warning is dropped when the AI
    budget is reached (that issue says more)."""
    found = thresholds(assessment)
    reached = any(threshold.kind == AI_BUDGET_REACHED for threshold in found)
    return tuple(
        HealthIssue(threshold.kind, threshold.subject, threshold.text)
        for threshold in found
        if not (reached and threshold.kind == AI_BUDGET_WARNING)
    )


# Formatting -----------------------------------------------------------------------------------


def dollars(micros: int) -> str:
    """``$20`` for whole dollars, else dollars and cents with the cents truncated."""
    _number("micros", micros, 2**63 - 1)
    whole, rest = divmod(micros, MICROS_PER_DOLLAR)
    if rest == 0:
        return f"${whole:,}"
    return f"${whole:,}.{rest // MICROS_PER_CENT:02d}"


def quantity(units: int, scale: int) -> str:
    """``units ÷ scale`` with thousands separators; a half shows as ``.5``."""
    _number("units", units, 2**63 - 1)
    if isinstance(scale, bool) or not isinstance(scale, int) or scale < 1:
        raise ValueError("scale must be an int of at least 1")
    whole, rest = divmod(units, scale)
    if rest == 0:
        return f"{whole:,}"
    fraction = format(Decimal(rest) / Decimal(scale), "f").removeprefix("0")
    return f"{whole:,}{fraction}"
