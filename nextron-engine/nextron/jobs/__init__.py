"""Delivery: the job runner lanes, thin job handlers and periodic triggers (AD-10).

Import rule: ``nextron.app.*.api``, ``nextron.app.queries.*``, domain types and
``apscheduler``. Handlers open one unit of work and call one use case.

- ``runner`` (Story 1.10): ``Runner``, the lane worker threads and the keeper thread that renews
  leases, reaps expired ones and purges old ``done`` jobs. It imports only
  ``nextron.app.jobqueue.api``, ``nextron.app.queries.setup``, the domain and the standard
  library, so it never names ``AdapterError``: ``JobQueue.fail`` classifies errors. Since Story
  1.12 it also runs the dead-job sweep (``dead_jobs``, the ``ops`` service's ``DeadJobAlerts``
  behind a local ``Protocol``) after a run whose job died and on every keeper tick. Since Story
  1.13 a handler whose use case the budget refused returns the domain's ``DEFERRED``, and the
  runner finishes the job with ``JobQueue.defer``: back to ``queued`` an hour later with its
  attempt given back, never ``dead``.
- ``handlers`` (Story 1.12): the thin job handlers, each built by ``nextron.main`` around what
  it calls (``send_alert(alerts)``). It imports only the domain and the standard library.
- ``triggers`` (Story 1.11): the closed AD-10 ``Trigger`` enum, the ``Every`` interval schedule
  and the ``Triggers`` registry, which ``nextron.main.build_triggers`` alone builds. It imports
  only the domain and the standard library.
- ``scheduler`` (Story 1.11): ``Scheduler``, APScheduler's ``AsyncIOScheduler(timezone=UTC)``
  with one interval job per registered trigger; each firing only enqueues its tick job
  (``JobQueue.enqueue_tick``). It is the only module that imports ``apscheduler``, with
  ``nextron.app.jobqueue.api``, ``triggers``, the domain and the standard library.
- ``heartbeat`` (Story 1.11): ``HeartbeatWriter``, the ``nextron-job-heartbeat`` thread that
  calls ``ops`` ``Heartbeat.beat`` every 30 s while the runner, its jobs and the scheduler are
  healthy. It imports only ``nextron.app.ops.api`` and the standard library.

The FastAPI lifespan (``nextron.main.engine_lifespan``) starts the runner, then the scheduler,
then the heartbeat writer, and stops them in reverse.

Enforced by contracts today: never ``nextron.adapters`` or ``nextron.db`` (contract 2), never
``nextron.main`` or ``nextron.web`` (contract 1), never the composition-root helpers, and
``apscheduler`` is imported nowhere else. ``tests/contracts/test_job_contracts.py`` also checks
each module's own imports: none of them imports ``nextron.ports`` or ``nextron.db``, and only
``scheduler`` imports ``apscheduler``.

Convention, not yet enforced: only the ``api`` and ``queries`` modules of ``nextron.app``, and
nothing from ``nextron.ports``.
"""
