"""The ``research`` service's crawl tables (FR-10, AD-7, AD-17, AD-18; migration
``0020_2_5_crawl``), written only by ``nextron.db.repos.research.crawl``. No foreign key
cascades.

``crawl_runs``: one row per crawl of a Lead's website. ``lead_id`` (``fk_crawl_runs_lead_id_leads``,
indexed by ``ix_crawl_runs_lead_id``); ``site_domain``, the registrable domain crawled, and
``final_domain``, the registrable domain the homepage landed on when it isn't ``site_domain``
(else NULL); ``started_at`` and ``ended_at``; ``outcome`` (``ck_crawl_runs_outcome``: one of
``nextron.domain.crawl.CrawlOutcome``) with a nullable ``error_code``; and the counts
``pages_fetched``, ``pages_skipped_robots``, ``renders`` and ``errors``
(``ck_crawl_runs_counts``: none negative).

``crawled_pages``: one row per Lead and page URL (``uq_crawled_pages_lead_id_url``), updated in
place by each crawl that fetches it. ``lead_id`` and ``run_id`` (the run that last fetched it);
``url`` and ``final_url``; ``kind`` (``ck_crawled_pages_kind``: one of
``nextron.domain.crawl.PageKind``); ``fetched_at``, ``http_status`` and ``content_hash`` (the
SHA-256 of the text, kept after the text is purged); ``text`` (the visible text, nullable:
``purge_page_text`` nulls it after ``text_purge_after``); ``changed_at``, when the hash
last changed; and ``tag_managers`` (Story 2.9, migration ``0029_2_9_tag_managers``), a
canonical JSON array of the tag manager keys the page loads (``[]`` when none), NULL for a page
never checked for them; and ``mailto`` (Story 2.13, migration ``0030_2_13_email_domains``), a
JSON list of the addresses of the page's visible ``mailto:`` links (NULL when it has none),
nulled with ``text``.

``ats_links``: one row per Lead and ATS URL (``uq_ats_links_lead_id_url``): ``lead_id``,
``run_id`` and ``page_id`` (the run and page that last saw it), ``url``, ``host``, ``tag``
(``ck_ats_links_tag``: ``a``, ``iframe`` or ``script``), ``first_seen_at`` and
``last_seen_at``.

``job_postings`` (Story 2.11, migration ``0035_2_11_job_postings``): one row per Lead, posting
URL and title (``uq_job_postings_lead_id_url_title``): ``lead_id``
(``fk_job_postings_lead_id_leads``, indexed by ``ix_job_postings_lead_id``); ``source``
(``ck_job_postings_source``: one of ``nextron.domain.postings.PostingSource``) and
``board_token`` (100), NULL exactly for a ``careers_page`` posting
(``ck_job_postings_board_token``); ``url`` (2,048), ``title`` (300) and the nullable
``description`` (an excerpt of at most 300 characters); the nullable ``posted_date`` (a date);
``first_seen_at`` and ``last_checked_at``; ``status`` (``ck_job_postings_status``: ``live`` or
``closed``) and ``closed_at``, set exactly when the posting is closed
(``ck_job_postings_closed_at``); and the nullable ``signal_id``
(``fk_job_postings_signal_id_signals``, indexed by ``ix_job_postings_signal_id``) and
``evidence_id`` (``fk_job_postings_evidence_id_evidence``): the posting's Event and its Quote
Evidence. A title or description may name a person, so both are PII class ``redact``.

``crawl_runs`` also holds, since migration ``0035_2_11_job_postings``, nullable counts with no
CHECK (NULL: not checked in this run): ``careers_postings`` and ``careers_events`` (the careers
postings listed and the hiring Events created by the run's extraction), ``greenhouse_postings``,
``greenhouse_events``, ``lever_postings``, ``lever_events``, ``ashby_postings`` and
``ashby_events`` (per board source, by ``check_job_boards``) and ``board_errors`` (the boards it
couldn't read).

``page_contact_methods`` (Story 2.6, migration ``0027_2_6_contact_methods``): one row per page
and contact method detected on it, by signature and source
(``uq_page_contact_methods_page_id_method_signature_source``): ``page_id``
(``fk_page_contact_methods_page_id_crawled_pages``, indexed by
``ix_page_contact_methods_page_id``), ``method`` (``ck_page_contact_methods_method``: one of
``nextron.domain.widgets.ContactMethod``), ``signature``, ``source``
(``ck_page_contact_methods_source``: one of ``nextron.domain.widgets.MatchSource``), ``matched``
and ``detected_at``. It has no ``lead_id``: on a merge its rows follow their page.

A page's text may quote a person, so ``crawled_pages.text`` is PII class ``null`` (Story 3.13:
an erasure nulls it and keeps ``content_hash``, as ``purge_page_text`` does);
``page_contact_methods.matched``, which may hold a phone number, is ``redact``. Every other
column (IDs, URLs of public pages, codes, counts, hashes and times) is ``none``.
"""

