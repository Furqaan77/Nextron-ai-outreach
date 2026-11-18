"""Repositories of the ``compliance`` service: the only writers of its tables (AD-3).

Only ``nextron.app.compliance`` and ``nextron.app.queries.compliance`` import this package
(contract group 7).

- ``suppression_entries``: the ``suppression_entries`` table, the Suppression List (Story 3.3;
  Story 3.5 adds the panel's readers and the void).
"""
