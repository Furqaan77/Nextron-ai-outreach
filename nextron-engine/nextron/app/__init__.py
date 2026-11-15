"""Application layer: one service per owner (AD-3), arranged in the app DAG (AD-1).

Import rule for ``nextron.app.<owner>``: ``nextron.domain``, ``nextron.ports``,
``nextron.db.uow``, ``nextron.db.eventlog``, its own ``nextron.db.repos.<owner>``, any
``nextron.app.queries.*`` module, and the ``api`` of owners below it in the app DAG. Never
another owner's repositories.

App DAG, top to bottom (a service may call the ``api`` of any layer below it; services in the
same layer never call each other; effects that point up the DAG use notifications):

1. entry services: ``replies``, ``discovery``, ``scoring``, ``ops``, ``analytics`` (read only)
2. ``pipeline``
3. ``sequencing``
4. ``approval``
5. owner services: ``drafting``, ``sending``, ``research``
6. foundation: ``compliance``, ``playbook``, ``setup``
7. ``notify``
8. ``jobqueue``
9. ``queries``: every owner's read API

Enforced by contracts today: contract 6 (an owner imports nothing from a higher or sibling
owner in the DAG), contract group 7 (an owner's commands and queries import no other owner's
``nextron.db.repos``), and contract 1 (the app never imports web, jobs, adapters or main).

Convention, not yet enforced: a service reaches a lower owner only through that owner's
``api`` module, never its other modules.
"""