from datetime import date, datetime
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk
from nextron.domain.crawl import LINK_TAGS, CrawlOutcome, PageKind
from nextron.domain.postings import MAX_EXCERPT, MAX_TITLE, PostingSource, PostingStatus
from nextron.domain.widgets import MAX_MATCHED, ContactMethod, MatchSource

DOMAIN_LENGTH: Final = 253  # nextron.domain.identity.MAX_DOMAIN_LENGTH
URL_LENGTH: Final = 2048  # nextron.domain.fetch_policy.MAX_URL_LENGTH
OUTCOME_LENGTH: Final = 32
ERROR_CODE_LENGTH: Final = 64  # nextron.domain.errors.CODE_PATTERN
KIND_LENGTH: Final = 16
HASH_LENGTH: Final = 64
TAG_LENGTH: Final = 8

OUTCOME_CHECK: Final = "outcome IN ({})".format(
    ",".join(f"'{outcome}'" for outcome in CrawlOutcome)
)
COUNTS_CHECK: Final = (
    "pages_fetched >= 0 AND pages_skipped_robots >= 0 AND renders >= 0 AND errors >= 0"
)
KIND_CHECK: Final = "kind IN ({})".format(",".join(f"'{kind}'" for kind in PageKind))
TAG_CHECK: Final = "tag IN ({})".format(",".join(f"'{tag}'" for tag in LINK_TAGS))
METHOD_LENGTH: Final = 16
SIGNATURE_LENGTH: Final = 32
SOURCE_LENGTH: Final = 8
METHOD_CHECK: Final = "method IN ({})".format(
    ",".join(f"'{method}'" for method in ContactMethod)
)
SOURCE_CHECK: Final = "source IN ({})".format(",".join(f"'{source}'" for source in MatchSource))
POSTING_SOURCE_LENGTH: Final = 16
BOARD_TOKEN_LENGTH: Final = 100
POSTING_STATUS_LENGTH: Final = 8
POSTING_SOURCE_CHECK: Final = "source IN ({})".format(
    ",".join(f"'{source}'" for source in PostingSource)
)
BOARD_TOKEN_CHECK: Final = (
    f"(source = '{PostingSource.CAREERS_PAGE}' AND board_token IS NULL)"
    f" OR (source <> '{PostingSource.CAREERS_PAGE}' AND board_token IS NOT NULL)"
)
POSTING_STATUS_CHECK: Final = "status IN ({})".format(
    ",".join(f"'{status}'" for status in PostingStatus)
)
CLOSED_AT_CHECK: Final = (
    f"(status = '{PostingStatus.CLOSED}' AND closed_at IS NOT NULL)"
    f" OR (status = '{PostingStatus.LIVE}' AND closed_at IS NULL)"
)


class CrawlRunRecord(Base):
    """One crawl of a Lead's website."""

    __tablename__ = "crawl_runs"
    __table_args__ = (
        Index("ix_crawl_runs_lead_id", "lead_id"),
        CheckConstraint(OUTCOME_CHECK, name="outcome"),
        CheckConstraint(COUNTS_CHECK, name="counts"),
    )

    id: Mapped[str] = ulid_pk()
    lead_id: Mapped[str] = mapped_column(Ulid, ForeignKey("leads.id"))
    site_domain: Mapped[str] = mapped_column(String(DOMAIN_LENGTH))
    final_domain: Mapped[str | None] = mapped_column(String(DOMAIN_LENGTH))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ended_at: Mapped[datetime] = mapped_column(UtcDateTime)
    outcome: Mapped[str] = mapped_column(String(OUTCOME_LENGTH))
    error_code: Mapped[str | None] = mapped_column(String(ERROR_CODE_LENGTH))
    pages_fetched: Mapped[int] = mapped_column(Integer)
    pages_skipped_robots: Mapped[int] = mapped_column(Integer)
    renders: Mapped[int] = mapped_column(Integer)
    errors: Mapped[int] = mapped_column(Integer)
    # Story 2.11: the job postings and hiring Events the run found, per source (NULL: unchecked)
    careers_postings: Mapped[int | None] = mapped_column(Integer)
    careers_events: Mapped[int | None] = mapped_column(Integer)
    greenhouse_postings: Mapped[int | None] = mapped_column(Integer)
    greenhouse_events: Mapped[int | None] = mapped_column(Integer)
    lever_postings: Mapped[int | None] = mapped_column(Integer)
    lever_events: Mapped[int | None] = mapped_column(Integer)
    ashby_postings: Mapped[int | None] = mapped_column(Integer)
    ashby_events: Mapped[int | None] = mapped_column(Integer)
    board_errors: Mapped[int | None] = mapped_column(Integer)


