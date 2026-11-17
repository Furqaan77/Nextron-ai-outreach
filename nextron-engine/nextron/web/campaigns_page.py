"""The Campaigns section of the Settings page (FR-3, AD-4, AD-13, NFR-3, UX-DR3, UX-DR23; Story
4.2).

``templates/settings.html`` includes ``templates/campaigns_panel.html`` (``id="campaigns-panel"``)
after the settings form; ``create_app`` builds its context with ``panel_context`` from
``nextron.app.queries.setup`` in the GET's read-only unit of work. The routes are
``@personal_data(False)``: Campaigns, Offers, Sequences and the LIA reference are configuration,
never a person.

**The panel.** With no Campaign it reads "No Campaigns yet. Run nextron seed sync to load
them." Otherwise each Campaign, by key, shows its name, status, Segment, Market, Offer and
Sequence names (an id that names nothing stored shows "unknown Segment" and so on) and its
Variants ("A (active), B (inactive)"). A UK Campaign (its Market's country is ``GB``) also shows
"Legitimate-interests assessment: <ref> dated <date>" or "Legitimate-interests assessment: not
recorded". Its forms: ``draft`` has Activate (a UK Campaign's form adds ``lia_ref`` and a
``type="date"`` ``lia_date``, prefilled from what is stored); ``active`` has Pause; ``paused``
has Resume; a UK Campaign that isn't ``draft`` also has "Save the LIA". Every form posts with
``hx-post``, ``hx-target="#campaigns-panel"`` and ``hx-swap="outerHTML"``, and carries the
Campaign's id in a hidden ``campaign`` field.

**The POSTs** (``nextron.web.app``): ``/settings/campaigns/activate``, ``/pause``, ``/resume``
and ``/lia``. Their fields come through ``nextron.web.forms.form_fields`` and are checked here:
only ``campaign`` (a ULID), plus ``lia_ref`` and ``lia_date`` where the form has them
(optional on Activate, required on "Save the LIA"), each at most once; anything else raises
``MalformedForm`` (400). Each runs its ``Campaigns`` use case in its own unit of work, then
renders the panel alone in a read-only one.

**Outcomes.** A ``role="status"`` notice: "Activated <name>.", "Paused <name>: its Steps won't
be enrolled or enter the Approval Queue until you resume it.", "Resumed <name>.", "Saved the
legitimate-interests assessment for <name>." or "Nothing changed: the assessment already has
these values.". A ``CampaignRefused`` answers 200 with a ``role="alert"`` notice, "Nothing was
changed for <name>." (plain "Nothing was changed." when the id names no listed Campaign)
followed by the form-level problem, and the refused form's LIA fields re-rendered with their
submitted values, each refused field marked ``aria-invalid="true"`` with its reason beside it.

**A stored row that no longer decodes.** On ``GET /settings`` a Segment, Market, Sequence, Offer
or Campaign row that no longer validates doesn't take the page down: ``unavailable_context``
shows a ``role="alert"`` "The Campaigns can't be shown: <field>: <reason>" and no Campaign, and
the settings form renders as usual.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from nextron.app.queries.setup import (
    SeededCampaign,
    SeededMarket,
    SeededOffer,
    SeededSegment,
    SeededSequence,
)
from nextron.app.setup.api import LiaSaved
from nextron.domain.campaign import FORM_FIELD, CampaignStatus, needs_lia
from nextron.domain.ids import is_ulid
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

PANEL_TEMPLATE: Final = "campaigns_panel.html"
PANEL: Final = "campaigns_panel"  # the context name the template reads
CAMPAIGN: Final = "campaign"
LIA_REF: Final = "lia_ref"
LIA_DATE: Final = "lia_date"
LIA_FIELDS: Final = frozenset({LIA_REF, LIA_DATE})
ACTIVATE: Final = "activate"
PAUSE: Final = "pause"
RESUME: Final = "resume"
SAVE_LIA: Final = "lia"
# What a use case that went through did (an ``Outcome``'s kind).
ACTIVATED: Final = "activated"
PAUSED: Final = "paused"
RESUMED: Final = "resumed"
LIA_SAVED: Final = "lia_saved"
LIA_KEPT: Final = "lia_unchanged"
NOTHING_CHANGED: Final = "Nothing was changed."
UNAVAILABLE: Final = "The Campaigns can't be shown"
LIA_UNCHANGED: Final = "Nothing changed: the assessment already has these values."
LIA_NOT_RECORDED: Final = "Legitimate-interests assessment: not recorded"
UNKNOWN_SEGMENT: Final = "unknown Segment"
UNKNOWN_MARKET: Final = "unknown Market"
UNKNOWN_OFFER: Final = "unknown Offer"
UNKNOWN_SEQUENCE: Final = "unknown Sequence"
UNKNOWN_CAMPAIGN: Final = "the Campaign"
# The form each status shows: draft Activate, active Pause, paused Resume.
STATUS_ACTIONS: Final = {
    CampaignStatus.DRAFT: ACTIVATE,
    CampaignStatus.ACTIVE: PAUSE,
    CampaignStatus.PAUSED: RESUME,
}


@dataclass(frozen=True, slots=True)
class Outcome:
    """A use case that went through: what happened (``activated``, ``paused``, ``resumed``,
    ``lia_saved`` or ``lia_unchanged``) and to which Campaign."""

    kind: str
    campaign_id: str


@dataclass(frozen=True, slots=True)
class Refusal:
    """A refused POST: which form, the Campaign it named, the problems by field (``form`` for
    the whole form) and the LIA fields it submitted (``None`` when it had none)."""

    action: str
    campaign_id: str
    problems: Mapping[str, str]
    lia_ref: str | None = None
    lia_date: str | None = None


@dataclass(frozen=True, slots=True)
class LiaForm:
    """The LIA fields of an Activate or "Save the LIA" form: their element-id ``prefix``, their
    values and each field's problem."""

    prefix: str
    values: Mapping[str, str]
    errors: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CampaignView:
    """One Campaign as the panel shows it, with the form its status offers (``action``) and its
    LIA forms (``None`` where it has none)."""

    id: str
    name: str
    status: str
    segment: str
    market: str
    offer: str
    sequence: str
    variants: str
    lia_text: str | None
    action: str | None
    activate_lia: LiaForm | None
    save_lia: LiaForm | None


