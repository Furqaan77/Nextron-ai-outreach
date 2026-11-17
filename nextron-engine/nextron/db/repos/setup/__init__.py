"""Repositories of the ``setup`` service: the only writers of its tables (AD-3).

Only ``nextron.app.setup`` and ``nextron.app.queries.setup`` import this package (contract
group 7). ``settings`` (Story 1.6) is the repository of the ``settings`` table, ``segments``
(Story 2.1) of the ``segments`` table, ``markets`` (Story 3.1) of the ``markets`` table, and
``sequences`` (Story 4.1) of the ``sequences`` and ``step_briefs`` tables.
"""
