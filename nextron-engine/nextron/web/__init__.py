"""Delivery: the localhost dashboard (FastAPI, Jinja2 templates and vendored HTMX; AD-13).

Import rule: ``nextron.app.*.api``, ``nextron.app.queries.*`` and domain types. Handlers are
sync ``def``.

Enforced by contracts today: never ``nextron.adapters`` or ``nextron.db`` (contract 2), never
``nextron.main`` or ``nextron.jobs`` (contract 1), and never the composition-root helpers
``nextron.bootstrap``, ``nextron.lock`` or ``nextron.startup_checks``.

Convention, not yet enforced: only the ``api`` and ``queries`` modules of ``nextron.app``, and
nothing from ``nextron.ports``.

Modules: ``app`` (the factory and routes), ``guard`` (the ASGI middleware that checks ``Host``
and the session and stamps the security headers on every response), ``sessions`` (the in-memory
session store), ``routing`` (the ``@personal_data`` declaration every route makes),
``templates/`` (Jinja2, autoescaping on, no inline script or style) and ``static/`` (the vendored
HTMX build and ``app.css``). ``tests/contracts/test_web_contracts.py`` checks that no route
handler is ``async def``, that every route declares ``personal_data``, that no template has
inline script or style, and that autoescaping is on. CSRF checks arrive in Story 1.5.
"""
