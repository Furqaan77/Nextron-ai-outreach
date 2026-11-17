"""The ``playbook`` service (owner map, AD-3). App-DAG layer: foundation.

Import rule: ``nextron.domain``, ``nextron.ports``, ``nextron.db.uow``, ``nextron.db.eventlog``,
``nextron.db.repos.playbook`` (never another owner's repositories), any ``nextron.app.queries.*``
module, and the ``api`` of these owners below it: ``notify``, ``jobqueue``.

It owns the versioned AI prompts and which tuple each AI route runs (Story 2.8): the
``prompt_snapshots`` and ``ai_route_activations`` tables. ``api.Playbook.sync_prompts`` stores
the prompt files and bootstrap-activates each route's default at ``serve`` start, and
``nextron.app.queries.playbook.active_tuple`` serves the pinned tuple to the extraction
services. Story 4.4 adds the Sales Playbook snapshots, the candidate route tuples and the
Playbook checks (``playbook_snapshots``, ``ai_route_tuples`` and ``playbook_checks``): the same
sync stores each new Playbook text, records each route's candidate tuple and the check, and
``active_tuple`` hands the gateway the pinned Playbook snapshot.
"""
