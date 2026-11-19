"""Read API of the ``approval`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.approval`` only.

- ``ApprovalView(id, draft_id, rev, approved_at, approved_hash)``: one approval of a Draft's
  revision, with the ``text_hash`` the Operator approved.
- ``current_approval(draft_id) -> ApprovalView | None``: the Draft's current approval, or
  ``None``. **A fail-closed seam (Story 6.9):** it answers ``None`` for every Draft until Story
  5.4 stores approvals, so the send gate's ``approval_live`` and ``hash_matches`` fail and no
  message can be sent.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ApprovalView:
    """One approval of a Draft's revision."""

    id: str
    draft_id: str
    rev: int
    approved_at: datetime
    approved_hash: str


def current_approval(draft_id: str) -> ApprovalView | None:
    """The Draft's current approval, or ``None``; ``None`` always until Story 5.4."""
    return None
