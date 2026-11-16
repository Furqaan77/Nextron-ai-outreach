"""Typed domain errors (the *Errors* convention).

A rule violation in the domain raises a subclass of ``DomainError``. Every subclass declares its
own ``code``: unique across all subclasses, stable once released (callers, stored records and
the dashboard key on it, so it is never renamed or reused) and matching ``CODE_PATTERN``
(``^[a-z][a-z0-9_]*$``). Defining a subclass with a missing, malformed or duplicate code raises
``TypeError`` at once. An intermediate base that groups related errors passes ``abstract=True``
and declares no code; neither it nor ``DomainError`` itself can be raised.
``tests/contracts/test_error_contracts.py`` imports every ``nextron.domain`` module and checks
the codes again, which also catches a code changed after the class was defined.

The message is written by the code that raises it. The web layer shows it to the Operator, so
it names what was wrong by ID, never by personal data or a secret; logs redact it anyway.
"""

import re
from typing import Any, ClassVar, Final

CODE_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]*")  # matched with fullmatch

# code -> "module.qualname" of the class that declared it
_CODE_OWNERS: Final[dict[str, str]] = {}


def is_abstract(cls: type) -> bool:
    """Whether ``cls`` is ``DomainError`` or a subclass defined with ``abstract=True``."""
    return bool(cls.__dict__.get("_abstract", False))


class DomainError(Exception):
    """Base of every domain rule violation. Raise a subclass that declares ``code``."""

    code: ClassVar[str]
    _abstract: ClassVar[bool] = True

    def __init_subclass__(cls, *, abstract: bool = False, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._abstract = abstract
        if abstract:
            return
        name = f"{cls.__module__}.{cls.__qualname__}"
        code = cls.__dict__.get("code")
        if not isinstance(code, str) or not CODE_PATTERN.fullmatch(code):
            raise TypeError(
                f"{name} must declare its own code matching ^[a-z][a-z0-9_]*$ "
                f"(or pass abstract=True); got {code!r}"
            )
        owner = _CODE_OWNERS.get(code)
        if owner is not None and owner != name:  # the same name again is a module reload
            raise TypeError(f"{name} reuses code {code!r} of {owner}")
        _CODE_OWNERS[code] = name

    def __init__(self, message: str) -> None:
        if is_abstract(type(self)):
            raise TypeError(f"{type(self).__name__} is abstract; raise a subclass with a code")
        super().__init__(message)
        self.message = message