@dataclass(frozen=True, slots=True)
class Panel:
    """What ``campaigns_panel.html`` renders."""

    notice: str | None
    alert: str | None
    campaigns: tuple[CampaignView, ...]
    unavailable: bool = False  # a stored row didn't decode: the alert says which


# Submitted forms ---------------------------------------------------------------------------------


def _single(
    fields: FormFields, required: frozenset[str], optional: frozenset[str] = frozenset()
) -> dict[str, str]:
    """The form's fields, each given once: ``campaign`` a ULID, the ``required`` ones present,
    nothing outside ``required`` and ``optional``; ``MalformedForm`` otherwise."""
    names = set(fields) - {CSRF_FIELD}
    if not names >= required or names - required - optional:
        raise MalformedForm(WRONG_FIELDS)
    single = {}
    for name in sorted(names):
        values = fields[name]
        if len(values) != 1:
            raise MalformedForm(REPEATED_FIELD)
        single[name] = values[0]
    if not is_ulid(single[CAMPAIGN]):
        raise MalformedForm(WRONG_FIELDS)
    return single


def submitted_activate(fields: FormFields) -> tuple[str, str | None, str | None]:
    """The Activate form's Campaign id and its LIA fields (``None`` when not submitted)."""
    single = _single(fields, frozenset({CAMPAIGN}), LIA_FIELDS)
    return single[CAMPAIGN], single.get(LIA_REF), single.get(LIA_DATE)


def submitted_campaign(fields: FormFields) -> str:
    """The Pause or Resume form's Campaign id."""
    return _single(fields, frozenset({CAMPAIGN}))[CAMPAIGN]


def submitted_lia(fields: FormFields) -> tuple[str, str, str]:
    """The "Save the LIA" form's Campaign id, reference and date."""
    single = _single(fields, frozenset({CAMPAIGN, *LIA_FIELDS}))
    return single[CAMPAIGN], single[LIA_REF], single[LIA_DATE]


# Notices -----------------------------------------------------------------------------------------


def lia_outcome(saved: object, campaign_id: str) -> Outcome:
    """The outcome of "Save the LIA": saved, or nothing changed (``LiaSaved.changed``)."""
    changed = isinstance(saved, LiaSaved) and saved.changed
    return Outcome(LIA_SAVED if changed else LIA_KEPT, campaign_id)


def outcome_notice(outcome: Outcome, name: str) -> str:
    """The ``role="status"`` notice of a use case that went through."""
    if outcome.kind == ACTIVATED:
        return f"Activated {name}."
    if outcome.kind == PAUSED:
        return (
            f"Paused {name}: its Steps won't be enrolled or enter the Approval Queue until you "
            "resume it."
        )
    if outcome.kind == RESUMED:
        return f"Resumed {name}."
    if outcome.kind == LIA_SAVED:
        return f"Saved the legitimate-interests assessment for {name}."
    return LIA_UNCHANGED


