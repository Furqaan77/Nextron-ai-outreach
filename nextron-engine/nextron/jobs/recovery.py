"""The recovery steps' runner (Story 1.19; AD-2, AD-5, AD-17, AD-20, OP-8).

It imports only the domain and the standard library: the ``ops`` service's ``Recovery`` is
reached through the ``RecoveryService`` protocol, and units of work through ``Units``.

After ``nextron restore`` the restored database holds an ``on`` ``recovery_state`` row with four
``pending`` steps. ``nextron.main`` builds one ``RecoveryRunner(units, recovery, steps)`` on the
frozen step registry (``nextron.main.build_recovery_steps``):

- ``startup()`` runs once at every engine start, before the job runner starts
  (``nextron.main.engine_lifespan``): ``recovery.pending(steps.registered())`` marks each step
  nobody registered ``not_applicable``. When a row is ``on`` it logs ``recovery_mode_on``
  (WARNING: ``recovery_id``, ``pending``: the registered steps still pending, comma-separated
  and empty when there are none), then runs each
  pending ``startup`` handler. A startup step that failed or wasn't done is tried again at the
  next start, never while the runner is up. Anything ``Recovery`` raises propagates, so the
  engine doesn't start (it fails closed).
- ``check() -> bool`` is the ``check_recovery`` job's work (``nextron.jobs.handlers``): it runs
  ``pending(...)``, then each pending handler that isn't a ``startup`` one, then returns
  ``recovery.finish()``, which ends recovery mode once every step is ``done`` or
  ``not_applicable`` and says whether it is still on.

**Running a handler.** ``handler.run(context)`` runs in a unit of work of its own, which the
handler uses for exactly one use case. ``True`` is followed by ``recovery.mark_done(...)`` in
another unit; ``False`` leaves the step ``pending``. An exception, or a result that isn't a
``bool``, logs ``recovery_step_failed`` (ERROR: ``recovery_id``, ``step``, ``code``, the
traceback) and leaves the step ``pending``. Errors from ``Recovery`` itself propagate.

Each unit of work it opens runs in a fresh ``contextvars.Context()``, as
``nextron.main.check_usage`` does, so it works inside the runner's job unit (a second
``units.open()`` in the same context would raise). The job unit holds no transaction while the
handler runs, so these units never wait on it.
"""

import contextvars
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Final, Protocol

from nextron.domain.recovery import (
    RecoveryContext,
    RecoveryHandler,
    RecoveryPass,
    RecoveryStep,
    RecoverySteps,
)

RECOVERY_MODE_ON: Final = "recovery_mode_on"
RECOVERY_STEP_FAILED: Final = "recovery_step_failed"

log = logging.getLogger(__name__)


class Units(Protocol):
    """What the runner needs of ``nextron.db.uow.UnitOfWorkFactory``."""

    def open(self, *, read_only: bool = False) -> AbstractContextManager[object]: ...


class RecoveryService(Protocol):
    """What the runner needs of the ``ops`` service's ``Recovery``."""

    def pending(self, registered: frozenset[RecoveryStep]) -> RecoveryPass | None: ...

    def mark_done(self, recovery_id: str, step: RecoveryStep) -> bool: ...

    def finish(self) -> bool: ...


def _code(error: BaseException) -> str:
    """The code a log record carries for ``error``: its own ``code`` text, or its class name."""
    code = getattr(error, "code", None)
    return code if isinstance(code, str) and code else type(error).__name__


class RecoveryRunner:
    """Runs the registered recovery steps' handlers; see the module docstring."""

    def __init__(self, units: Units, recovery: RecoveryService, steps: RecoverySteps) -> None:
        if not isinstance(steps, RecoverySteps):
            raise TypeError("steps must be a RecoverySteps registry")
        if not steps.frozen:
            raise RuntimeError("freeze the recovery step registry before running it")
        self._units = units
        self._recovery = recovery
        self._steps = steps

    def _in_unit[T](self, call: Callable[[], T]) -> T:
        """``call()`` in a unit of work of its own, inside a fresh ``contextvars.Context()``."""

        def run() -> T:
            with self._units.open():
                return call()

        return contextvars.Context().run(run)

    def _pending(self) -> RecoveryPass | None:
        registered = self._steps.registered()
        return self._in_unit(lambda: self._recovery.pending(registered))

    def startup(self) -> None:
        """At an engine start, before the runner: mark the unregistered steps and run each
        pending ``startup`` handler."""
        found = self._pending()
        if found is None:
            return
        log.warning(
            RECOVERY_MODE_ON,
            extra={
                "recovery_id": found.context.recovery_id,
                "pending": ",".join(str(step) for step in found.pending),
            },
        )
        for step in found.pending:
            handler = self._steps.get(step)
            if handler is not None and handler.startup:
                self._run(handler, found.context)

    def check(self) -> bool:
        """The ``check_recovery`` job's work: run each pending non-``startup`` handler, then
        end recovery mode when every step is settled; whether it is still on."""
        found = self._pending()
        if found is not None:
            for step in found.pending:
                handler = self._steps.get(step)
                if handler is not None and not handler.startup:
                    self._run(handler, found.context)
        return self._in_unit(self._recovery.finish)

    def _run(self, handler: RecoveryHandler, context: RecoveryContext) -> None:
        """Run one handler in its own unit; mark its step done on ``True``."""
        try:
            done: object = self._in_unit(lambda: handler.run(context))
            if not isinstance(done, bool):
                raise TypeError(f"the {handler.step} handler must return a bool")
        except Exception as exc:  # noqa: BLE001 - a failed step stays pending, recovery stays on
            log.error(
                RECOVERY_STEP_FAILED,
                exc_info=exc,
                extra={
                    "recovery_id": context.recovery_id,
                    "step": str(handler.step),
                    "code": _code(exc),
                },
            )
            return
        if done:
            self._in_unit(lambda: self._recovery.mark_done(context.recovery_id, handler.step))
