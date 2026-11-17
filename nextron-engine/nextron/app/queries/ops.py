"""Read API of the ``ops`` service (AD-3, AD-20).

Import rule: ``nextron.domain`` and ``nextron.db.repos.ops`` only.

- ``heartbeat()``: the ``HeartbeatRow`` of the engine's heartbeat (its ``runner_heartbeat``
  row: ``pid``, ``started_at`` and ``beat_at``), or ``None`` before the first beat.

It reads through the current unit of work, read-only ones included.
"""

from nextron.db.repos.ops import heartbeat as heartbeat_repo
from nextron.db.repos.ops.heartbeat import HeartbeatRow


def heartbeat() -> HeartbeatRow | None:
    """The engine's heartbeat row, or ``None``."""
    return heartbeat_repo.get()
