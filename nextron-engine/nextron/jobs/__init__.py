"""Delivery: the job runner lanes, thin job handlers and periodic triggers (AD-10).

Import rule: ``nextron.app.*.api``, ``nextron.app.queries.*``, domain types and
``apscheduler``. Handlers open one unit of work and call one use case.

Enforced by contracts today: never ``nextron.adapters`` or ``nextron.db`` (contract 2), never
``nextron.main`` or ``nextron.web`` (contract 1), never the composition-root helpers, and
``apscheduler`` is imported nowhere else.

Convention, not yet enforced: only the ``api`` and ``queries`` modules of ``nextron.app``, and
nothing from ``nextron.ports``.
"""
