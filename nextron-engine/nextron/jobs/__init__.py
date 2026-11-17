"""Delivery: the job runner lanes, thin job handlers and periodic triggers (AD-10).

Import rule: ``nextron.app.*.api``, ``nextron.app.queries.*``, domain types and
``apscheduler``. Handlers open one unit of work and call one use case.

- ``runner`` (Story 1.10): ``Runner``, the lane worker threads and the keeper thread that renews
  leases, reaps expired ones and purges old ``done`` jobs. The FastAPI lifespan starts and stops
  it (``nextron.main.runner_lifespan``). It imports only ``nextron.app.jobqueue.api``,
  ``nextron.app.queries.setup``, the domain and the standard library, so it never names
  ``AdapterError``: ``JobQueue.fail`` classifies errors.

Enforced by contracts today: never ``nextron.adapters`` or ``nextron.db`` (contract 2), never
``nextron.main`` or ``nextron.web`` (contract 1), never the composition-root helpers, and
``apscheduler`` is imported nowhere else. ``tests/contracts/test_job_contracts.py`` also checks
that ``runner`` imports nothing from ``nextron.ports`` or ``nextron.db``.

Convention, not yet enforced: only the ``api`` and ``queries`` modules of ``nextron.app``, and
nothing from ``nextron.ports``.
"""
