"""Offer validation (Story 9.6; FR-69, FR-54, NFR-49, NFR-50): the Calls report's figures.

``offer_validation()`` reads, through the current unit of work (a read-only one included), the
unsuperseded ``call_outcomes`` rows of engine-sourced Leads and the Pipeline Stage ``events``
rows only (``nextron.app.queries.pipeline.call_report_facts``), and returns
``offer_validation.build_report``'s ``OfferReport``. It writes nothing: no table, event, job,
notification, alert or email.

The rules (``nextron.domain.offer_validation``): a Lead is counted when its creation ``events``
row has an engine source's cause (NFR-50: only engine-sourced Leads count). Booked is every
unsuperseded booking, held and no-shows those with that recorded outcome. From 10 held calls
the report shows audits proposed (and their share of held calls), audits accepted (distinct
Leads with a held call that reached ``audit_won``), the proposed price range and median, the
price reactions, the qualified share and the top 3 objections; with fewer than 2 audits
accepted, it raises the NFR-49 checkpoint "Review the Offer's price and scope before the next
Variant".
"""

from nextron.app.queries import pipeline as pipeline_queries
from nextron.domain.offer_validation import OfferReport, build_report


def offer_validation() -> OfferReport:
    """The Calls report's figures; see the module docstring."""
    facts, accepted = pipeline_queries.call_report_facts()
    return build_report(facts, accepted)
