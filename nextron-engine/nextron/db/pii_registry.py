"""The personal-data registry (AD-17): every column of every model, classified.

``PII`` maps ``table -> {column: PiiClass}``. The class says what erasure does to the column:

- ``none``: holds no personal data (IDs, statuses, codes, times, counts); left as it is.
- ``redact``: free text that may hold personal data; becomes ``"[erased]"``.
- ``null``: personal data with no text to keep; set to ``NULL``.
- ``keep_hash``: personal data replaced by a hash, so suppression and dedupe still match.

Every column of ``nextron.db.models.Base.metadata`` is classified exactly once and nothing else
is listed; ``tests/contracts/test_persistence_contracts.py`` fails otherwise. ``events`` never
holds personal data, so every one of its columns is ``none``.

``HASH_KINDS`` (Story 3.13) maps each ``keep_hash`` column, as ``(table, column)``, to the
suppression key kind its value is hashed as (``nextron.domain.erasure.tombstone_hash``), or to
``None`` when the column already holds a hash. ``nextron.db.erasure.tombstoned`` reads both;
``tests/contracts/test_erasure_contracts.py`` fails when a ``keep_hash`` column isn't listed
here, or a listed one isn't ``keep_hash``.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class PiiClass(StrEnum):
    NONE = "none"
    REDACT = "redact"
    NULL = "null"
    KEEP_HASH = "keep_hash"


PII: Final[dict[str, dict[str, PiiClass]]] = {
    "events": {
        "id": PiiClass.NONE,
        "entity": PiiClass.NONE,
        "entity_id": PiiClass.NONE,
        "from_status": PiiClass.NONE,
        "to_status": PiiClass.NONE,
        "cause": PiiClass.NONE,
        "cause_ref": PiiClass.NONE,
        "actor": PiiClass.NONE,
        "at": PiiClass.NONE,
    },
    # Later keys hold Nextron staff addresses and the postal address (Story 1.6).
    "settings": {
        "id": PiiClass.NONE,
        "key": PiiClass.NONE,
        "value": PiiClass.REDACT,
        "updated_at": PiiClass.NONE,
    },
    # Story 2.1. A Segment's definition holds role titles, keywords and rules, never a person.
    "segments": {
        "id": PiiClass.NONE,
        "key": PiiClass.NONE,
        "definition": PiiClass.NONE,
        "version": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # Story 11.5. Each version of a Segment's definition, as immutable rows: never a person.
    "segment_versions": {
        "id": PiiClass.NONE,
        "segment_id": PiiClass.NONE,
        "version": PiiClass.NONE,
        "definition": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "actor": PiiClass.NONE,
    },
    # Story 3.1. A Market's rules and footer template hold codes, flags and placeholders, never
    # a person.
    "markets": {
        "id": PiiClass.NONE,
        "code": PiiClass.NONE,
        "name": PiiClass.NONE,
        "enabled": PiiClass.NONE,
        "country_code": PiiClass.NONE,
        "cctlds": PiiClass.NONE,
        "cold_email_allowed": PiiClass.NONE,
        "eligible_legal_forms": PiiClass.NONE,
        "free_webmail_blocked": PiiClass.NONE,
        "default_timezone": PiiClass.NONE,
        "unknown_timezone": PiiClass.NONE,
        "unknown_send_window": PiiClass.NONE,
        "english_variant": PiiClass.NONE,
        "footer_template": PiiClass.NONE,
        "privacy_line_required": PiiClass.NONE,
        "version": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # Story 4.1. A Sequence and its Step briefs hold rules, limits and angle texts, never a
    # person.
    "sequences": {
        "id": PiiClass.NONE,
        "key": PiiClass.NONE,
        "name": PiiClass.NONE,
        "version": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    "step_briefs": {
        "id": PiiClass.NONE,
        "sequence_id": PiiClass.NONE,
        "step": PiiClass.NONE,
        "delay_days": PiiClass.NONE,
        "purpose": PiiClass.NONE,
        "word_limit": PiiClass.NONE,
        "word_comparison": PiiClass.NONE,
        "allowed_content": PiiClass.NONE,
        "ask": PiiClass.NONE,
        "link_rule": PiiClass.NONE,
        "no_price": PiiClass.NONE,
        "no_audit": PiiClass.NONE,
        "subject_rule": PiiClass.NONE,
        "segment_angles": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # Story 2.2. A Lead is an organization: its name, keys and country are firm data, so erasing a
    # Contact never tombstones the firm's Lead.
    "leads": {
        "id": PiiClass.NONE,
        "name": PiiClass.NONE,
        "web_domain": PiiClass.NONE,
        "ch_number": PiiClass.NONE,
        "place_id": PiiClass.NONE,
        "segment_id": PiiClass.NONE,
        "country_code": PiiClass.NONE,
        "merged_into": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "pipeline_stage": PiiClass.NONE,
        "hq_tz": PiiClass.NONE,  # Story 2.9: a zone and an Evidence id, firm data
        "hq_evidence_id": PiiClass.NONE,
        # Story 2.6: the methods a firm's website offers and the URLs of its public pages
        "contact_methods": PiiClass.NONE,
        "no_decision_maker": PiiClass.NONE,  # Story 2.10: a flag about the firm
        # Story 2.12: the firm's Companies House type and status, and two Evidence ids
        "legal_form": PiiClass.NONE,
        "company_status": PiiClass.NONE,
        "legal_form_status": PiiClass.NONE,
        "legal_form_site_evidence_id": PiiClass.NONE,
        "legal_form_registry_evidence_id": PiiClass.NONE,
        # Story 2.15: the firm's scores, its Tier and the ids and keys behind them
        "fit_score": PiiClass.NONE,
        "signal_score": PiiClass.NONE,
        "lead_score": PiiClass.NONE,
        "tier": PiiClass.NONE,
        "tier_computed_at": PiiClass.NONE,
        "score_segment_version": PiiClass.NONE,
        "score_factors": PiiClass.NONE,
        "disqualified_rule": PiiClass.NONE,
        "disqualified_evidence": PiiClass.NONE,
        # Story 8.2: an organization's location from Google Places, kept at most 30 days
        "place_lat": PiiClass.NONE,
        "place_lng": PiiClass.NONE,
        "place_coords_at": PiiClass.NONE,
        # Story 9.1: the deal value is the firm's; the Operator's notes are free text that may
        # name a person, so erasing a Contact redacts its Lead's notes
        "deal_value": PiiClass.NONE,
        "notes": PiiClass.REDACT,
    },
    # Story 9.1. A booked call holds IDs, a zone, an actor code and times; the Contact is kept
    # as its id only.
    "call_outcomes": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "contact_id": PiiClass.NONE,
        "scheduled_at": PiiClass.NONE,
        "prospect_tz": PiiClass.NONE,
        "booked_at": PiiClass.NONE,
        "booked_by": PiiClass.NONE,
        "superseded_at": PiiClass.NONE,
        # Story 9.4: the outcome. Codes, flags, an amount, a date and times, except the two
        # free texts (why not qualified, the next step), which may name a person: erasing a
        # Contact redacts them on its Lead's (and merged Leads') rows
        "outcome": PiiClass.NONE,
        "qualified": PiiClass.NONE,
        "reason": PiiClass.REDACT,
        "next_step": PiiClass.REDACT,
        "next_step_on": PiiClass.NONE,
        "audit_proposed": PiiClass.NONE,
        "audit_price": PiiClass.NONE,
        "objections": PiiClass.NONE,
        "price_reaction": PiiClass.NONE,
        "recorded_at": PiiClass.NONE,
        "recorded_by": PiiClass.NONE,
    },
    # Story 2.8. A prompt is the engine's own text, never a person's; an activation holds codes,
    # a hash and times.
    "prompt_snapshots": {
        "id": PiiClass.NONE,
        "route": PiiClass.NONE,
        "prompt_version": PiiClass.NONE,
        "sha256": PiiClass.NONE,
        "text": PiiClass.NONE,
        "created_at": PiiClass.NONE,
    },
    "ai_route_activations": {
        "id": PiiClass.NONE,
        "route": PiiClass.NONE,
        "model": PiiClass.NONE,
        "effort": PiiClass.NONE,
        "prompt_version": PiiClass.NONE,
        "prompt_sha256": PiiClass.NONE,
        "playbook_sha256": PiiClass.NONE,
        "schema_version": PiiClass.NONE,
        "actor": PiiClass.NONE,
        "bootstrap": PiiClass.NONE,
        "test_run_id": PiiClass.NONE,
        "activated_at": PiiClass.NONE,
        "deactivated_at": PiiClass.NONE,
    },
    # Story 4.4. The Sales Playbook is Nextron's own text; a candidate tuple and a Playbook check
    # hold codes, hashes and times.
    "playbook_snapshots": {
        "id": PiiClass.NONE,
        "sha256": PiiClass.NONE,
        "version": PiiClass.NONE,
        "content": PiiClass.NONE,
        "captured_at": PiiClass.NONE,
    },
    "ai_route_tuples": {
        "id": PiiClass.NONE,
        "route": PiiClass.NONE,
        "model": PiiClass.NONE,
        "effort": PiiClass.NONE,
        "prompt_version": PiiClass.NONE,
        "prompt_sha256": PiiClass.NONE,
        "playbook_sha256": PiiClass.NONE,
        "schema_version": PiiClass.NONE,
        "created_at": PiiClass.NONE,
    },
    "playbook_checks": {
        "id": PiiClass.NONE,
        "checked_at": PiiClass.NONE,
        "trigger": PiiClass.NONE,
        "playbook_sha256": PiiClass.NONE,
        "problem": PiiClass.NONE,
    },
    # Story 4.5. Fixture cases are synthetic (invented firms and people on reserved domains);
    # runs hold codes, counts, metrics and the cases' verdicts. Story 4.6 (decision 7): an
    # Operator's override case holds a pseudonymised Reply, still personal data, so its input is
    # redacted when its Contact is erased (Playbook.erase_override_cases); contact_id is the
    # erasure's key, and the expected class and covers tags name no one.
    "ai_test_cases": {
        "id": PiiClass.NONE,
        "case_key": PiiClass.NONE,
        "test_set": PiiClass.NONE,
        "route": PiiClass.NONE,
        "input": PiiClass.REDACT,
        "expected": PiiClass.NONE,
        "covers": PiiClass.NONE,
        "sha256": PiiClass.NONE,
        "loaded_at": PiiClass.NONE,
        "retired_at": PiiClass.NONE,
        "origin": PiiClass.NONE,
        "contact_id": PiiClass.NONE,
    },
    "ai_test_runs": {
        "id": PiiClass.NONE,
        "tuple_id": PiiClass.NONE,
        "test_set": PiiClass.NONE,
        "outcome": PiiClass.NONE,
        "error": PiiClass.NONE,
        "case_count": PiiClass.NONE,
        "passed_count": PiiClass.NONE,
        "metrics": PiiClass.NONE,
        "results": PiiClass.NONE,
        "cost_micros": PiiClass.NONE,
        "started_at": PiiClass.NONE,
        "finished_at": PiiClass.NONE,
        "cases_sha256": PiiClass.NONE,
    },
    # Story 2.3. A Contact is a person: free text is redacted, the address is kept as a hash so
    # suppression and dedupe still match after an erasure, and the LinkedIn URL is nulled.
    "contacts": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "name": PiiClass.REDACT,
        "role": PiiClass.REDACT,
        "email": PiiClass.KEEP_HASH,
        "address_class": PiiClass.NONE,
        "linkedin_url": PiiClass.NULL,
        "is_primary": PiiClass.NONE,
        "tz": PiiClass.NONE,
        "market_id": PiiClass.NONE,
        # Story 3.2: why the Market and timezone apply (codes only), and the office's place
        "market_basis": PiiClass.NONE,
        "tz_basis": PiiClass.NONE,
        "office_country": PiiClass.NULL,
        "office_region": PiiClass.NULL,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
        "primary_set_by": PiiClass.NONE,  # Story 2.10: who chose the Primary (a code)
        "erased_at": PiiClass.NONE,  # Story 3.13: when the Contact was erased
        # Story 2.13: the finders tried and the search's outcome (codes), and when it ran
        "email_search_tried": PiiClass.NONE,
        "email_search_outcome": PiiClass.NONE,
        "email_search_at": PiiClass.NONE,
        # Story 2.14: the address's last check (codes) and when it ran, and when the verifier
        # answered
        "email_check_result": PiiClass.NONE,
        "email_checked_at": PiiClass.NONE,
        "email_invalid_reason": PiiClass.NONE,
        "email_verifier_at": PiiClass.NONE,
    },
    # Story 2.13. What the engine learned about a mail domain: a firm's domain, a pattern code
    # and Evidence ids, never a person's name or address.
    "email_domains": {
        "id": PiiClass.NONE,
        "domain": PiiClass.NONE,
        "pattern": PiiClass.NONE,
        "pattern_source": PiiClass.NONE,
        "pattern_evidence_ids": PiiClass.NONE,
        "pattern_learned_at": PiiClass.NONE,
        "domain_search_at": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # Story 2.3. A record's source detail (a CSV's origin, a URL) and notes are free text; its
    # address is the Contact's address at the time, kept as a hash like contacts.email.
    "provenance_records": {
        "id": PiiClass.NONE,
        "contact_id": PiiClass.NONE,
        "subject": PiiClass.NONE,
        "source_kind": PiiClass.NONE,
        "source_detail": PiiClass.REDACT,
        "address": PiiClass.KEEP_HASH,
        "retrieved_at": PiiClass.NONE,
        "address_class": PiiClass.NONE,
        "notes": PiiClass.REDACT,
        "basis_rule": PiiClass.NONE,
        "basis_campaign_id": PiiClass.NONE,
        "lia_ref": PiiClass.NONE,
        "lia_date": PiiClass.NONE,
        "basis_recorded_at": PiiClass.NONE,
    },
    # Story 4.2. Offers, Campaigns and Variants are configuration; the LIA reference names the
    # Operator's own assessment record, never a person.
    "offers": {
        "id": PiiClass.NONE,
        "key": PiiClass.NONE,
        "name": PiiClass.NONE,
        "ladder": PiiClass.NONE,
        "version": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    "campaigns": {
        "id": PiiClass.NONE,
        "key": PiiClass.NONE,
        "name": PiiClass.NONE,
        "segment_id": PiiClass.NONE,
        "market_id": PiiClass.NONE,
        "offer_id": PiiClass.NONE,
        "sequence_id": PiiClass.NONE,
        "status": PiiClass.NONE,
        "lia_ref": PiiClass.NONE,
        "lia_date": PiiClass.NONE,
        "version": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    "variants": {
        "id": PiiClass.NONE,
        "campaign_id": PiiClass.NONE,
        "name": PiiClass.NONE,
        "status": PiiClass.NONE,
        "step_angles": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # Story 4.3. Approved Company Facts are claims about Nextron, its work, its Offer and public
    # regulation, never about a person.
    "company_facts": {
        "id": PiiClass.NONE,
        "fact_id": PiiClass.NONE,
        "version": PiiClass.NONE,
        "type": PiiClass.NONE,
        "wording": PiiClass.NONE,
        "source_url": PiiClass.NONE,
        "allowed_segment_ids": PiiClass.NONE,
        "allowed_market_ids": PiiClass.NONE,
        "metric": PiiClass.NONE,
        "metric_confirmed_at": PiiClass.NONE,
        "scope_note": PiiClass.NONE,
        "last_checked_on": PiiClass.NONE,
        "created_by": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "retracted_at": PiiClass.NONE,
    },
    # Story 4.7. Nextron's own sender identity (its Sending Domains, its inboxes' addresses,
    # labels and From name), never a data subject's: erasing a Contact never rewrites it.
    "sending_domains": {
        "id": PiiClass.NONE,
        "domain": PiiClass.NONE,
        "created_at": PiiClass.NONE,
    },
    "sending_inboxes": {
        "id": PiiClass.NONE,
        "address": PiiClass.NONE,
        "label": PiiClass.NONE,
        "sending_domain_id": PiiClass.NONE,
        "from_name": PiiClass.NONE,
        "status": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        # Story 6.4: the inbox's warm-up record and Daily Cap ramp (a date, a method or tool
        # name, and counts), about Nextron's own inbox.
        "warmup_started_on": PiiClass.NONE,
        "warmup_method": PiiClass.NONE,
        "warm_level": PiiClass.NONE,
        "warmup_tool_daily": PiiClass.NONE,
        "ramp_step": PiiClass.NONE,
        "ramp_target": PiiClass.NONE,
    },
    # Story 8.3. A discovery run: the Operator's Campaign and location setting, ids, a hash of the
    # Segment's queries, a status, times and counts. Nothing a Places search returned.
    "discovery_runs": {
        "id": PiiClass.NONE,
        "source": PiiClass.NONE,
        "campaign_id": PiiClass.NONE,
        "segment_id": PiiClass.NONE,
        "country_code": PiiClass.NONE,
        "location": PiiClass.NONE,
        "query_hash": PiiClass.NONE,
        "status": PiiClass.NONE,
        "started_at": PiiClass.NONE,
        "finished_at": PiiClass.NONE,
        "seen": PiiClass.NONE,
        "created": PiiClass.NONE,
        "merged": PiiClass.NONE,
        "suppressed": PiiClass.NONE,
        "no_website": PiiClass.NONE,
        "market_blocked": PiiClass.NONE,
    },
    # Story 6.5. A Sending Domain's DNS authentication results: IDs, flags, reason codes and a
    # time, about Nextron's own domains.
    "domain_checks": {
        "id": PiiClass.NONE,
        "sending_domain_id": PiiClass.NONE,
        "checked_at": PiiClass.NONE,
        "spf_ok": PiiClass.NONE,
        "dkim_ok": PiiClass.NONE,
        "dmarc_ok": PiiClass.NONE,
        "aligned_ok": PiiClass.NONE,
        "passed": PiiClass.NONE,
        "details": PiiClass.NONE,
    },
    # Story 4.8. An Enrollment holds IDs, codes and times only: who it is lives in contacts.
    "enrollments": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "contact_id": PiiClass.NONE,
        "campaign_id": PiiClass.NONE,
        "variant_id": PiiClass.NONE,
        "sending_inbox_id": PiiClass.NONE,
        "sequence_id": PiiClass.NONE,
        "sequence_version": PiiClass.NONE,
        "offer_id": PiiClass.NONE,
        "origin": PiiClass.NONE,
        "status": PiiClass.NONE,
        "pause_cause": PiiClass.NONE,
        "stop_cause": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
        "last_step_sent_at": PiiClass.NONE,
        "ended_at": PiiClass.NONE,
    },
    "variant_counters": {
        "id": PiiClass.NONE,
        "campaign_id": PiiClass.NONE,
        "last_variant_id": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # Story 4.9. A Draft slot holds IDs, codes, numbers, flags and times; its text lives in
    # draft_revisions (Story 4.12).
    "drafts": {
        "id": PiiClass.NONE,
        "kind": PiiClass.NONE,
        "enrollment_id": PiiClass.NONE,
        "step_no": PiiClass.NONE,
        "day_offset": PiiClass.NONE,
        "current_rev": PiiClass.NONE,
        "due_at": PiiClass.NONE,
        "generic": PiiClass.NONE,
        "stale": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
        "status": PiiClass.NONE,
        "reapproval_required": PiiClass.NONE,
        "event_expired": PiiClass.NONE,
        "skipped_at": PiiClass.NONE,
        "status_at": PiiClass.NONE,
    },
    # Story 2.5. A page's visible text may quote a person, so an erasure nulls it (Story 3.13);
    # the URLs are of an organization's public pages, and the rest are IDs, codes, counts,
    # hashes and times.
    "crawl_runs": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "site_domain": PiiClass.NONE,
        "final_domain": PiiClass.NONE,
        "started_at": PiiClass.NONE,
        "ended_at": PiiClass.NONE,
        "outcome": PiiClass.NONE,
        "error_code": PiiClass.NONE,
        "pages_fetched": PiiClass.NONE,
        "pages_skipped_robots": PiiClass.NONE,
        "renders": PiiClass.NONE,
        "errors": PiiClass.NONE,
        # Story 2.11: counts of the postings and hiring Events each check found
        "careers_postings": PiiClass.NONE,
        "careers_events": PiiClass.NONE,
        "greenhouse_postings": PiiClass.NONE,
        "greenhouse_events": PiiClass.NONE,
        "lever_postings": PiiClass.NONE,
        "lever_events": PiiClass.NONE,
        "ashby_postings": PiiClass.NONE,
        "ashby_events": PiiClass.NONE,
        "board_errors": PiiClass.NONE,
    },
    "crawled_pages": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "run_id": PiiClass.NONE,
        "url": PiiClass.NONE,
        "final_url": PiiClass.NONE,
        "kind": PiiClass.NONE,
        "fetched_at": PiiClass.NONE,
        "http_status": PiiClass.NONE,
        "content_hash": PiiClass.NONE,
        # Story 3.13: an erasure nulls the text and keeps the hash, as purge_page_text does
        "text": PiiClass.NULL,
        "text_purge_after": PiiClass.NONE,
        "changed_at": PiiClass.NONE,
        "tag_managers": PiiClass.NONE,  # Story 2.9: tag manager keys, codes only
        "mailto": PiiClass.NULL,  # Story 2.13: published addresses (JSON), purged with the text
    },
    "ats_links": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "run_id": PiiClass.NONE,
        "page_id": PiiClass.NONE,
        "url": PiiClass.NONE,
        "host": PiiClass.NONE,
        "tag": PiiClass.NONE,
        "first_seen_at": PiiClass.NONE,
        "last_seen_at": PiiClass.NONE,
    },
    # Story 2.11. A posting's title or description may name a person (a hiring manager, a
    # recruiter to write to), so both are redacted; the URL is an organization's public posting,
    # and the rest are IDs, codes, dates and times.
    "signal_inbox_items": {  # Story 2.16: the pasted item and what names its author
        "id": PiiClass.NONE,
        "url": PiiClass.REDACT,  # a post's URL often carries its author's name
        "text": PiiClass.REDACT,
        "author": PiiClass.REDACT,
        "company": PiiClass.REDACT,
        "status": PiiClass.NONE,
        "flag_reason": PiiClass.NONE,
        "submitted_at": PiiClass.NONE,
        "processed_at": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "parsed_segment": PiiClass.NONE,
        "parsed_signal_type": PiiClass.NONE,
        "parsed_website": PiiClass.NONE,
        "parsed_author_name": PiiClass.REDACT,
        "parsed_author_role": PiiClass.REDACT,
        "parsed_event_date": PiiClass.NONE,
        "parsed_quote": PiiClass.REDACT,
        "extracted_by": PiiClass.NONE,
    },
    "job_postings": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "source": PiiClass.NONE,
        "board_token": PiiClass.NONE,
        "url": PiiClass.NONE,
        "title": PiiClass.REDACT,
        "description": PiiClass.REDACT,
        "posted_date": PiiClass.NONE,
        "first_seen_at": PiiClass.NONE,
        "last_checked_at": PiiClass.NONE,
        "status": PiiClass.NONE,
        "closed_at": PiiClass.NONE,
        "signal_id": PiiClass.NONE,
        "evidence_id": PiiClass.NONE,
    },
    # Story 2.6. What matched may be a phone number or a page's own words, so it is redacted;
    # it is not purged with the page text. The rest are IDs, codes and times.
    "page_contact_methods": {
        "id": PiiClass.NONE,
        "page_id": PiiClass.NONE,
        "method": PiiClass.NONE,
        "signature": PiiClass.NONE,
        "source": PiiClass.NONE,
        "matched": PiiClass.REDACT,
        "detected_at": PiiClass.NONE,
    },
    # Story 3.3. The Suppression List keeps only a key's hash (kept after an erasure, so an
    # erased person stays suppressed) and, for a firm's domain, the domain itself; the void
    # reason is the Operator's free text.
    "suppression_entries": {
        "id": PiiClass.NONE,
        "kind": PiiClass.NONE,
        "value_hash": PiiClass.KEEP_HASH,
        "domain_plain": PiiClass.NONE,
        "status": PiiClass.NONE,
        "source": PiiClass.NONE,
        "cause_ref": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "voided_at": PiiClass.NONE,
        "void_reason": PiiClass.REDACT,
    },
    # Story 4.11. A footer's text holds the sender's name and Nextron's postal address, like
    # settings.value, so erasure redacts it.
    "footer_snapshots": {
        "id": PiiClass.NONE,
        "market_id": PiiClass.NONE,
        "sha256": PiiClass.NONE,
        "text": PiiClass.REDACT,
        "created_at": PiiClass.NONE,
    },
    # Story 10.7. The prospect's WhatsApp number and Instagram handle are the Contact's
    # personal data: erasure NULLs them. The Evidence is research's row (evidence.quote);
    # every other column is an ID, a code or a time.
    "channel_permissions": {
        "id": PiiClass.NONE,
        "contact_id": PiiClass.NONE,
        "channel": PiiClass.NONE,
        "prospect_action": PiiClass.NONE,
        "occurred_at": PiiClass.NONE,
        "evidence_id": PiiClass.NONE,
        "whatsapp_number": PiiClass.NULL,
        "instagram_handle": PiiClass.NULL,
        "last_inbound_at": PiiClass.NONE,
        "status": PiiClass.NONE,
        "revoked_at": PiiClass.NONE,
        "recorded_by": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # Story 4.12. A Draft's subject, body and regenerate instruction are written to a person and
    # may name them, so erasure redacts them; text_hash is already a hash of the message (to
    # address included), so it is kept; the rest are IDs, codes, versions and times.
    "draft_revisions": {
        "id": PiiClass.NONE,
        "draft_id": PiiClass.NONE,
        "rev": PiiClass.NONE,
        "subject": PiiClass.REDACT,
        "body": PiiClass.REDACT,
        "footer_snapshot_id": PiiClass.NONE,
        "evidence_ids": PiiClass.NONE,
        "fact_versions": PiiClass.NONE,
        "cited_signal_id": PiiClass.NONE,
        "origin": PiiClass.NONE,
        "route_tuple_id": PiiClass.NONE,
        "playbook_sha256": PiiClass.NONE,
        "playbook_version": PiiClass.NONE,
        "prompt_version": PiiClass.NONE,
        "instruction": PiiClass.REDACT,
        "text_hash": PiiClass.KEEP_HASH,
        "created_at": PiiClass.NONE,
    },
    # Story 10.2. The account's label and owner name may name a Nextron staff member, never a
    # Contact; the role, status, caps and times hold no personal data.
    "linkedin_accounts": {
        "id": PiiClass.NONE,
        "label": PiiClass.REDACT,
        "owner_name": PiiClass.REDACT,
        "owner_role": PiiClass.NONE,
        "status": PiiClass.NONE,
        "tasks_per_weekday": PiiClass.NONE,
        "daily_invite_cap": PiiClass.NONE,
        "weekly_invite_cap": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # Story 4.10: dates, counts and times only.
    "daily_capacity": {
        "id": PiiClass.NONE,
        "local_date": PiiClass.NONE,
        "total_cold_capacity": PiiClass.NONE,
        "follow_ups_reserved": PiiClass.NONE,
        "new_sequence_capacity": PiiClass.NONE,
        "picked": PiiClass.NONE,
        "signal_shortfall": PiiClass.NONE,
        "computed_at": PiiClass.NONE,
    },
    # Story 3.8. Only the token's hash is stored, never the token, the address or its hash:
    # the token is the only way to its address hash, and that needs k_tok.
    "unsub_tokens": {
        "id": PiiClass.NONE,
        "token_hash": PiiClass.NONE,
        "om_id": PiiClass.NONE,
        "kid": PiiClass.NONE,
        "issued_at": PiiClass.NONE,
    },
    # Story 3.11. An applied unsubscribe request keeps only its token's hash and two times,
    # never the token, the address or its hash.
    "unsub_requests": {
        "id": PiiClass.NONE,
        "token_hash": PiiClass.NONE,
        "received_at": PiiClass.NONE,
        "processed_at": PiiClass.NONE,
    },
    # Story 3.12. How the unsubscribe poll went: times and a closed code, never a token, an
    # address, the Worker host or the bearer.
    "unsub_poll_state": {
        "id": PiiClass.NONE,
        "last_success_at": PiiClass.NONE,
        "failing_since": PiiClass.NONE,
        "last_failure_at": PiiClass.NONE,
        "last_failure_code": PiiClass.NONE,
    },
    # Story 3.13. A data subject request is kept as the proof of erasure; its reference is the
    # Operator's free text (check_request_ref refuses an address or the person's name), and the
    # rest are IDs, codes and times.
    "data_subject_requests": {
        "id": PiiClass.NONE,
        "contact_id": PiiClass.NONE,
        "kind": PiiClass.NONE,
        "status": PiiClass.NONE,
        "request_ref": PiiClass.REDACT,
        "opened_at": PiiClass.NONE,
        "completed_at": PiiClass.NONE,
        "receipt_id": PiiClass.NONE,
    },
    # Story 3.14. A receipt's request reference is the Operator's free text (as on its request);
    # the rest are IDs, codes and times. Each store's action is fixed text and its detail is
    # built by nextron.domain.erasure_receipt from counts, dates, ids and codes (no @).
    "erasure_receipts": {
        "id": PiiClass.NONE,
        "contact_id": PiiClass.NONE,
        "request_ref": PiiClass.REDACT,
        "cause": PiiClass.NONE,
        "outcome": PiiClass.NONE,
        "created_at": PiiClass.NONE,
    },
    "erasure_receipt_stores": {
        "id": PiiClass.NONE,
        "receipt_id": PiiClass.NONE,
        "store": PiiClass.NONE,
        "action": PiiClass.NONE,
        "status": PiiClass.NONE,
        "detail": PiiClass.NONE,
        "at": PiiClass.NONE,
    },
    # Story 2.9. A quote or a fact's value is a page's words and may quote a person, so both are
    # redacted; the rest are IDs, codes, numbers, public page URLs and times.
    "evidence": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "type": PiiClass.NONE,
        "quote": PiiClass.REDACT,
        "sought": PiiClass.NONE,
        "number": PiiClass.NONE,
        "fact": PiiClass.NONE,
        "value": PiiClass.REDACT,
        "pages_checked": PiiClass.NONE,
        "source": PiiClass.NONE,
        "source_url": PiiClass.NONE,
        "retrieved_at": PiiClass.NONE,
        "extracted_by": PiiClass.NONE,
        "person_contact_id": PiiClass.NONE,
        "superseded_by": PiiClass.NONE,
        "retracted_at": PiiClass.NONE,
        "last_confirmed_at": PiiClass.NONE,
        "created_at": PiiClass.NONE,
    },
    "signals": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "signal_type": PiiClass.NONE,
        "signal_class": PiiClass.NONE,
        "tier": PiiClass.NONE,
        "score_only": PiiClass.NONE,
        "event_date": PiiClass.NONE,
        "first_seen_at": PiiClass.NONE,
        "retracted_at": PiiClass.NONE,
    },
    "signal_evidence": {
        "id": PiiClass.NONE,
        "signal_id": PiiClass.NONE,
        "evidence_id": PiiClass.NONE,
    },
    "extraction_runs": {
        "id": PiiClass.NONE,
        "lead_id": PiiClass.NONE,
        "crawl_run_id": PiiClass.NONE,
        "route": PiiClass.NONE,
        "actor": PiiClass.NONE,
        "outcome": PiiClass.NONE,
        "error_code": PiiClass.NONE,
        "pages_sent": PiiClass.NONE,
        "pages_truncated": PiiClass.NONE,
        "stored": PiiClass.NONE,
        "discarded": PiiClass.NONE,
        "started_at": PiiClass.NONE,
        "ended_at": PiiClass.NONE,
    },
    # Payloads and keys hold IDs and scalars only (AD-10, AD-17). last_error is free text: an
    # exception's message is redacted and cut to 500 characters, but could still name a person.
    "jobs": {
        "id": PiiClass.NONE,
        "type": PiiClass.NONE,
        "lane": PiiClass.NONE,
        "status": PiiClass.NONE,
        "priority": PiiClass.NONE,
        "payload": PiiClass.NONE,
        "idempotency_key": PiiClass.NONE,
        "concurrency_key": PiiClass.NONE,
        "run_after": PiiClass.NONE,
        "attempts": PiiClass.NONE,
        "lease_until": PiiClass.NONE,
        "last_error": PiiClass.REDACT,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    # The engine's process id and two times (Story 1.11).
    "runner_heartbeat": {
        "id": PiiClass.NONE,
        "pid": PiiClass.NONE,
        "started_at": PiiClass.NONE,
        "beat_at": PiiClass.NONE,
    },
    # Story 1.16. The copy's path, size, hash, revision, counts and times. error is free text:
    # an exception's message, redacted and cut to 500 characters, could still name a person.
    "backups": {
        "id": PiiClass.NONE,
        "kind": PiiClass.NONE,
        "status": PiiClass.NONE,
        "started_at": PiiClass.NONE,
        "finished_at": PiiClass.NONE,
        "path": PiiClass.NONE,
        "size_bytes": PiiClass.NONE,
        "sha256": PiiClass.NONE,
        "db_revision": PiiClass.NONE,
        "suppression_count": PiiClass.NONE,
        "provenance_count": PiiClass.NONE,
        "error": PiiClass.REDACT,
        "local_deleted_at": PiiClass.NONE,
        # Story 1.17. The off-PC outcome, the copy's file name and times; offpc_error is free
        # text like error.
        "offpc_status": PiiClass.NONE,
        "offpc_name": PiiClass.NONE,
        "offpc_ledger": PiiClass.NONE,
        "offpc_at": PiiClass.NONE,
        "offpc_deleted_at": PiiClass.NONE,
        "offpc_error": PiiClass.REDACT,
    },
    # Story 1.18. The restore test's kind, result, times, the tested backup's id, each check's
    # outcome, the reason code, the copy's revision and counts. detail is built from codes,
    # revisions and counts, but it is free text, so redact like the backups' errors.
    "restore_tests": {
        "id": PiiClass.NONE,
        "kind": PiiClass.NONE,
        "result": PiiClass.NONE,
        "started_at": PiiClass.NONE,
        "finished_at": PiiClass.NONE,
        "backup_id": PiiClass.NONE,
        "check_fetch": PiiClass.NONE,
        "check_integrity": PiiClass.NONE,
        "check_revision": PiiClass.NONE,
        "check_suppression": PiiClass.NONE,
        "check_provenance": PiiClass.NONE,
        "reason": PiiClass.NONE,
        "copy_revision": PiiClass.NONE,
        "suppression_count": PiiClass.NONE,
        "provenance_count": PiiClass.NONE,
        "detail": PiiClass.REDACT,
    },
    # recovery_state (Story 1.19): codes, times and file names (never a path); no personal data.
    "recovery_state": {
        "id": PiiClass.NONE,
        "mode": PiiClass.NONE,
        "restored_at": PiiClass.NONE,
        "backup_at": PiiClass.NONE,
        "source_name": PiiClass.NONE,
        "pre_restore_name": PiiClass.NONE,
        "ended_at": PiiClass.NONE,
        "reapply_erasure_ledger": PiiClass.NONE,
        "mark_oms_unknown": PiiClass.NONE,
        "reingest_mailboxes": PiiClass.NONE,
        "repoll_unsubscribes": PiiClass.NONE,
    },
    # Story 1.12. Ops alert text carries IDs and counts only, but later kinds (Hot Lead,
    # LinkedIn) are free text that may name a person; every other column is an ID, a code or a
    # time.
    "alerts": {
        "id": PiiClass.NONE,
        "kind": PiiClass.NONE,
        "subject": PiiClass.NONE,
        "channel": PiiClass.NONE,
        "text": PiiClass.REDACT,
        "status": PiiClass.NONE,
        "provider_message_id": PiiClass.NONE,
        "error_code": PiiClass.NONE,
        "resent_at": PiiClass.NONE,
        "created_at": PiiClass.NONE,
        "updated_at": PiiClass.NONE,
    },
    "notifications": {
        "id": PiiClass.NONE,
        "kind": PiiClass.NONE,
        "subject": PiiClass.NONE,
        "text": PiiClass.REDACT,
        "created_at": PiiClass.NONE,
        "dismissed_at": PiiClass.NONE,
    },
    # Story 1.13. Codes, counts, flags and times only: nextron.domain.budget refuses any value
    # that could hold free text or an address.
    "api_usage": {
        "id": PiiClass.NONE,
        "provider": PiiClass.NONE,
        "operation": PiiClass.NONE,
        "sku": PiiClass.NONE,
        "units": PiiClass.NONE,
        "ok": PiiClass.NONE,
        "error_code": PiiClass.NONE,
        "at": PiiClass.NONE,
    },
    "ai_usage": {
        "id": PiiClass.NONE,
        "route": PiiClass.NONE,
        "route_tuple": PiiClass.NONE,
        "served_model": PiiClass.NONE,
        "input_tokens": PiiClass.NONE,
        "output_tokens": PiiClass.NONE,
        "cache_read_tokens": PiiClass.NONE,
        "cache_write_tokens": PiiClass.NONE,
        "iterations": PiiClass.NONE,
        "batch": PiiClass.NONE,
        "cost_micros": PiiClass.NONE,
        "refused": PiiClass.NONE,
        "error_code": PiiClass.NONE,
        "at": PiiClass.NONE,
    },
}

# Story 3.13: the suppression key kind each keep_hash column's value is hashed as, or None when
# it already holds a hash.
HASH_KINDS: Final[Mapping[tuple[str, str], str | None]] = MappingProxyType(
    {
        ("contacts", "email"): "address",
        ("provenance_records", "address"): "address",
        ("suppression_entries", "value_hash"): None,
        ("draft_revisions", "text_hash"): None,  # Story 4.12
    }
)
