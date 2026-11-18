"""Public command API of the ``drafting`` service.

Other services and the delivery layers change ``drafting`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

- ``ERASURE`` and ``@command erase_pii(n: ContactErased)`` (Story 3.13, AD-17): drafting's
  ``ContactErased`` subscriber writes nothing, because ``footer_snapshots`` (its one table with
  a personal-data column) holds the Sender's name and Nextron's postal address, never a
  Contact's data.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from nextron.db.uow import command
from nextron.domain.erasure import Coverage, TableErasure
from nextron.domain.notifications import ContactErased


# Erasure (Story 3.13) -------------------------------------------------------------------------

# What drafting's erase_pii does to each of its tables with a personal-data column: none of them
# ever holds a Contact's data.
ERASURE: Final[Mapping[str, TableErasure]] = MappingProxyType(
    {
        "footer_snapshots": TableErasure(
            Coverage.NOT_HELD,
            "a Market footer's text: the Sender's name and Nextron's postal address, never a "
            "Contact's data",
        ),
    }
)


@command
def erase_pii(n: ContactErased) -> None:
    """``drafting``'s ``ContactErased`` subscriber (Story 3.13, AD-17): it writes nothing,
    because no ``drafting`` table holds a Contact's data (``ERASURE``)."""
