"""Repositories of the ``compliance`` service: the only writers of its tables (AD-3).

Only ``nextron.app.compliance`` and ``nextron.app.queries.compliance`` import this package
(contract group 7).

- ``suppression_entries``: the ``suppression_entries`` table, the Suppression List (Story 3.3).
- ``unsub_tokens``: the ``unsub_tokens`` table, the record of every issued unsubscribe token
  (Story 3.8).
"""
