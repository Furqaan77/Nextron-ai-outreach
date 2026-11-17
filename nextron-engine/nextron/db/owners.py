"""The executable owner map (AD-3).

``OWNERS`` lists every owning service in ``nextron.app``. Each owner has a command package
``nextron.app.<owner>`` (public surface ``api.py``), a read API ``nextron.app.queries.<owner>``
and a repository package ``nextron.db.repos.<owner>``.

``scripts/gen_owner_contracts.py`` turns ``OWNERS`` into import-linter contract group 7: an
owner's commands and queries may import only its own repositories. After changing ``OWNERS``,
run ``uv run python scripts/gen_owner_contracts.py``; a contract test fails until you do.

``TABLES`` maps each table to its writers, column by column: ``table -> {writer: (columns...)}``.
A writer is an owner in ``OWNERS`` or a shared database module in ``SHARED_WRITERS``, which
writes on behalf of every owner: ``db.eventlog`` (the ``events`` log) and ``db.metering`` (the
usage tables, through ``UsageMeterPort``). Every
column of ``nextron.db.models.Base.metadata`` belongs to exactly one writer, and nothing else is
listed; ``tests/contracts/test_persistence_contracts.py`` fails on any unmapped, doubly mapped or
stale column. A story that adds a table or column adds it here (and to ``pii_registry.py``) in
the same change.
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

# Database modules that write a table on behalf of every owner.
SHARED_WRITERS: Final[tuple[str, ...]] = ("db.eventlog", "db.metering")

TABLES: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "events": {
        "db.eventlog": (
            "id",
            "entity",
            "entity_id",
            "from_status",
            "to_status",
            "cause",
            "cause_ref",
            "actor",
            "at",
        ),
    },
    "settings": {
        "setup": ("id", "key", "value", "updated_at"),
    },
    "jobs": {
        "jobqueue": (
            "id",
            "type",
            "lane",
            "status",
            "priority",
            "payload",
            "idempotency_key",
            "concurrency_key",
            "run_after",
            "attempts",
            "lease_until",
            "last_error",
            "created_at",
            "updated_at",
        ),
    },
    "runner_heartbeat": {
        "ops": ("id", "pid", "started_at", "beat_at"),
    },
    "alerts": {
        "notify": (
            "id",
            "kind",
            "subject",
            "channel",
            "text",
            "status",
            "provider_message_id",
            "error_code",
            "resent_at",
            "created_at",
            "updated_at",
        ),
    },
    "notifications": {
        "notify": ("id", "kind", "subject", "text", "created_at", "dismissed_at"),
    },
    "api_usage": {
        "db.metering": ("id", "provider", "operation", "sku", "units", "ok", "error_code", "at"),
    },
    "ai_usage": {
        "db.metering": (
            "id",
            "route",
            "route_tuple",
            "served_model",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "iterations",
            "batch",
            "cost_micros",
            "refused",
            "error_code",
            "at",
        ),
    },
}
