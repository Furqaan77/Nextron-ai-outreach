"""The ``research`` service (owner map, AD-3). App-DAG layer: owner services.

Import rule: ``nextron.domain``, ``nextron.ports``, ``nextron.db.uow``, ``nextron.db.eventlog``,
``nextron.db.repos.research`` (never another owner's repositories), any ``nextron.app.queries.*``
module, and the ``api`` of these owners below it: ``compliance``, ``playbook``, ``setup``,
``notify``, ``jobqueue``.
"""
