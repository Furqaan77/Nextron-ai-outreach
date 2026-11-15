"""Delivery: the localhost dashboard (FastAPI, Jinja2 templates and vendored HTMX; AD-13).

Import rule: ``nextron.app.*.api``, ``nextron.app.queries.*`` and domain types. Handlers are
sync ``def``.

Enforced by contracts today: never ``nextron.adapters`` or ``nextron.db`` (contract 2), never
``nextron.main`` or ``nextron.jobs`` (contract 1), and never the composition-root helpers
``nextron.bootstrap``, ``nextron.lock`` or ``nextron.startup_checks``.

Convention, not yet enforced: only the ``api`` and ``queries`` modules of ``nextron.app``, and
nothing from ``nextron.ports``.
"""
