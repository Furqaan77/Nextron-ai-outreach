"""The ``jobqueue`` service (owner map, AD-3). App-DAG layer: jobqueue.

Import rule: ``nextron.domain``, ``nextron.ports``, ``nextron.db.uow``, ``nextron.db.eventlog``,
``nextron.db.repos.jobqueue`` (never another owner's repositories), any ``nextron.app.queries.*``
module, and the ``api`` of these owners below it: none.
"""
