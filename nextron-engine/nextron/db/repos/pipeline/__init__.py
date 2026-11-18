"""Repositories of the ``pipeline`` service: the only writers of its tables (AD-3).

Only ``nextron.app.pipeline`` and ``nextron.app.queries.pipeline`` import this package (contract
group 7). ``stages`` (Story 2.2) is the repository of the ``leads`` table's pipeline group
(``pipeline_stage``, and Story 9.1's ``deal_value`` and ``notes``). ``bookings`` (Story 9.1) is
the repository of ``call_outcomes``. ``board`` (Story 9.7) holds the Pipeline board's reads,
read-only.
"""
