"""Read API of the ``replies`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.replies`` only.

- ``thread_quiet(thread_id, since) -> bool | None``: whether the Thread has had no inbound or
  other activity after ``since`` (the approval time): ``True`` quiet, ``False`` active, ``None``
  unknown. **A fail-closed seam (Story 6.9):** it answers ``None`` until Story 6.8 syncs
  ``mail_messages``, so the send gate's ``thread_quiet`` fails for every cold message.
"""

from datetime import datetime


def thread_quiet(thread_id: str | None, since: datetime) -> bool | None:
    """Whether the Thread is quiet since ``since``; ``None`` (unknown) always until Story
    6.8."""
    return None
