"""``FakeEmailSyntax``: the in-memory fake of ``nextron.ports.email_syntax.EmailSyntaxPort``."""

from collections.abc import Iterable
from typing import Final

from nextron.ports.email_syntax import checked_address

REFUSED_CHARACTERS: Final = frozenset(',;<>()[]\\" ')


class FakeEmailSyntax:
    """Refuses each address in ``refused`` and any address holding one of ``,;<>()[]\\"`` or a
    space; accepts the rest. Records every address it was asked about."""

    def __init__(self, refused: Iterable[str] = ()) -> None:
        self.refused = set(refused)
        self.checked: list[str] = []

    def is_valid(self, address: str) -> bool:
        text = checked_address(address)
        self.checked.append(text)
        if text in self.refused:
            return False
        return REFUSED_CHARACTERS.isdisjoint(text)