class CrawledPageRecord(Base):
    """One page of a Lead's website, as its last crawl found it."""

    __tablename__ = "crawled_pages"
    __table_args__ = (
        UniqueConstraint("lead_id", "url"),
        CheckConstraint(KIND_CHECK, name="kind"),
    )

    id: Mapped[str] = ulid_pk()
    lead_id: Mapped[str] = mapped_column(Ulid, ForeignKey("leads.id"))
    run_id: Mapped[str] = mapped_column(Ulid, ForeignKey("crawl_runs.id"))
    url: Mapped[str] = mapped_column(String(URL_LENGTH))
    final_url: Mapped[str] = mapped_column(String(URL_LENGTH))
    kind: Mapped[str] = mapped_column(String(KIND_LENGTH))
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime)
    http_status: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(HASH_LENGTH))
    text: Mapped[str | None] = mapped_column(Text)
    text_purge_after: Mapped[datetime] = mapped_column(UtcDateTime)
    changed_at: Mapped[datetime] = mapped_column(UtcDateTime)
    tag_managers: Mapped[str | None] = mapped_column(Text)
    mailto: Mapped[str | None] = mapped_column(Text)  # Story 2.13


class AtsLinkRecord(Base):
    """One ATS board link found on a Lead's website."""

    __tablename__ = "ats_links"
    __table_args__ = (
        UniqueConstraint("lead_id", "url"),
        CheckConstraint(TAG_CHECK, name="tag"),
    )

    id: Mapped[str] = ulid_pk()
    lead_id: Mapped[str] = mapped_column(Ulid, ForeignKey("leads.id"))
    run_id: Mapped[str] = mapped_column(Ulid, ForeignKey("crawl_runs.id"))
    page_id: Mapped[str] = mapped_column(Ulid, ForeignKey("crawled_pages.id"))
    url: Mapped[str] = mapped_column(String(URL_LENGTH))
    host: Mapped[str] = mapped_column(String(DOMAIN_LENGTH))
    tag: Mapped[str] = mapped_column(String(TAG_LENGTH))
    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime)


class JobPostingRecord(Base):
    """One job posting of a Lead, from a careers page or an ATS board (Story 2.11)."""

    __tablename__ = "job_postings"
    __table_args__ = (
        Index("ix_job_postings_lead_id", "lead_id"),
        Index("ix_job_postings_signal_id", "signal_id"),
        UniqueConstraint("lead_id", "url", "title"),
        CheckConstraint(POSTING_SOURCE_CHECK, name="source"),
        CheckConstraint(BOARD_TOKEN_CHECK, name="board_token"),
        CheckConstraint(POSTING_STATUS_CHECK, name="status"),
        CheckConstraint(CLOSED_AT_CHECK, name="closed_at"),
    )

    id: Mapped[str] = ulid_pk()
    lead_id: Mapped[str] = mapped_column(Ulid, ForeignKey("leads.id"))
    source: Mapped[str] = mapped_column(String(POSTING_SOURCE_LENGTH))
    board_token: Mapped[str | None] = mapped_column(String(BOARD_TOKEN_LENGTH))
    url: Mapped[str] = mapped_column(String(URL_LENGTH))
    title: Mapped[str] = mapped_column(String(MAX_TITLE))
    description: Mapped[str | None] = mapped_column(String(MAX_EXCERPT))
    posted_date: Mapped[date | None] = mapped_column(Date)
    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_checked_at: Mapped[datetime] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String(POSTING_STATUS_LENGTH))
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    signal_id: Mapped[str | None] = mapped_column(Ulid, ForeignKey("signals.id"))
    evidence_id: Mapped[str | None] = mapped_column(Ulid, ForeignKey("evidence.id"))


class PageContactMethodRecord(Base):
    """One contact method detected on a crawled page, by one signature from one source."""

    __tablename__ = "page_contact_methods"
    __table_args__ = (
        Index("ix_page_contact_methods_page_id", "page_id"),
        UniqueConstraint("page_id", "method", "signature", "source"),
        CheckConstraint(METHOD_CHECK, name="method"),
        CheckConstraint(SOURCE_CHECK, name="source"),
    )

    id: Mapped[str] = ulid_pk()
    page_id: Mapped[str] = mapped_column(Ulid, ForeignKey("crawled_pages.id"))
    method: Mapped[str] = mapped_column(String(METHOD_LENGTH))
    signature: Mapped[str] = mapped_column(String(SIGNATURE_LENGTH))
    source: Mapped[str] = mapped_column(String(SOURCE_LENGTH))
    matched: Mapped[str] = mapped_column(String(MAX_MATCHED))
    detected_at: Mapped[datetime] = mapped_column(UtcDateTime)
