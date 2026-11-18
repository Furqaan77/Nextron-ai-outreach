"""The Discovery page (Story 8.3; FR-5, UX-DR28, UX-DR29, AD-13): views for ``create_app``'s two
routes, which stay thin.

- ``GET /leads/discovery`` (``discovery_page``, ``@personal_data(False)``: Campaign names,
  Operator locations, statuses and counts) renders ``discovery.html``, which extends
  ``base.html`` and includes ``discovery_panel.html``. Like every page it sends
  ``Cache-Control: no-store``. It is registered before ``/leads/{lead_id}`` and linked from the
  Leads list.
- ``POST /leads/discovery/places`` (``discovery_places``, ``@personal_data(False)``) runs
  ``PlaceDiscovery.request_run`` in its own unit of work and answers 200 with
  ``discovery_panel.html`` alone, which HTMX swaps in for the page's ``#discovery`` block.

**The "Run Places discovery" form** (``method="post"``, ``hx-post``, ``{{ csrf_field() }}``)
holds the Campaign (each active Campaign by name, its value the Campaign's id) and the location
(each item of the ``places_discovery_locations`` setting, its value the location's hash, its
label ``<location> (<CC>)``). ``submitted_run`` needs ``campaign_id`` and ``location`` and
allows ``csrf_token`` besides, each once; anything else is ``MalformedForm("wrong_fields")``
(400). With no
active Campaign or no location the page says so instead of showing the form.

**The answers:** queued ("Places discovery queued.") or already queued ("Places discovery is
already queued for that Campaign and location."), both ``role="status"``; or the refusal's
message (``role="alert"``), with nothing queued.

**Recent runs:** the newest 20 runs, each with its source, Campaign (its name, or "Unknown
Campaign"), location and country, status, start time and the counts seen, created, merged,
suppressed, no own website and Market blocked.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from nextron.app.queries.discovery import StoredRun
from nextron.app.queries.setup import SeededCampaign
from nextron.domain.place_discovery import DiscoveryLocation
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import WRONG_FIELDS, FormFields, MalformedForm

PAGE_TEMPLATE: Final = "discovery.html"
PANEL_TEMPLATE: Final = "discovery_panel.html"
CAMPAIGN_FIELD: Final = "campaign_id"
LOCATION_FIELD: Final = "location"
RUN_FIELDS: Final = frozenset({CAMPAIGN_FIELD, LOCATION_FIELD})
QUEUED: Final = "Places discovery queued."
ALREADY_QUEUED: Final = "Places discovery is already queued for that Campaign and location."
UNKNOWN_CAMPAIGN: Final = "Unknown Campaign"
SOURCE_LABELS: Final = {"places": "Google Places"}


@dataclass(frozen=True, slots=True)
class Notice:
    """What the last request did: ``role`` is ``status`` or ``alert``."""

    role: str
    text: str


@dataclass(frozen=True, slots=True)
class Option:
    """One choice of a select: its value and its label."""

    value: str
    label: str


@dataclass(frozen=True, slots=True)
class RunRow:
    """One recent run, as the table shows it."""

    source: str
    campaign: str
    location: str
    status: str
    started_at: str
    seen: int
    created: int
    merged: int
    suppressed: int
    no_website: int
    market_blocked: int


@dataclass(frozen=True, slots=True)
class SubmittedRun:
    """What the form sent: the Campaign's id and the location's hash, as typed."""

    campaign_id: str
    location: str


def submitted_run(fields: FormFields) -> SubmittedRun:
    """The form's fields; ``MalformedForm`` for any other field, a missing or repeated one."""
    if set(fields) - {CSRF_FIELD} != RUN_FIELDS or any(
        len(values) != 1 for values in fields.values()
    ):
        raise MalformedForm(WRONG_FIELDS)
    return SubmittedRun(fields[CAMPAIGN_FIELD][0], fields[LOCATION_FIELD][0])


def campaign_options(campaigns: Iterable[SeededCampaign]) -> tuple[Option, ...]:
    """Each Campaign as a choice, by name."""
    options = (Option(campaign.id, campaign.name) for campaign in campaigns)
    return tuple(sorted(options, key=lambda option: (option.label.casefold(), option.value)))


def location_options(locations: Iterable[DiscoveryLocation]) -> tuple[Option, ...]:
    """Each location as a choice, in the setting's order."""
    return tuple(
        Option(location.hash, f"{location.location} ({location.country_code})")
        for location in locations
    )


def run_rows(runs: Iterable[StoredRun], names: dict[str, str]) -> tuple[RunRow, ...]:
    """Each run as a table row; ``names`` maps Campaign ids to names."""
    return tuple(
        RunRow(
            source=SOURCE_LABELS.get(run.source, run.source),
            campaign=names.get(run.campaign_id, UNKNOWN_CAMPAIGN),
            location=f"{run.location} ({run.country_code})",
            status=run.status,
            started_at=run.started_at.strftime("%Y-%m-%d %H:%M UTC"),
            seen=run.seen,
            created=run.created,
            merged=run.merged,
            suppressed=run.suppressed,
            no_website=run.no_website,
            market_blocked=run.market_blocked,
        )
        for run in runs
    )


def panel_context(
    *,
    campaigns: tuple[Option, ...],
    locations: tuple[Option, ...],
    runs: tuple[RunRow, ...],
    notice: Notice | None = None,
    chosen: SubmittedRun | None = None,
) -> dict[str, object]:
    """What ``discovery_panel.html`` renders."""
    return {
        "campaigns": campaigns,
        "locations": locations,
        "runs": runs,
        "notice": notice,
        "chosen_campaign": "" if chosen is None else chosen.campaign_id,
        "chosen_location": "" if chosen is None else chosen.location,
    }
