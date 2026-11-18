"""The active unsubscribe-token key id for ``Compliance.issue_unsub_token`` (Story 3.8; FR-70,
AD-15): the one setting the ``compliance`` service reads.

``active_unsub_kid()`` is the ``unsub_active_kid`` setting (``queries.setup.get``), read in the
caller's unit of work. Only ``issue_unsub_token`` in ``nextron.app.compliance.api`` calls it.

It lives outside ``api.py`` because that module also evaluates live Eligibility (Story 3.6,
FR-18, NFR-7), and no setting may switch Eligibility off:
``tests/contracts/test_eligibility_contracts.py`` (e) requires ``api.py`` to import no
``settings_schema`` and to read no ``queries.setup`` reader but ``markets``. The key id chooses
which keys encode a new token; it has nothing to do with Eligibility, so Eligibility never
calls this.
"""

from nextron.app.queries import setup as setup_queries
from nextron.domain.settings_schema import Key


def active_unsub_kid() -> int:
    """The ``unsub_active_kid`` setting: the key id new unsubscribe tokens are encoded under."""
    return setup_queries.get(Key.UNSUB_ACTIVE_KID)
