"""Who may receive mail and alerts outside ``prod`` (AD-14, NFR-8, NFR-17).

Outside ``prod``, ``nextron.main`` wraps every ``mail`` and ``alert`` adapter in a sink
(``nextron.adapters.sink``). The sink delivers a message only to a recipient on the
``sink_allowlist`` setting and writes every other one to the sink folder. These are its rules:

- ``sink_applies(env)``: whether the sink wraps an environment's adapters; ``True`` for every
  value except exactly ``"prod"`` (``PROD``), so a mistyped or missing environment is sunk.
- ``is_sink_recipient(text)``: whether ``text`` is a ``str`` that fully matches an ASCII email
  address (a local part of atext characters and dots, ``@``, then two or more labels of
  ``A-Z``, ``a-z``, ``0-9`` and ``-``, separated by dots; at most 254 characters) or a numeric
  Telegram chat id (``-?[1-9][0-9]{0,19}``). Telegram user names (``@name``) are refused: a
  user name can be handed to someone else, a numeric id can't.
- ``allowlisted(recipient, allowlist)``: whether ``recipient`` is a valid sink recipient that
  equals a valid ``allowlist`` entry after ASCII-only lowercasing (``A-Z`` to ``a-z``, never
  Unicode casefolding, which would make ``ß`` equal ``ss``). Invalid and non-``str`` entries are
  ignored, nothing is stripped, and a bare ``str`` or ``bytes`` given as the allowlist counts as
  an empty one.

Import rule: the standard library only.
"""

import re
from collections.abc import Iterable
from typing import Final, TypeGuard

PROD: Final = "prod"
MAX_EMAIL_LENGTH: Final = 254

# RFC 5322 atext, as the body of a character class (the hyphen escaped)
_ATEXT: Final = r"A-Za-z0-9!#$%&'*+/=?^_`{|}~\-"
_EMAIL: Final = re.compile(rf"[{_ATEXT}.]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_CHAT_ID: Final = re.compile(r"-?[1-9][0-9]{0,19}")
_ASCII_LOWER: Final = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
)


def sink_applies(env: object) -> bool:
    """Whether the sink wraps ``env``'s mail and alert adapters: everywhere except ``prod``."""
    return not (isinstance(env, str) and env == PROD)


def is_sink_recipient(text: object) -> TypeGuard[str]:
    """Whether ``text`` is an ASCII email address or a numeric Telegram chat id."""
    if not isinstance(text, str):
        return False
    if _CHAT_ID.fullmatch(text):
        return True
    return len(text) <= MAX_EMAIL_LENGTH and _EMAIL.fullmatch(text) is not None


def _ascii_lower(text: str) -> str:
    return text.translate(_ASCII_LOWER)


def allowlisted(recipient: object, allowlist: Iterable[object]) -> bool:
    """Whether ``recipient`` is a valid sink recipient on ``allowlist``; see the module
    docstring."""
    if not is_sink_recipient(recipient) or isinstance(allowlist, (str, bytes)):
        return False
    wanted = _ascii_lower(recipient)
    return any(
        is_sink_recipient(entry) and _ascii_lower(entry) == wanted for entry in allowlist
    )
