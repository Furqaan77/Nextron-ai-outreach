"""The ``approval`` service (owner map, AD-3). App-DAG layer: approval.

Import rule: ``nextron.domain``, ``nextron.ports``, ``nextron.db.uow``, ``nextron.db.eventlog``,
``nextron.db.repos.approval`` (never another owner's repositories), any ``nextron.app.queries.*``
module, and the ``api`` of these owners below it: ``drafting``, ``sending``, ``research``,
``compliance``, ``playbook``, ``setup``, ``notify``, ``jobqueue``.
"""
