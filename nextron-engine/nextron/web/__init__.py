"""Delivery: the localhost dashboard (FastAPI, Jinja2 templates and vendored HTMX; AD-13).

Import rule: ``nextron.app.*.api``, ``nextron.app.queries.*`` and domain types. Handlers are
sync ``def``.

Enforced by contracts today: never ``nextron.adapters`` or ``nextron.db`` (contract 2), never
``nextron.main`` or ``nextron.jobs`` (contract 1), and never the composition-root helpers
``nextron.bootstrap``, ``nextron.lock`` or ``nextron.startup_checks``.

Convention, not yet enforced: only the ``api`` and ``queries`` modules of ``nextron.app``, and
nothing from ``nextron.ports``.

Modules: ``app`` (the factory and routes), ``guard`` (the ASGI middleware that checks ``Host``,
the session, and ``Origin`` and ``Sec-Fetch-Site`` on unsafe methods, and stamps the security
headers on every response), ``sessions`` (the in-memory session store, with each session's
CSRF token), ``csrf`` (the ``require_csrf`` dependency every POST runs first), ``forms`` (the
``form_fields`` dependency that parses a POST's urlencoded form, and the 400 for a malformed
one), ``routing`` (the ``@personal_data`` declaration every route makes, and the route class
that runs each GET in a read-only unit of work and gives each POST the CSRF check),
``settings_page`` (the Settings form, generated from the settings schema), ``credentials_page``
(the write-only Credentials rows, generated from the credential registry), ``quotas_page`` (the
Quotas and cost page, from the ``ops`` service's ``Budget.status()``), ``health_page`` (the
health panel, from the ``ops`` service's ``EngineHealth.report()``, and its "Back up now"
through ``Backups.request_backup()``), ``templating`` (the
``plain``, ``safe_link`` and ``csrf_field`` helpers for untrusted text), ``templates/`` (Jinja2,
autoescaping on, no inline script or style; ``macros.html`` holds ``untrusted_block``) and
``static/`` (the vendored HTMX build and ``app.css``). ``tests/contracts/test_web_contracts.py``
checks that no route handler is ``async def``, that every route declares ``personal_data`` and
keeps the route rules, that no template has inline script or style, that every form posts
through HTMX (``hx-post``) with ``csrf_field()``, that autoescaping is on, and that the ``safe``
filter and ``Markup`` appear only where ``tests/contracts/safe_allowlist.txt`` allows them.
"""
