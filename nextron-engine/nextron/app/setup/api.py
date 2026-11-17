"""Public command API of the ``setup`` service.

Other services and the delivery layers change ``setup`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Setup`` (AD-12) stores operator settings in the ``settings`` table, each as the canonical text
of a setting declared in ``nextron.domain.settings_schema``. Each method is a ``@use_case``
that reads the clock once and opens one ``uow.transaction()``.

- ``set(key, value)`` stores one typed value: an undeclared key raises ``UnknownSetting``, then
  ``encode`` raises ``InvalidSettingValue`` for a value it refuses; otherwise the canonical text
  is upserted with ``updated_at`` = now (the row keeps its ``id``).
- ``save(texts)`` stores what the Settings page submitted, all or nothing. An undeclared key
  raises ``UnknownSetting`` before anything is read. Each text is stripped, then:

  - blank, on a key with a default: the row is deleted, so the default applies;
  - blank, on a required key that isn't set: nothing changes (a setting can stay unset);
  - blank, on a required key that is set: a problem with rule ``required`` (it can't be
    cleared);
  - otherwise ``parse``; the canonical text is written only when it differs from the stored
    one, so an unchanged key keeps its ``updated_at``. A key without a row compares with its
    default text instead: the page shows the default, and sending it back unchanged leaves the
    key unset, so a default changed in a later version still reaches it.

  Any problem raises ``InvalidSettings`` (every refused key with its rule) before anything is
  written. It returns every stored text after the save, as ``{key: text}``.

Both log ``settings_changed`` with ``keys``, the changed key names comma-joined, when at least
one key changed; never a value.
"""

import logging
from collections.abc import Mapping
from typing import Final

from nextron.db.repos.setup import settings as settings_repo
from nextron.db.uow import current, use_case
from nextron.domain import settings_schema
from nextron.domain.settings_schema import (
    REQUIRED,
    REQUIRED_RULE,
    InvalidSettings,
    InvalidSettingValue,
)
from nextron.ports.clock import Clock

SETTINGS_CHANGED: Final = "settings_changed"
CANNOT_CLEAR: Final = "is required and can't be cleared once set"

log = logging.getLogger(__name__)


def _log_changed(keys: list[str]) -> None:
    if keys:
        log.info(SETTINGS_CHANGED, extra={"keys": ",".join(keys)})


class Setup:
    """Operator settings: one typed value at a time, or a whole Settings page at once."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @use_case
    def set(self, key: str, value: object) -> None:
        """Store ``value`` for the declared setting ``key``."""
        now = self._clock.now()
        found = settings_schema.setting(key)
        text = settings_schema.encode(found, value)
        name = str(found.key)
        with current().transaction():
            settings_repo.upsert(name, text, now)
        _log_changed([name])

    @use_case
    def save(self, texts: Mapping[str, str]) -> dict[str, str]:
        """Store the submitted ``texts`` (``{key: text}``, any subset of the declared keys),
        all or nothing; return every stored text afterwards."""
        now = self._clock.now()
        chosen = {key: settings_schema.setting(key) for key in texts}
        with current().transaction():
            stored = settings_repo.stored_texts()
            problems: dict[str, InvalidSettingValue] = {}
            writes: dict[str, str] = {}
            deletes: list[str] = []
            changed: list[str] = []  # in submission order
            for key, raw in texts.items():
                found = chosen[key]
                name = str(found.key)
                if not isinstance(raw, str):
                    problems[name] = InvalidSettingValue(name, found.type, "must be text")
                    continue
                text = raw.strip()
                before = stored.get(name)
                if not text:
                    if found.default is not REQUIRED:
                        if before is not None:
                            deletes.append(name)
                            changed.append(name)
                    elif before is not None:
                        problems[name] = InvalidSettingValue(name, REQUIRED_RULE, CANNOT_CLEAR)
                    continue
                try:
                    canonical = settings_schema.encode(found, settings_schema.parse(found, text))
                except InvalidSettingValue as refused:
                    problems[name] = refused
                    continue
                # an unset key compares with its default text, so an unchanged default stays unset
                unchanged = before if before is not None else settings_schema.default_text(found)
                if canonical != unchanged:
                    writes[name] = canonical
                    changed.append(name)
            if problems:
                raise InvalidSettings(problems)
            for name in deletes:
                settings_repo.delete(name)
            for name, canonical in writes.items():
                settings_repo.upsert(name, canonical, now)
            after = settings_repo.stored_texts()
        _log_changed(changed)
        return after
