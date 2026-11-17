"""The ``playbook`` service (owner map, AD-3). App-DAG layer: foundation.

Import rule: ``nextron.domain``, ``nextron.ports``, ``nextron.db.uow``, ``nextron.db.eventlog``,
``nextron.db.repos.playbook`` (never another owner's repositories), any ``nextron.app.queries.*``
module, and the ``api`` of these owners below it: ``notify``, ``jobqueue``.

It owns the versioned AI prompts and which tuple each AI route runs (Story 2.8): the
``prompt_snapshots`` and ``ai_route_activations`` tables. ``api.Playbook.sync_prompts`` stores
the prompt files and bootstrap-activates each route's default at ``serve`` start, and
``nextron.app.queries.playbook.active_tuple`` serves the pinned tuple to the extraction
services.
"""
