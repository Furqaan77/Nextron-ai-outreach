"""Repositories of the ``analytics`` service: the only writers of its tables (AD-3).

Only ``nextron.app.analytics`` and ``nextron.app.queries.analytics`` import this package (contract
group 7). Analytics owns no table yet. ``lineage`` (Story 9.8) reads the ``events`` rows about a
Lead and the rows linked to it by foreign keys, read-only, as ``nextron.db.repos.pipeline.board``
reads other owners' tables.
"""
