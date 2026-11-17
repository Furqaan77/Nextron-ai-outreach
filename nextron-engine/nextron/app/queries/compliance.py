"""Read API of the ``compliance`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.compliance`` only.

- ``newest_unsub_token(kid) -> datetime | None``: when the newest unsubscribe token under
  ``kid`` was issued, or ``None`` when ``kid`` has none (Story 3.8).
- ``newest_unsub_tokens() -> Mapping[int, datetime]``: the same for every kid with a token, by
  kid.

Both read in the caller's unit of work, read-only or not.
"""

from collections.abc import Mapping
from datetime import datetime

from nextron.db.repos.compliance import unsub_tokens as unsub_tokens_repo


def newest_unsub_token(kid: int) -> datetime | None:
    """When the newest unsubscribe token under ``kid`` was issued, or ``None``."""
    return unsub_tokens_repo.newest_issued_at(kid)


def newest_unsub_tokens() -> Mapping[int, datetime]:
    """``{kid: when its newest unsubscribe token was issued}`` for every kid with a token."""
    return unsub_tokens_repo.newest_by_kid()
