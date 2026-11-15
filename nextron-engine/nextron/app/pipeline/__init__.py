"""The ``pipeline`` service (owner map, AD-3). App-DAG layer: pipeline.

Import rule: ``nextron.domain``, ``nextron.ports``, ``nextron.db.uow``, ``nextron.db.eventlog``,
``nextron.db.repos.pipeline`` (never another owner's repositories), any ``nextron.app.queries.*``
module, and the ``api`` of these owners below it: ``sequencing``, ``approval``, ``drafting``,
``sending``, ``research``, ``compliance``, ``playbook``, ``setup``, ``notify``, ``jobqueue``.
"""
