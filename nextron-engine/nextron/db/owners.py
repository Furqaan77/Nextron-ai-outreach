"""The executable owner map (AD-3).

``OWNERS`` lists every owning service in ``nextron.app``. Each owner has a command package
``nextron.app.<owner>`` (public surface ``api.py``), a read API ``nextron.app.queries.<owner>``
and a repository package ``nextron.db.repos.<owner>``.

``scripts/gen_owner_contracts.py`` turns ``OWNERS`` into import-linter contract group 7: an
owner's commands and queries may import only its own repositories. After changing ``OWNERS``,
run ``uv run python scripts/gen_owner_contracts.py``; a contract test fails until you do.

``TABLES`` maps each table to its owner. It is empty until a story adds a table; Story 1.3 adds
the column-level map and the test that fails on any unmapped column.
"""

from typing import Final

OWNERS: Final[tuple[str, ...]] = (
    "setup",
    "discovery",
    "research",
    "scoring",
    "sequencing",
    "drafting",
    "approval",
    "sending",
    "replies",
    "pipeline",
    "compliance",
    "playbook",
    "notify",
    "jobqueue",
    "ops",
    "analytics",
)

TABLES: Final[dict[str, str]] = {}
