"""Repositories of the ``research`` service: the only writers of its tables (AD-3).

Only ``nextron.app.research`` and ``nextron.app.queries.research`` import this package (contract
group 7): ``contacts`` (the ``contacts`` table) and ``provenance`` (``provenance_records``).
``crawl`` (Story 2.5) is the repository of the ``crawl_runs``, ``crawled_pages`` and
``ats_links`` tables, and of ``page_contact_methods`` (Story 2.6). ``signals`` (Story 2.9) is
the repository of ``evidence``, ``signals``, ``signal_evidence`` and ``extraction_runs``, and
``profile`` (Stories 2.6 and 2.9) reads and writes the Lead's profile group, the ``research``
columns of ``leads`` (``hq_tz``, ``hq_evidence_id`` and ``contact_methods``). ``export``
(Story 3.15) reads, without writing, every column of the rows a subject access export needs.
``postings`` (Story 2.11) is the repository of ``job_postings``.
"""
