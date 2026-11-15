"""The ``compliance`` service (owner map, AD-3). App-DAG layer: foundation.

Import rule: ``nextron.domain``, ``nextron.ports``, ``nextron.db.uow``, ``nextron.db.eventlog``,
``nextron.db.repos.compliance`` (never another owner's repositories), any ``nextron.app.queries.*``
module, and the ``api`` of these owners below it: ``notify``, ``jobqueue``.
"""
