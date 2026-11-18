"""Repositories of the ``compliance`` service: the only writers of its tables (AD-3).

Only ``nextron.app.compliance`` and ``nextron.app.queries.compliance`` import this package
(contract group 7).

- ``suppression_entries``: the ``suppression_entries`` table, the Suppression List (Story 3.3;
  Story 3.5 adds the panel's readers and the void).
- ``unsub_tokens``: the ``unsub_tokens`` table, the record of every issued unsubscribe token
  (Story 3.8).
- ``subject_requests``: the ``data_subject_requests`` table, the data subject requests (Story
  3.15; Story 3.13's schema). Story 3.15 also adds ``suppression_entries.for_export``.
"""
