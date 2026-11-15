"""The ``replies`` service (owner map, AD-3). App-DAG layer: entry services.

Import rule: ``nextron.domain``, ``nextron.ports``, ``nextron.db.uow``, ``nextron.db.eventlog``,
``nextron.db.repos.replies`` (never another owner's repositories), any ``nextron.app.queries.*``
module, and the ``api`` of these owners below it: ``pipeline``, ``sequencing``, ``approval``,
``drafting``, ``sending``, ``research``, ``compliance``, ``playbook``, ``setup``, ``notify``,
``jobqueue``.
"""
