"""The Quotas and cost page (FR-57, NFR-11, NFR-12, UX-DR28): this month's usage against the
quotas and the AI budget.

``create_app`` serves it: ``GET /quotas`` (``quotas_page``, ``@personal_data(False)``: usage
rows hold no personal data) renders ``quotas.html`` from ``Budget.status()`` (the ``ops``
service) through ``page_context``. The Settings page links to it; the navigation's sections
are fixed (UX-DR), so it isn't listed there. In order, the page shows:

- the note "Sending, reply handling and unsubscribe polling are never blocked.";
- "Non-essential work stopped: <each stopped reason, joined by '; '>", or "Non-essential work
  is running.";
- the month (UTC);
- **AI spend**: "<spent> of <budget>" (``dollars``), the call count, and the state's line when
  the spend is at the warning, at the budget, or the budget is $0 (off);
- **a quotas table**: Quota, Used, Monthly quota and State (``OK``, ``Reached: <stops>
  stopped`` or ``Off (quota 0)``), each amount in the quota's own unit, so Hunter shows credits
  with halves;
- **a usage table**: Provider, Operation, SKU, Calls, Failed and Units, one row per line of the
  month; a scaled provider's units are in its quota's unit, named ("1.5 credits" for Hunter),
  every other provider's are plain numbers; with no lines, "No external calls recorded this
  month."
"""

from dataclasses import dataclass
from typing import Final

from nextron.domain.budget import (
    AI_STOPS,
    NEVER_BLOCKED,
    QUOTAS,
    AiLine,
    AiState,
    ApiUse,
    Assessment,
    QuotaLine,
    QuotaState,
    display_scale,
    dollars,
    quantity,
)

PAGE_TEMPLATE: Final = "quotas.html"
RUNNING: Final = "Non-essential work is running."
STOPPED_LEAD: Final = "Non-essential work stopped: "
NO_USAGE: Final = "No external calls recorded this month."
NO_SKU: Final = "-"


@dataclass(frozen=True, slots=True)
class QuotaRow:
    """One row of the quotas table, as the template shows it."""

    label: str
    used: str
    limit: str
    state: str


@dataclass(frozen=True, slots=True)
class UsageRow:
    """One row of the usage table, as the template shows it."""

    provider: str
    operation: str
    sku: str
    calls: str
    failed: str
    units: str


@dataclass(frozen=True, slots=True)
class AiSpend:
    """The AI spend section: "<spent> of <budget>", the call count and the state's line."""

    spend: str
    calls: str
    line: str | None


def ai_line(ai: AiLine) -> str | None:
    """The AI spend's state as a sentence, or ``None`` when it is simply OK."""
    match ai.state:
        case AiState.WARNING:
            return (
                f"Warning: AI spend has reached {ai.alert_pct}% of the {dollars(ai.budget)} "
                "budget."
            )
        case AiState.REACHED:
            return f"AI budget reached: {AI_STOPS} stopped until next month."
        case AiState.OFF:
            return f"Off (budget $0): {AI_STOPS} don't run."
        case _:
            return None


def quota_state(line: QuotaLine) -> str:
    """``OK``, ``Reached: <stops> stopped`` or ``Off (quota 0)``."""
    match line.state:
        case QuotaState.REACHED:
            return f"Reached: {line.spec.stops} stopped"
        case QuotaState.OFF:
            return "Off (quota 0)"
        case _:
            return "OK"


def quota_row(line: QuotaLine) -> QuotaRow:
    spec = line.spec
    return QuotaRow(
        label=spec.label,
        used=f"{quantity(line.used, spec.scale)} {spec.unit}",
        limit=f"{quantity(line.limit, spec.scale)} {spec.unit}",
        state=quota_state(line),
    )


def usage_units(line: ApiUse) -> str:
    """A usage line's units: in its quota's unit, named, for a scaled provider (Hunter's
    credits, with halves), else the plain number."""
    scale = display_scale(line.provider)
    shown = quantity(line.units, scale)
    if scale == 1:
        return shown
    unit = next(spec.unit for spec in QUOTAS if spec.provider == line.provider)
    return f"{shown} {unit}"


def stopped_text(assessment: Assessment) -> str:
    """What non-essential work is doing: stopped, with why, or running."""
    if assessment.stopped:
        return STOPPED_LEAD + "; ".join(assessment.stopped)
    return RUNNING


def page_context(assessment: Assessment) -> dict[str, object]:
    """What ``quotas.html`` renders for ``assessment``."""
    ai = assessment.ai
    usage = [
        UsageRow(
            provider=line.provider,
            operation=line.operation,
            sku=line.sku if line.sku is not None else NO_SKU,
            calls=f"{line.calls:,}",
            failed=f"{line.failed:,}",
            units=usage_units(line),
        )
        for line in assessment.api
    ]
    return {
        "never_blocked": NEVER_BLOCKED,
        "stopped": stopped_text(assessment),
        "any_stopped": bool(assessment.stopped),
        "month": assessment.month,
        "ai": AiSpend(f"{dollars(ai.spent)} of {dollars(ai.budget)}", f"{ai.calls:,}", ai_line(ai)),
        "quotas": [quota_row(line) for line in assessment.quotas],
        "usage": usage,
        "no_usage": NO_USAGE,
    }
