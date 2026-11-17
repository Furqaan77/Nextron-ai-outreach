"""Read API of the ``setup`` service (AD-3, AD-12).

Import rule: ``nextron.domain`` and ``nextron.db.repos.setup`` only.

- ``get(key)``: the typed value of a declared setting, the only way consumers read one
  (``get(Key.SENDER_TIMEZONE)``). With a row it is the stored text decoded by ``parse`` (which
  raises ``InvalidSettingValue`` when the text no longer parses or passes the rules); without
  one it is the default, and a required setting nobody has set raises ``SettingNotSet``, so no
  consumer runs on a missing value by accident. An undeclared key raises ``UnknownSetting``.
- ``stored_texts()``: every stored ``{key: canonical text}`` (the Settings page shows them).

Both read through the current unit of work, read-only ones included.
"""

from typing import Any

from nextron.db.repos.setup import settings as settings_repo
from nextron.domain.settings_schema import REQUIRED, SettingNotSet, parse, setting


def get(key: str) -> Any:
    """The typed value of the declared setting ``key``; see the module docstring."""
    found = setting(key)
    text = settings_repo.stored_text(str(found.key))
    if text is None:
        if found.default is REQUIRED:
            raise SettingNotSet(found.key)
        return found.default
    return parse(found, text)


def stored_texts() -> dict[str, str]:
    """Every stored setting's canonical text, by key."""
    return settings_repo.stored_texts()
