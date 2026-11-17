"""Repositories of the ``playbook`` service: the only writers of its tables (AD-3).

Only ``nextron.app.playbook`` and ``nextron.app.queries.playbook`` import this package (contract
group 7). ``snapshots`` (Story 2.8) is the repository of the ``prompt_snapshots`` table,
``activations`` (Story 2.8) of the ``ai_route_activations`` table, and ``company_facts``
(Story 4.3) of the ``company_facts`` table.
"""