def refused_notice(refusal: Refusal, name: str | None = None) -> str:
    """The ``role="alert"`` notice of a refused POST: nothing was changed (for ``name``, the
    refused Campaign's, when it is a listed one), and the form-level problem when there is
    one."""
    problem = refusal.problems.get(FORM_FIELD)
    head = NOTHING_CHANGED if name is None else f"Nothing was changed for {name}."
    return f"{head} {problem}." if problem else head


# The panel ---------------------------------------------------------------------------------------


def _variants_text(campaign: SeededCampaign) -> str:
    return ", ".join(f"{variant.name} ({variant.status})" for variant in campaign.variants)


def _lia_form(campaign: SeededCampaign, action: str, refusal: Refusal | None) -> LiaForm:
    """An LIA form, holding the refused submission when it was this form's, else the stored
    LIA."""
    prefix = f"campaign-{campaign.id}-{action}"
    if refusal is not None and refusal.action == action and refusal.campaign_id == campaign.id:
        values = {LIA_REF: refusal.lia_ref or "", LIA_DATE: refusal.lia_date or ""}
        errors = {name: reason for name, reason in refusal.problems.items() if name in LIA_FIELDS}
        return LiaForm(prefix, values, errors)
    lia = campaign.lia
    if lia is None:
        return LiaForm(prefix, {LIA_REF: "", LIA_DATE: ""})
    return LiaForm(prefix, {LIA_REF: lia.ref, LIA_DATE: lia.date.isoformat()})


def _view(
    campaign: SeededCampaign,
    names: tuple[Mapping[str, str], Mapping[str, str], Mapping[str, str], Mapping[str, str]],
    countries: Mapping[str, str],
    refusal: Refusal | None,
) -> CampaignView:
    segment_names, market_names, offer_names, sequence_names = names
    uk = needs_lia(countries.get(campaign.market_id))
    lia_text = None
    if uk:
        lia = campaign.lia
        lia_text = (
            LIA_NOT_RECORDED
            if lia is None
            else f"Legitimate-interests assessment: {lia.ref} dated {lia.date.isoformat()}"
        )
    action = STATUS_ACTIONS.get(campaign.status)
    return CampaignView(
        id=campaign.id,
        name=campaign.name,
        status=str(campaign.status),
        segment=segment_names.get(campaign.segment_id, UNKNOWN_SEGMENT),
        market=market_names.get(campaign.market_id, UNKNOWN_MARKET),
        offer=offer_names.get(campaign.offer_id, UNKNOWN_OFFER),
        sequence=sequence_names.get(campaign.sequence_id, UNKNOWN_SEQUENCE),
        variants=_variants_text(campaign),
        lia_text=lia_text,
        action=action,
        activate_lia=_lia_form(campaign, ACTIVATE, refusal) if uk and action == ACTIVATE else None,
        save_lia=(
            _lia_form(campaign, SAVE_LIA, refusal)
            if uk and campaign.status != CampaignStatus.DRAFT
            else None
        ),
    )


def panel_context(
    campaigns: Sequence[SeededCampaign],
    segments: Sequence[SeededSegment],
    markets: Sequence[SeededMarket],
    offers: Sequence[SeededOffer],
    sequences: Sequence[SeededSequence],
    *,
    outcome: Outcome | None = None,
    refusal: Refusal | None = None,
) -> dict[str, object]:
    """The context ``campaigns_panel.html`` reads (``campaigns_panel``): the notice or alert,
    and each Campaign."""
    names = (
        {found.id: found.definition.name for found in segments},
        {found.id: found.definition.name for found in markets},
        {found.id: found.definition.name for found in offers},
        {found.id: found.definition.name for found in sequences},
    )
    countries = {found.id: found.definition.country_code for found in markets}
    listed = {found.id: found.name for found in campaigns}
    notice = None
    if outcome is not None:
        notice = outcome_notice(outcome, listed.get(outcome.campaign_id, UNKNOWN_CAMPAIGN))
    panel = Panel(
        notice=notice,
        alert=None if refusal is None else refused_notice(refusal, listed.get(refusal.campaign_id)),
        campaigns=tuple(_view(found, names, countries, refusal) for found in campaigns),
    )
    return {PANEL: panel}


def unavailable_context(problem: str) -> dict[str, object]:
    """The panel when a stored row it reads no longer decodes (``problem`` is the error's
    ``<field>: <reason>``): a ``role="alert"`` notice and no Campaign, so the rest of the
    Settings page still works."""
    alert = f"{UNAVAILABLE}: {problem}"
    return {PANEL: Panel(notice=None, alert=alert, campaigns=(), unavailable=True)}
