"""Repositories of the ``playbook`` service: the only writers of its tables (AD-3).

Only ``nextron.app.playbook`` and ``nextron.app.queries.playbook`` import this package (contract
group 7). ``snapshots`` (Story 2.8) is the repository of the ``prompt_snapshots`` table,
``activations`` (Story 2.8) of the ``ai_route_activations`` table, ``company_facts``
(Story 4.3) of the ``company_facts`` table, and ``playbook_snapshots``, ``route_tuples`` and
``playbook_checks`` (Story 4.4) of the ``playbook_snapshots``, ``ai_route_tuples`` and
``playbook_checks`` tables, which they only read and insert.
"""
