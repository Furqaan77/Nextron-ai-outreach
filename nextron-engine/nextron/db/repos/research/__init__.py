"""Repositories of the ``research`` service: the only writers of its tables (AD-3).

Only ``nextron.app.research`` and ``nextron.app.queries.research`` import this package (contract
group 7): ``contacts`` (the ``contacts`` table) and ``provenance`` (``provenance_records``).
``crawl`` (Story 2.5) is the repository of the ``crawl_runs``, ``crawled_pages`` and
``ats_links`` tables.
"""
